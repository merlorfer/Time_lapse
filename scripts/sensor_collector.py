#!/usr/bin/env python3
"""
sensor_collector.py – Multi-source Zigbee sensor CSV collector.

Polls every active source over loopback HTTP (GET /api/devices) and writes a
CSV row whenever a sensor's value actually changed:
  - "UART"          -> esp32_proxy.py on 127.0.0.1:8083 (only if serial_enabled)
  - each WiFi unit  -> its transparent forward on 127.0.0.1:<unit port>
No serial/BLE code lives here. Config is re-read from timelapse_config.json on
every tick, so unit/sensor setting changes need no restart.

The row timestamp is the device's own sensor.last_update (epoch seconds), i.e.
when the value changed on the device, not when this poll happened.
Files: /tmp/sensor_data/<date>/<Source>-<device>.csv
"""

import csv
import http.client
import json
import os
import re
import time

TIMELAPSE_CONFIG = "/home/orangepi/timelapse/timelapse_config.json"
SENSOR_DATA_DIR  = "/tmp/sensor_data"
STATE_FILE       = "/tmp/sensor_collector_state.json"
UART_PORT        = 8083
UART_SOURCE_KEY  = "UART"
POLL_TICK_SEC    = 15
FETCH_TIMEOUT    = 30.0  # must exceed wifigw_forward.py UPSTREAM_TIMEOUT (20s)
CSV_FIELDS       = ["timestamp", "temperature", "humidity",
                    "water_level", "lower_active", "upper_active",
                    "valid", "error"]

_last_attempt: dict = {}   # "<source>|<ieee>" -> monotonic time of last check
_warned: set = set()       # sources we already logged as unreachable
_state: dict = {}          # "<source>|<ieee>" -> {"last_update": int, "reading": dict}


def load_config() -> dict:
    try:
        with open(TIMELAPSE_CONFIG) as f:
            return json.load(f)
    except Exception:
        return {}


def load_state():
    global _state
    try:
        with open(STATE_FILE) as f:
            _state = json.load(f)
    except Exception:
        _state = {}


def save_state():
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(_state, f)
        os.replace(tmp, STATE_FILE)
    except Exception as exc:
        print(f"[collector] state save failed: {exc}")


def active_sources(cfg: dict) -> list:
    sources = []
    if cfg.get("serial_enabled", True):
        sources.append((UART_SOURCE_KEY, UART_PORT))
    for u in cfg.get("units", []):
        sources.append((u["name"], u["port"]))
    return sources


def fetch_devices(port: int):
    """GET /api/devices on a local port; None on any failure."""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=FETCH_TIMEOUT)
        try:
            conn.request("GET", "/api/devices")
            resp = conn.getresponse()
            data = resp.read()
        finally:
            conn.close()
        if resp.status != 200:
            return None
        return json.loads(data).get("devices", [])
    except Exception:
        return None


def extract_reading(dev: dict) -> dict:
    sensor = dev.get("sensor", {})
    dtype  = dev.get("device_type", "")
    err    = dev.get("error", {})
    row    = {}
    if "temperature" in dtype or "temperature" in sensor:
        row["temperature"] = sensor.get("current_value", "")
        row["humidity"]    = sensor.get("humidity", "")
    elif "humidity" in dtype:
        row["humidity"] = sensor.get("current_value", "")
    elif "water_level" in dtype or "leak" in dtype:
        row["water_level"]  = sensor.get("current_value", "")
        row["lower_active"] = int(bool(sensor.get("lower_active")))
        row["upper_active"] = int(bool(sensor.get("upper_active")))
        row["valid"]        = int(bool(sensor.get("valid")))
    row["error"] = err.get("message", "") if err else ""
    return row


def write_row(source: str, dev: dict, reading: dict, last_update: int):
    # The firmware's time(NULL) is the RTC wall clock as set via /api/rtc/set,
    # i.e. LOCAL time stored as if it were UTC -- so decode it with gmtime to
    # get back the same wall-clock time (localtime would add the UTC offset).
    st = time.gmtime(last_update)
    date = time.strftime("%Y-%m-%d", st)
    ts   = time.strftime("%Y-%m-%d %H:%M:%S", st)
    fs_source = re.sub(r"[^\w\-]", "_", source)
    name = (dev.get("custom_name") or dev["ieee_addr"].replace("0x", "")).replace("/", "_")
    day_dir = os.path.join(SENSOR_DATA_DIR, date)
    os.makedirs(day_dir, exist_ok=True)
    filepath = os.path.join(day_dir, f"{fs_source}-{name}.csv")
    row = {"timestamp": ts}
    row.update(reading)
    write_header = not os.path.isfile(filepath)
    with open(filepath, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in CSV_FIELDS})


def poll_source(source: str, port: int, source_cfg: dict):
    devices = fetch_devices(port)
    if devices is None:
        if source not in _warned:
            print(f"[collector] {source} (:{port}) not reachable – skipping")
            _warned.add(source)
        return
    _warned.discard(source)

    now = time.monotonic()
    changed = False
    for dev in devices:
        ieee = dev.get("ieee_addr")
        dcfg = source_cfg.get(ieee)
        if not ieee or not dcfg or not dcfg.get("enabled"):
            continue
        # A multi-cluster sensor (e.g. temperature + humidity) shares one ieee,
        # so the per-device state/attempt key must include endpoint + type.
        key = f"{source}|{ieee}|{dev.get('endpoint', 1)}|{dev.get('device_type', '')}"
        interval = max(1, int(dcfg.get("interval_min", 60))) * 60
        if now - _last_attempt.get(key, -interval) < interval:
            continue
        _last_attempt[key] = now

        last_update = int(dev.get("sensor", {}).get("last_update", 0) or 0)
        if last_update == 0:
            continue  # never reported yet
        prev = _state.get(key)
        if prev and last_update <= prev.get("last_update", 0):
            continue  # no new report since last check
        reading = extract_reading(dev)
        measure_keys = [k for k in reading if k != "error"]
        if prev and all(str(reading.get(k, "")) == str(prev["reading"].get(k, ""))
                        for k in measure_keys):
            # Report arrived but value is unchanged: remember it, write nothing.
            prev["last_update"] = last_update
            changed = True
            continue
        write_row(source, dev, reading, last_update)
        _state[key] = {"last_update": last_update, "reading": reading}
        changed = True
        print(f"[collector] {source}: saved {dev.get('custom_name', ieee)}")
    if changed:
        save_state()


def main():
    load_state()
    print("[collector] started")
    while True:
        cfg = load_config()
        sensor_sources = cfg.get("sensor_sources", {})
        for source, port in active_sources(cfg):
            try:
                poll_source(source, port, sensor_sources.get(source, {}))
            except Exception as exc:
                print(f"[collector] {source}: unexpected error: {exc}")
        time.sleep(POLL_TICK_SEC)


if __name__ == "__main__":
    main()

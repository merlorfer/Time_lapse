#!/usr/bin/env python3
"""
esp32_proxy.py – ESP32C6 BLE Proxy
Serves the ESP32C6 web UI on :8082 and proxies /api/* calls via BLE GATT.

Requires: pip3 install bleak
"""

import asyncio
import collections
import csv
import json
import os
import re
import signal
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from bleak import BleakClient, BleakScanner

# =============================================================================
# Configuration
# =============================================================================

BLE_DEVICE_NAME   = "ESP32C6_Gateway"
BLE_REQ_UUID      = "0000fff1-0000-1000-8000-00805f9b34fb"   # CMD_REQ (Write)
BLE_RES_UUID      = "0000fff2-0000-1000-8000-00805f9b34fb"   # CMD_RES (Notify)
WEB_DIR           = "/home/orangepi/esp32/web"
HTTP_PORT         = 8083
SERIAL_PORT       = "/dev/ttyACM0"
SERIAL_BAUD       = 115200
SERIAL_BUF_LINES  = 500

SENSOR_CONFIG_FILE = "/home/orangepi/esp32/sensor_config.json"
SENSOR_DATA_DIR    = "/tmp/sensor_data"
SERIAL_LOG_FILE    = "/tmp/esp32_serial.log"
CSV_FIELDS         = ["timestamp", "temperature", "humidity",
                      "water_level", "lower_active", "upper_active",
                      "valid", "error"]

# =============================================================================
# Serial log reader  (tails the screen log file – screen holds the port)
# =============================================================================

_serial_buf: collections.deque = collections.deque(maxlen=SERIAL_BUF_LINES)
_serial_lock = threading.Lock()
_serial_available = False
# ANSI escape sequence filter
_ANSI = re.compile(rb'\x1b\[[0-9;]*[A-Za-z]|\x1b[()][AB012]|\r')


def _serial_reader_thread():
    global _serial_available
    while True:
        if not os.path.exists(SERIAL_LOG_FILE):
            _serial_available = False
            time.sleep(3)
            continue
        print(f"[SERIAL] Tailing {SERIAL_LOG_FILE}")
        try:
            with open(SERIAL_LOG_FILE, "rb") as f:
                # Start near the end so we show recent content on restart
                f.seek(max(0, os.path.getsize(SERIAL_LOG_FILE) - 8192))
                _serial_available = True
                buf = b""
                while True:
                    chunk = f.read(512)
                    if chunk:
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            text = _ANSI.sub(b"", line).decode("utf-8", errors="replace").strip()
                            if text:
                                with _serial_lock:
                                    _serial_buf.append({"t": time.strftime("%H:%M:%S"), "msg": text})
                    else:
                        # Check file still exists (screen may restart)
                        if not os.path.exists(SERIAL_LOG_FILE):
                            _serial_available = False
                            break
                        time.sleep(0.2)
        except Exception as exc:
            _serial_available = False
            print(f"[SERIAL] {exc} – retry in 3 s")
            time.sleep(3)


# =============================================================================
# Shared async state  (all writes happen inside the BLE event loop)
# =============================================================================

_loop:   asyncio.AbstractEventLoop | None = None
_client: BleakClient | None = None
_lock:   asyncio.Lock | None = None
_ready   = threading.Event()   # set once the lock is initialised
connected = False               # bool reads are atomic in CPython

# =============================================================================
# BLE internals
# =============================================================================

async def _send(cmd: str, params: dict) -> dict:
    """Send one BLE command and return the parsed JSON response."""
    if not connected or _client is None or not _client.is_connected:
        raise ConnectionError("BLE not connected")

    payload = json.dumps({"cmd": cmd, "params": params}).encode()
    chunks: dict[int, str] = {}
    done = asyncio.Event()

    def _notify(_sender, data: bytearray):
        text = data.decode("utf-8", errors="replace")
        print(f"[BLE] notify cmd={cmd} len={len(text)} data={repr(text[:60])}")
        m = re.match(r"^\[(\d+)/(\d+)\](.*)", text, re.DOTALL)
        if m:
            idx  = int(m.group(1))
            tot  = int(m.group(2))
            chunks[idx] = m.group(3)
            print(f"[BLE]   chunk {idx}/{tot}")
            if len(chunks) == tot:
                done.set()
        else:
            chunks[0] = text
            done.set()

    await _client.start_notify(BLE_RES_UUID, _notify)
    try:
        await _client.write_gatt_char(BLE_REQ_UUID, payload, response=True)
        await asyncio.wait_for(done.wait(), timeout=15.0)
    finally:
        try:
            await _client.stop_notify(BLE_RES_UUID)
        except Exception:
            pass

    full = "".join(chunks[i] for i in sorted(chunks.keys()))

    # Try direct parse first; if the firmware prepends {"status":"ok"} before
    # the actual payload, or BlueZ concatenates two notifications, parse each
    # JSON object in sequence and return the last one.
    try:
        return json.loads(full)
    except json.JSONDecodeError as exc:
        if "Extra data" not in str(exc):
            raise
        decoder = json.JSONDecoder()
        result, idx = None, 0
        while idx < len(full):
            while idx < len(full) and full[idx] in " \t\n\r":
                idx += 1
            if idx >= len(full):
                break
            try:
                obj, idx = decoder.raw_decode(full, idx)
                result = obj
            except json.JSONDecodeError:
                break
        if result is not None:
            print(f"[BLE] multi-JSON response for '{cmd}', using last object")
            return result
        raise


async def _ble_init():
    """One-shot coroutine: just initialise the lock and signal ready."""
    global _lock
    _lock = asyncio.Lock()
    _ready.set()


async def ble_connect_async():
    """Connect to ESP32C6_Gateway (called from HTTP handler via run_coroutine_threadsafe)."""
    global _client, connected
    if connected and _client and _client.is_connected:
        return {"ok": True, "msg": "Already connected"}
    print(f"[BLE] Scanning for {BLE_DEVICE_NAME}…")
    dev = await BleakScanner.find_device_by_name(BLE_DEVICE_NAME, timeout=10.0)
    if dev is None:
        raise ConnectionError("Device not found")
    _client = BleakClient(dev, disconnected_callback=_on_disc)
    await _client.connect()
    connected = True
    print(f"[BLE] Connected: {dev.address}")
    return {"ok": True, "address": dev.address}


async def ble_disconnect_async():
    """Disconnect from ESP32C6_Gateway."""
    global connected
    if _client and _client.is_connected:
        await _client.disconnect()
    connected = False
    print("[BLE] Disconnected by user")
    return {"ok": True}


def _on_disc(_c):
    global connected
    connected = False
    print("[BLE] Disconnected")


def _start_ble_thread():
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop.create_task(_ble_init())
    _loop.run_forever()


# =============================================================================
# Sensor config
# =============================================================================

_sensor_config: dict = {}       # {ieee_addr: {enabled, interval_min}}
_sensor_runtime: dict = {}      # {ieee_addr: {consecutive_failures, suspended, last_ok}}
_sensor_cfg_lock = threading.Lock()


def _load_sensor_config():
    global _sensor_config
    try:
        with open(SENSOR_CONFIG_FILE) as f:
            _sensor_config = json.load(f)
    except FileNotFoundError:
        _sensor_config = {}
    except Exception as exc:
        print(f"[SENSOR] Config load error: {exc}")
        _sensor_config = {}


def _save_sensor_config():
    try:
        with open(SENSOR_CONFIG_FILE, "w") as f:
            json.dump(_sensor_config, f, indent=2)
    except Exception as exc:
        print(f"[SENSOR] Config save error: {exc}")


def _get_runtime(ieee: str) -> dict:
    if ieee not in _sensor_runtime:
        _sensor_runtime[ieee] = {"consecutive_failures": 0, "suspended": False, "last_ok": None}
    return _sensor_runtime[ieee]


# =============================================================================
# Sensor data collection
# =============================================================================

def _ble_connect_sync(timeout: int = 15) -> bool:
    """Connect via BLE from a regular thread. Returns True on success."""
    try:
        result = asyncio.run_coroutine_threadsafe(
            ble_connect_async(), _loop).result(timeout=timeout)
        return result.get("ok", False)
    except Exception as exc:
        print(f"[SENSOR] BLE connect failed: {exc}")
        return False


def _ble_disconnect_sync():
    try:
        asyncio.run_coroutine_threadsafe(
            ble_disconnect_async(), _loop).result(timeout=5)
    except Exception:
        pass


def _extract_reading(dev: dict) -> dict:
    sensor = dev.get("sensor", {})
    dtype  = dev.get("device_type", "")
    err    = dev.get("error", {})
    row    = {}
    if "temperature" in dtype or "temperature" in sensor:
        row["temperature"] = sensor.get("current_value", "")
        row["humidity"]    = sensor.get("humidity", "")
    elif "water_level" in dtype:
        row["water_level"]  = sensor.get("current_value", "")
        row["lower_active"] = int(bool(sensor.get("lower_active")))
        row["upper_active"] = int(bool(sensor.get("upper_active")))
        row["valid"]        = int(bool(sensor.get("valid")))
    row["error"] = err.get("message", "") if err else ""
    return row


def _save_reading(dev: dict, ts: str, date: str):
    name = (dev.get("custom_name") or dev["ieee_addr"].replace("0x", "")).replace("/", "_")
    day_dir = os.path.join(SENSOR_DATA_DIR, date)
    os.makedirs(day_dir, exist_ok=True)
    filepath = os.path.join(day_dir, f"{name}.csv")
    row = {"timestamp": ts}
    row.update(_extract_reading(dev))
    write_header = not os.path.isfile(filepath)
    with open(filepath, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in CSV_FIELDS})


def _collect_now(to_collect: list):
    """BLE connect → get_devices → save readings → disconnect. Max 2 attempts."""
    success = False
    for attempt in range(2):
        if _ble_connect_sync():
            success = True
            break
        if attempt == 0:
            print("[SENSOR] Attempt 1 failed, retrying in 10 s…")
            time.sleep(10)

    if not success:
        with _sensor_cfg_lock:
            for ieee in to_collect:
                rt = _get_runtime(ieee)
                rt["consecutive_failures"] += 1
                if rt["consecutive_failures"] >= 3:
                    rt["suspended"] = True
                    print(f"[SENSOR] {ieee} suspended after 3 consecutive failures")
        return

    try:
        data    = ble_call("get_devices", {})
        devices = data.get("devices", [])
        ts      = time.strftime("%Y-%m-%d %H:%M:%S")
        date    = time.strftime("%Y-%m-%d")
        for dev in devices:
            ieee = dev["ieee_addr"]
            if ieee not in to_collect:
                continue
            _save_reading(dev, ts, date)
            print(f"[SENSOR] Saved reading for {dev.get('custom_name', ieee)}")
            with _sensor_cfg_lock:
                rt = _get_runtime(ieee)
                rt["consecutive_failures"] = 0
                rt["suspended"]            = False
                rt["last_ok"]              = ts
    except Exception as exc:
        print(f"[SENSOR] Collection error: {exc}")
    finally:
        _ble_disconnect_sync()


def _sensor_scheduler():
    """Wakes every minute, collects data for enabled sensors whose interval is due."""
    while True:
        now_min = time.localtime().tm_hour * 60 + time.localtime().tm_min
        to_collect = []
        with _sensor_cfg_lock:
            for ieee, cfg in _sensor_config.items():
                if not cfg.get("enabled"):
                    continue
                rt = _get_runtime(ieee)
                if rt["suspended"]:
                    continue
                interval = max(1, cfg.get("interval_min", 60))
                if now_min % interval == 0:
                    to_collect.append(ieee)
        if to_collect:
            threading.Thread(target=_collect_now, args=(to_collect,), daemon=True).start()
        # Sleep until next minute boundary
        time.sleep(60 - time.localtime().tm_sec + 1)


# =============================================================================
# Sync BLE wrapper (called from HTTP handler threads)
# =============================================================================

def ble_call(cmd: str, params: dict) -> dict:
    async def _locked():
        async with _lock:
            return await _send(cmd, params)
    return asyncio.run_coroutine_threadsafe(_locked(), _loop).result(timeout=20)

def ble_call(cmd: str, params: dict) -> dict:
    async def _locked():
        async with _lock:
            return await _send(cmd, params)
    return asyncio.run_coroutine_threadsafe(_locked(), _loop).result(timeout=20)


# =============================================================================
# HTTP → BLE command mapping  (mirrors script.js endpointToCommand)
# =============================================================================

def endpoint_to_cmd(method: str, path: str, body: dict):
    """Returns (cmd, params) tuple or (None, None) if unknown."""

    if path == "/api/ble-status":
        # Internal proxy endpoint – handled before reaching here
        return None, None

    if path == "/api/status":
        return "get_status", {}

    if path == "/api/devices":
        return "get_devices", {}

    if path == "/api/rtc/set":
        parts = re.split(r"[ \-:]", body.get("datetime", ""))
        if len(parts) >= 6:
            keys = ("year", "month", "day", "hour", "minute", "second")
            return "set_rtc", dict(zip(keys, (int(p) for p in parts[:6])))

    m = re.match(r"^/api/devices/(0x[0-9A-Fa-f]+)/config$", path)
    if m:
        return "set_device_config", {"ieee_addr": m.group(1), **body}

    m = re.match(r"^/api/devices/(0x[0-9A-Fa-f]+)$", path)
    if m:
        ieee = m.group(1)
        if method == "DELETE":
            return "delete_device", {"ieee_addr": ieee}
        if "cmd" in body:
            return "control_device", body

    if path == "/api/zigbee/permit-join":
        return "permit_join", body or {"duration": 60}

    if path == "/api/config":
        if method == "POST":
            return "set_global_settings", body
        return "get_global_settings", {}

    if path == "/api/reboot":
        return "reboot", {}

    if path == "/api/wifi/shutdown":
        return "switch_mode", {}

    if path == "/api/factory-reset":
        return "factory_reset", {}

    if path == "/api/rules":
        if method == "POST":
            return "set_rules", body
        return "get_rules", {}

    if path == "/api/rules/timers":
        return "get_rules_timers", {}

    if path == "/api/rules/var":
        return "set_rules_var", body

    if path == "/api/rules/varconfig":
        return "set_rules_varconfig", body

    if path == "/api/rules/reset":
        return "reset_rules", {}

    if path == "/api/logs/live":
        return "get_logs_live", {"lines": 50}

    return None, None


# =============================================================================
# HTTP handler
# =============================================================================

MIME = {
    ".html":        "text/html; charset=utf-8",
    ".js":          "application/javascript",
    ".css":         "text/css",
    ".json":        "application/json",
    ".png":         "image/png",
    ".ico":         "image/x-icon",
    ".webmanifest": "application/manifest+json",
}


class Handler(BaseHTTPRequestHandler):

    def log_message(self, *_):
        pass  # suppress default per-request logging

    # ── helpers ──────────────────────────────────────────────────────────────

    def _send_json(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        if n:
            try:
                return json.loads(self.rfile.read(n))
            except Exception:
                pass
        return {}

    # ── /api/* ────────────────────────────────────────────────────────────────

    def _handle_api(self):
        body = self._read_body()
        path = urlparse(self.path).path
        cmd, params = endpoint_to_cmd(self.command, path, body)

        if cmd is None:
            self._send_json(404, {"error": "unknown endpoint", "path": path})
            return

        if not connected:
            self._send_json(503, {"error": "BLE not connected", "status": "disconnected"})
            return

        try:
            self._send_json(200, ble_call(cmd, params))
        except ConnectionError as exc:
            self._send_json(503, {"error": str(exc)})
        except TimeoutError:
            self._send_json(504, {"error": "BLE command timeout"})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    # ── static files ──────────────────────────────────────────────────────────

    def _serve_static(self):
        path = urlparse(self.path).path or "/"
        if path == "/":
            path = "/index.html"
        fp = os.path.realpath(os.path.join(WEB_DIR, path.lstrip("/")))
        web_root = os.path.realpath(WEB_DIR)
        if not fp.startswith(web_root) or not os.path.isfile(fp):
            self.send_response(404)
            self.end_headers()
            return
        with open(fp, "rb") as f:
            data = f.read()
        ext = os.path.splitext(fp)[1].lower()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── HTTP verbs ────────────────────────────────────────────────────────────

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/ble-status":
            self._send_json(200, {"connected": connected})
        elif self.path == "/api/sensor-config":
            with _sensor_cfg_lock:
                cfg_copy = dict(_sensor_config)
            runtime = {ieee: {"suspended": _get_runtime(ieee)["suspended"],
                               "consecutive_failures": _get_runtime(ieee)["consecutive_failures"],
                               "last_ok": _get_runtime(ieee)["last_ok"]}
                       for ieee in cfg_copy}
            self._send_json(200, {"config": cfg_copy, "runtime": runtime})
        elif self.path == "/api/sensor-status":
            with _sensor_cfg_lock:
                status = {ieee: _get_runtime(ieee) for ieee in _sensor_config}
            self._send_json(200, status)
        elif self.path.startswith("/api/serial-logs"):
            qs = parse_qs(urlparse(self.path).query)
            since = int(qs.get("since", [0])[0])
            with _serial_lock:
                lines = list(_serial_buf)
            # 'since' = number of lines already seen by the client
            new_lines = lines[since:]
            self._send_json(200, {
                "available": _serial_available,
                "total": len(lines),
                "lines": new_lines,
            })
        elif self.path.startswith("/api/"):
            self._handle_api()
        else:
            self._serve_static()

    def do_POST(self):
        if self.path == "/api/ble-connect":
            try:
                result = asyncio.run_coroutine_threadsafe(
                    ble_connect_async(), _loop).result(timeout=15)
                self._send_json(200, result)
            except Exception as exc:
                self._send_json(503, {"ok": False, "msg": str(exc)})
        elif self.path == "/api/ble-disconnect":
            try:
                result = asyncio.run_coroutine_threadsafe(
                    ble_disconnect_async(), _loop).result(timeout=5)
                self._send_json(200, result)
            except Exception as exc:
                self._send_json(500, {"ok": False, "msg": str(exc)})
        elif self.path == "/api/sensor-config":
            body = self._read_body()
            with _sensor_cfg_lock:
                for ieee, cfg in body.items():
                    _sensor_config[ieee] = {
                        "enabled":      bool(cfg.get("enabled", False)),
                        "interval_min": max(1, int(cfg.get("interval_min", 60))),
                    }
                    # Reset suspension when config is updated
                    rt = _get_runtime(ieee)
                    rt["suspended"]            = False
                    rt["consecutive_failures"] = 0
                _save_sensor_config()
            self._send_json(200, {"ok": True})
        elif self.path.startswith("/api/"):
            self._handle_api()
        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            self._handle_api()
        else:
            self.send_response(404)
            self.end_headers()


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    signal.signal(signal.SIGHUP, signal.SIG_IGN)

    _load_sensor_config()
    threading.Thread(target=_start_ble_thread, daemon=True).start()
    threading.Thread(target=_serial_reader_thread, daemon=True).start()
    threading.Thread(target=_sensor_scheduler, daemon=True).start()
    _ready.wait()

    print(f"[HTTP] ESP32C6 proxy on :{HTTP_PORT}")
    print(f"[HTTP] Web files: {WEB_DIR}")

    class ReusableHTTPServer(HTTPServer):
        allow_reuse_address = True

    try:
        ReusableHTTPServer(("0.0.0.0", HTTP_PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        pass

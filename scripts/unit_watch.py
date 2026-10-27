#!/usr/bin/env python3
"""
unit_watch.py – watches the CLCode01 / WiFiGateway units and keeps an error
journal (unit_errors.py) + queues an email for every NEW error.

Per source (every active unit, and "UART" when serial_enabled), once a minute:
  1. GET /api/devices  -> the firmware's own device errors
       "Sensor timeout"   a sensor did not report within its configured timeout
       "Nem valaszol"     a device did not answer a command (3 retries)
       "Parancs sikertelen" / anything else
     Failing to get the list for `unreachable_min` minutes = "unit unreachable".
  2. the unit's serial log (gateway: /api/gw/serial-log, UART: /api/serial-logs)
       E-level lines (except the ignore list)
       CLCode01 reboot (tick counter going backwards / "created (reset: ...)")

Dedup rule (see unit_errors.py): an error that is still unacknowledged never
produces another entry or email; only after acknowledging can it come back.

Optional config in timelapse_config.json:
  "unit_watch": {"unreachable_min": 5, "ignore": ["substring of an E line", ...]}
"""

import http.client
import json
import os
import re
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alert_lib
import unit_errors

TIMELAPSE_CONFIG = "/home/orangepi/timelapse/timelapse_config.json"
STATE_FILE       = os.environ.get("UNIT_WATCH_STATE", "/home/orangepi/timelapse/logs/unit_watch_state.json")
UART_PORT        = 8083
UART_SOURCE_KEY  = "UART"
POLL_SEC         = 60
FETCH_TIMEOUT    = 30.0
SEEN_MAX         = 600
WRAP_GUARD       = 4_000_000_000     # ESP tick (ms) wraps at 2^32: not a reboot
REBOOT_SLACK     = 2000              # ms the tick may "go back" before it counts as a reboot
DEFAULT_UNREACHABLE_MIN = 5
# Known repeating E lines that are not unit faults (BLE client came and went).
DEFAULT_IGNORE = ["Failed to send notification", "Failed to allocate mbuf for notification"]

_LINE_RE  = re.compile(r"^\s*([EWIDV]) \((\d+)\) ([^:]+): (.*)$")
_ANSI_RE  = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_RESET_RE = re.compile(r"created \(reset: (.+)\)\s*$")

KIND_EVENT = {
    "sensor_timeout":     ("unit_sensor_timeout", "szenzor nem jelentkezik"),
    "device_no_response": ("unit_device_error",   "eszköz nem válaszol"),
    "command_failed":     ("unit_device_error",   "parancs sikertelen"),
    "device_error":       ("unit_device_error",   "eszközhiba"),
    "log_error":          ("unit_log_error",      "hiba a soros naplóban"),
    "reboot":             ("unit_reboot",         "a CLCode01 újraindult"),
    "unreachable":        ("unit_unreachable",    "az egység nem érhető el"),
}
DEVICE_KINDS = {"sensor_timeout", "device_no_response", "command_failed", "device_error"}
MESSAGE_KIND = {"Sensor timeout": "sensor_timeout", "Nem valaszol": "device_no_response",
                "Parancs sikertelen": "command_failed"}


def load_config() -> dict:
    try:
        with open(TIMELAPSE_CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def active_sources(cfg: dict) -> list:
    sources = []
    if cfg.get("serial_enabled", True):
        sources.append((UART_SOURCE_KEY, UART_PORT))
    for u in cfg.get("units", []):
        sources.append((u["name"], u["port"]))
    return sources


def http_get_json(port: int, path: str):
    """Parsed JSON, or None on any failure."""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=FETCH_TIMEOUT)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            data = resp.read()
        finally:
            conn.close()
        if resp.status != 200:
            return None
        return json.loads(data)
    except Exception:
        return None


def fetch_log_lines(source: str, port: int):
    """List of raw log lines, or None when unavailable."""
    if source == UART_SOURCE_KEY:
        d = http_get_json(port, "/api/serial-logs?since=0")
        if not d or not d.get("available"):
            return None
        return [str(x.get("msg", "")) for x in d.get("lines", [])]
    d = http_get_json(port, "/api/gw/serial-log")
    if not d:
        return None
    return [str(x) for x in d.get("lines", [])]


# ── state (which log lines were already looked at) ──────────────────────────

def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict):
    tmp = STATE_FILE + ".tmp"
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)
    except OSError as exc:
        print(f"[unit_watch] state save failed: {exc}")


def _crc(raw: str) -> int:
    return zlib.crc32(raw.encode("utf-8", errors="replace"))


def normalize(msg: str) -> str:
    """Numbers and addresses out, so the same fault with other values is the same fault."""
    return re.sub(r"0x[0-9a-fA-F]+|\b\d+(?:\.\d+)?\b", "#", msg)


# ── analysis (pure functions, no network) ────────────────────────────────────

def process_log(source: str, raw_lines: list, st: dict, ignore: list) -> list:
    """Events from the lines not seen before. Mutates st (the source's state)."""
    parsed = []
    for raw in raw_lines:
        raw = _ANSI_RE.sub("", raw).rstrip()
        if not raw.strip():
            continue
        m = _LINE_RE.match(raw)
        parsed.append((raw, m.group(1), int(m.group(2)), m.group(3).strip(), m.group(4)) if m
                      else (raw, None, None, None, raw))

    if "seen" not in st:                       # first look at this source: baseline only
        st["seen"] = [_crc(p[0]) for p in parsed][-SEEN_MAX:]
        ticks = [p[2] for p in parsed if p[2] is not None]
        st["last_tick"] = ticks[-1] if ticks else 0
        return []

    seen = set(st["seen"])
    new_idx = [i for i, p in enumerate(parsed) if _crc(p[0]) not in seen]
    if not new_idx:
        return []
    st["seen"] = (st["seen"] + [_crc(parsed[i][0]) for i in new_idx])[-SEEN_MAX:]

    events = []
    prev = st.get("last_tick", 0)
    reboot_at, reasons = [], []
    for i in new_idx:
        raw, level, tick, tag, msg = parsed[i]
        if tick is not None:
            if prev and tick < prev - REBOOT_SLACK and prev < WRAP_GUARD:
                reboot_at.append(i)
            prev = tick
        m = _RESET_RE.search(raw)
        if m:
            reasons.append(m.group(1))
    st["last_tick"] = prev

    if reboot_at or reasons:
        i0 = reboot_at[0] if reboot_at else new_idx[0]
        reason = reasons[-1] if reasons else ""
        events.append({
            "kind": "reboot", "key": f"reboot|{source}", "device": "",
            "message": "A CLCode01 újraindult" + (f" (ok: {reason})" if reason else ""),
            "details": [p[0] for p in parsed[max(0, i0 - 1):i0 + 3]],
        })

    for i in new_idx:
        raw, level, tick, tag, msg = parsed[i]
        if level != "E" or any(s and s in raw for s in ignore):
            continue
        events.append({
            "kind": "log_error", "key": f"log|{source}|{tag}|{normalize(msg)}", "device": "",
            "message": f"{tag}: {msg}",
            "details": [p[0] for p in parsed[max(0, i - 3):i + 1]],
        })
    return events


def device_events(source: str, devices: list) -> list:
    events = []
    for dev in devices:
        err = dev.get("error")
        if not err:
            continue
        msg = err.get("message", "") if isinstance(err, dict) else str(err)
        ieee = dev.get("ieee_addr", "")
        name = dev.get("custom_name") or ieee
        kind = MESSAGE_KIND.get(msg, "device_error")
        events.append({
            "kind": kind,
            "key": f"dev|{source}|{ieee}|{dev.get('endpoint', 1)}|{dev.get('device_type', '')}|{msg}",
            "device": name, "message": msg,
            "details": [f"IEEE: {ieee}", f"Végpont: {dev.get('endpoint', 1)}",
                        f"Típus: {dev.get('device_type', '')}",
                        f"A firmware szerint ekkor: {err.get('timestamp', '?') if isinstance(err, dict) else '?'}"],
        })
    return events


# ── reporting ────────────────────────────────────────────────────────────────

def emit(source: str, ev: dict):
    entry, is_new = unit_errors.report(ev["key"], source, ev["device"], ev["kind"],
                                       ev["message"], ev["details"],
                                       state_like=ev["kind"] in DEVICE_KINDS)
    if not is_new:
        return
    event_id, short = KIND_EVENT[ev["kind"]]
    what = f"{ev['device']} – {ev['message']}" if ev["device"] else ev["message"]
    print(f"[unit_watch] {source}: {what}")
    body = [f"Egység: {source}"]
    if ev["device"]:
        body.append(f"Eszköz: {ev['device']}")
    body.append(f"Hiba: {ev['message']}")
    body.append(f"Időpont: {alert_lib.fmt_ts(entry['first_ts'])}")
    if ev["details"]:
        body += ["", "Részletek:"] + [f"  {d}" for d in ev["details"]]
    body += ["", "Ugyanez a hiba nem kerül újra elküldésre, amíg a webes felület Hibanapló",
             "részében nem nyugtázod. Rendszer: " + os.uname().nodename]
    try:
        alert_lib.enqueue(event_id, f"{source}: {short}", "\n".join(body))
    except Exception as exc:
        print(f"[unit_watch] enqueue failed: {exc}")


_fail_since: dict = {}       # source -> monotonic time of the first failed device poll
_unreach_reported: set = set()


def watch_source(source: str, port: int, wcfg: dict, state: dict):
    now = time.monotonic()
    unreachable_s = max(1, int(wcfg.get("unreachable_min", DEFAULT_UNREACHABLE_MIN))) * 60
    ignore = list(wcfg.get("ignore", DEFAULT_IGNORE))

    data = http_get_json(port, "/api/devices")
    devices = data.get("devices") if isinstance(data, dict) else None
    if devices is None:
        first = _fail_since.setdefault(source, now)
        if now - first >= unreachable_s and source not in _unreach_reported:
            _unreach_reported.add(source)
            emit(source, {"kind": "unreachable", "key": f"unreach|{source}", "device": "",
                          "message": f"Nem érhető el (a lekérdezés {int((now - first) // 60)} perce sikertelen)",
                          "details": [f"Helyi port: 127.0.0.1:{port}"]})
    else:
        if source in _fail_since or source in _unreach_reported:
            _fail_since.pop(source, None)
            _unreach_reported.discard(source)
            unit_errors.mark_cleared(source, {"unreachable"}, set())
        events = device_events(source, devices)
        for ev in events:
            emit(source, ev)
        unit_errors.mark_cleared(source, DEVICE_KINDS, {ev["key"] for ev in events})

    lines = fetch_log_lines(source, port)
    if lines is not None:
        src_state = state.setdefault(source, {})
        before = json.dumps(src_state, sort_keys=True)
        for ev in process_log(source, lines, src_state, ignore):
            emit(source, ev)
        if json.dumps(src_state, sort_keys=True) != before:
            save_state(state)


def main():
    state = load_state()
    print("[unit_watch] started")
    while True:
        cfg = load_config()
        wcfg = cfg.get("unit_watch") or {}
        for source, port in active_sources(cfg):
            try:
                watch_source(source, port, wcfg, state)
            except Exception as exc:
                print(f"[unit_watch] {source}: unexpected error: {exc}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()

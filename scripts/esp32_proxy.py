#!/usr/bin/env python3
"""
esp32_proxy.py – ESP32C6 Proxy (USB serial preferred, BLE fallback)
Serves the ESP32C6 web UI on :8083 and proxies /api/* calls via USB serial
(screen session) or BLE GATT when USB is not available.

Requires: pip3 install bleak
"""

import asyncio
import collections
import json
import os
import re
import signal
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
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
SCREEN_SESSION    = "esp32serial"
SERIAL_CMD_TIMEOUT = 15.0  # seconds to wait for >>> response

SERIAL_LOG_FILE    = "/tmp/esp32_serial.log"

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
# USB serial command interface  (via screen session, >>> response protocol)
# =============================================================================

_serial_cmd_lock = threading.Lock()   # only one command at a time


def _get_screen_id() -> str | None:
    """Return the full screen session ID (e.g. '289778.esp32serial') or None."""
    r = subprocess.run(["screen", "-ls"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        line = line.strip()
        if SCREEN_SESSION in line and ("(Detached)" in line or "(Attached)" in line):
            return line.split()[0]
    return None


def _screen_running() -> bool:
    return _get_screen_id() is not None


def serial_call(cmd: str, params: dict, timeout: float = SERIAL_CMD_TIMEOUT) -> dict:
    """Send a JSON command via the screen session and parse the >>> response."""
    session_id = _get_screen_id()
    if not session_id:
        raise ConnectionError("screen session not found")

    payload = json.dumps({"cmd": cmd, "params": params} if params else {"cmd": cmd})

    with _serial_cmd_lock:
        # Mark position in log file before sending
        try:
            log_pos = os.path.getsize(SERIAL_LOG_FILE)
        except OSError:
            log_pos = 0

        # Send command via temp file + readreg/paste (handles large payloads)
        tmp = f"/tmp/_esp32_cmd_{os.getpid()}.txt"
        with open(tmp, "w") as f:
            f.write(payload + "\n")
        subprocess.run(["screen", "-S", session_id, "-p", "0", "-X",
                        "readreg", "p", tmp], capture_output=True)
        subprocess.run(["screen", "-S", session_id, "-p", "0", "-X",
                        "paste", "p"], capture_output=True)
        os.unlink(tmp)
        print(f"[SERIAL] → {payload[:80]}")

        # Wait for >>> response to appear in the log file.
        # screen only flushes its logfile periodically (see esp32-serial.service:
        # "logfile flush 1") and its internal stdio buffer can also spill to disk
        # mid-line for a long response, so at any given poll the last "line" in
        # the file may just be a response that is still being written. Only the
        # portion up to the last completed newline is safe to parse -- treating
        # a not-yet-newline-terminated tail as a finished line is what used to
        # produce spurious "JSON parse error ... column 4094" failures on long
        # responses (e.g. get_devices with many devices).
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with open(SERIAL_LOG_FILE, "rb") as f:
                    f.seek(log_pos)
                    raw_bytes = f.read()
                clean = _ANSI.sub(b"", raw_bytes)
                if clean.endswith(b"\n"):
                    complete = clean
                elif b"\n" in clean:
                    complete = clean.rsplit(b"\n", 1)[0]
                else:
                    complete = b""  # nothing fully written yet
                for line_b in complete.split(b"\n"):
                    line = line_b.decode("utf-8", errors="replace").strip()
                    if line.startswith(">>>") and "{" in line:
                        raw = line[line.index("{"):]
                        try:
                            result = json.loads(raw)
                        except json.JSONDecodeError as je:
                            print(f"[SERIAL] JSON parse error on complete line: {je} | raw={raw[:200]}")
                            continue
                        print(f"[SERIAL] ← {line[:120]}")
                        return result
            except Exception:
                pass
            time.sleep(0.1)

    raise TimeoutError(f"No serial response for cmd={cmd} within {timeout}s")


def device_call(cmd: str, params: dict, timeout: float = SERIAL_CMD_TIMEOUT) -> dict:
    """Send command via serial if available, fall back to BLE."""
    if _serial_available and _screen_running():
        return serial_call(cmd, params, timeout=timeout)
    return ble_call(cmd, params, timeout=timeout)


# =============================================================================
# Chunked config/rules upload (export/import panel)
#
# Mirrors CLCode01/tools/usb_proxy/proxy.py's SerialLink.upload(): a large
# "rules" or "config" payload is sent as upload_begin/upload_chunk*/upload_commit
# instead of one oversized command, so it never has to fit in one serial line.
# 700 chars/chunk stays well under the firmware's serial line buffer.
# =============================================================================

UPLOAD_CHUNK_CHARS = 700


def _upload_commit_timeout(size_bytes: int) -> float:
    """A config import re-creates every device in NVS; give it more time
    the bigger the payload is instead of a single fixed timeout."""
    return max(SERIAL_CMD_TIMEOUT, 5.0 + size_bytes / 2000.0)


def device_upload(target: str, text: str) -> dict:
    size = len(text.encode("utf-8"))
    resp = device_call("upload_begin", {"target": target, "size": size})
    if resp.get("status") != "ok":
        return resp

    for i in range(0, len(text), UPLOAD_CHUNK_CHARS):
        piece = text[i:i + UPLOAD_CHUNK_CHARS]
        resp = device_call("upload_chunk", {"data": piece})
        if resp.get("status") != "ok":
            device_call("upload_abort", {})
            return resp

    return device_call("upload_commit", {}, timeout=_upload_commit_timeout(size))


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
# Sync BLE wrapper (called from HTTP handler threads)
# =============================================================================

def ble_call(cmd: str, params: dict, timeout: float = 20.0) -> dict:
    async def _locked():
        async with _lock:
            return await _send(cmd, params)
    return asyncio.run_coroutine_threadsafe(_locked(), _loop).result(timeout=timeout)


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

    if path == "/api/rules/exec":
        return "exec_rules_cmd", body

    if path == "/api/devices/virtual":
        return "add_virtual_device", body

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

        serial_ok = _serial_available and _screen_running()
        if not serial_ok and not connected:
            self._send_json(503, {"error": "Not connected", "status": "disconnected"})
            return

        try:
            result = device_call(cmd, params)
            # Normalize: serial returns {status:"ok"}, JS expects {ok:true} or {success:true}
            if result.get("status") == "ok" and "ok" not in result and "success" not in result:
                result["ok"] = True
            self._send_json(200, result)
        except ConnectionError as exc:
            self._send_json(503, {"error": str(exc)})
        except TimeoutError:
            self._send_json(504, {"error": "Command timeout"})
        except Exception as exc:
            print(f"[HTTP] ERROR cmd={cmd}: {exc}")
            self._send_json(500, {"error": str(exc)})

    # ── config export/import (USB proxy-only feature, downloads/uploads files) ──

    def _device_available(self) -> bool:
        return (_serial_available and _screen_running()) or connected

    def _download(self, data: bytes, content_type: str, filename: str):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _export_rules(self):
        if not self._device_available():
            self._send_json(503, {"error": "Not connected"})
            return
        try:
            resp = device_call("get_rules", {})
        except (ConnectionError, TimeoutError) as exc:
            self._send_json(504, {"error": str(exc)})
            return
        text = resp.get("text", "") if resp.get("status") == "ok" else ""
        self._download(text.encode("utf-8"), "text/plain; charset=utf-8", "rules.txt")

    def _import_rules(self):
        if not self._device_available():
            self._send_json(503, {"error": "Not connected"})
            return
        text = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8", errors="replace")
        try:
            resp = device_upload("rules", text)
        except (ConnectionError, TimeoutError) as exc:
            self._send_json(504, {"error": str(exc)})
            return
        if resp.get("status") == "ok" and "ok" not in resp:
            resp["ok"] = True
        self._send_json(200, resp)

    def _export_config(self):
        if not self._device_available():
            self._send_json(503, {"error": "Not connected"})
            return
        try:
            resp = device_call("export_config", {})
        except (ConnectionError, TimeoutError) as exc:
            self._send_json(504, {"error": str(exc)})
            return
        data = json.dumps(resp, indent=2, ensure_ascii=False).encode("utf-8")
        self._download(data, "application/json; charset=utf-8", "config.json")

    def _import_config(self):
        if not self._device_available():
            self._send_json(503, {"error": "Not connected"})
            return
        text = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8", errors="replace")
        try:
            resp = device_upload("config", text)
        except (ConnectionError, TimeoutError) as exc:
            self._send_json(504, {"error": str(exc)})
            return
        if resp.get("status") == "ok" and "ok" not in resp:
            resp["ok"] = True
        self._send_json(200, resp)

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
            serial_ok = _serial_available and _screen_running()
            self._send_json(200, {
                "connected": connected,
                "serial": serial_ok,
                "transport": "serial" if serial_ok else ("ble" if connected else "none"),
            })
        elif self.path == "/api/proxy/rules.txt":
            self._export_rules()
        elif self.path == "/api/proxy/config.json":
            self._export_config()
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
        elif self.path == "/api/proxy/rules.txt":
            self._import_rules()
        elif self.path == "/api/proxy/config.json":
            self._import_config()
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

    threading.Thread(target=_start_ble_thread, daemon=True).start()
    threading.Thread(target=_serial_reader_thread, daemon=True).start()
    _ready.wait()

    print(f"[HTTP] ESP32C6 proxy on :{HTTP_PORT}")
    print(f"[HTTP] Web files: {WEB_DIR}")

    class ReusableHTTPServer(ThreadingMixIn, HTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    try:
        ReusableHTTPServer(("0.0.0.0", HTTP_PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        pass

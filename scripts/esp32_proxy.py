#!/usr/bin/env python3
"""
esp32_proxy.py – ESP32C6 BLE Proxy
Serves the ESP32C6 web UI on :8082 and proxies /api/* calls via BLE GATT.

Requires: pip3 install bleak
"""

import asyncio
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from bleak import BleakClient, BleakScanner

# =============================================================================
# Configuration
# =============================================================================

BLE_DEVICE_NAME = "ESP32C6_Gateway"
BLE_REQ_UUID    = "0000fff1-0000-1000-8000-00805f9b34fb"   # CMD_REQ (Write)
BLE_RES_UUID    = "0000fff2-0000-1000-8000-00805f9b34fb"   # CMD_RES (Notify)
WEB_DIR         = "/home/orangepi/esp32/web"
HTTP_PORT       = 8083

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
            chunks[1] = text
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

    total = max(chunks.keys())
    full = "".join(chunks[i] for i in range(1, total + 1))

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


async def _reconnect_loop():
    """Background coroutine: keep BLE connection alive."""
    global _client, _lock, connected

    _lock = asyncio.Lock()
    _ready.set()                # signal HTTP server it may start

    while True:
        if not connected:
            try:
                print(f"[BLE] Scanning for {BLE_DEVICE_NAME}…")
                dev = await BleakScanner.find_device_by_name(BLE_DEVICE_NAME, timeout=10.0)
                if dev is None:
                    raise ConnectionError("Device not found")
                _client = BleakClient(dev, disconnected_callback=_on_disc)
                await _client.connect()
                connected = True
                print(f"[BLE] Connected: {dev.address}")
            except Exception as exc:
                print(f"[BLE] {exc} – retrying in 5 s")
                await asyncio.sleep(5)
        else:
            await asyncio.sleep(2)


def _on_disc(_c):
    global connected
    connected = False
    print("[BLE] Disconnected – will reconnect")


def _start_ble_thread():
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop.create_task(_reconnect_loop())
    _loop.run_forever()


# =============================================================================
# Sync wrapper (called from HTTP handler threads)
# =============================================================================

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
        if self.path.startswith("/api/"):
            self._handle_api()
        else:
            self._serve_static()

    def do_POST(self):
        if self.path.startswith("/api/"):
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
    threading.Thread(target=_start_ble_thread, daemon=True).start()
    _ready.wait()   # block until BLE lock is initialised (loop is running)

    print(f"[HTTP] ESP32C6 proxy on :{HTTP_PORT}")
    print(f"[HTTP] Web files: {WEB_DIR}")

    class ReusableHTTPServer(HTTPServer):
        allow_reuse_address = True

    try:
        ReusableHTTPServer(("0.0.0.0", HTTP_PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        pass

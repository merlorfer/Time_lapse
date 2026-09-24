#!/usr/bin/env python3
"""
wifigw_forward.py – Transparent HTTP forward for WiFi-connected gateway units
(e.g. the WiFiGateway ESP32-C3, see ../WiFiGateway). Each configured unit gets
its own listening port on the Orange Pi; requests are forwarded byte-for-byte
to the unit's current LAN IP – no endpoint translation, no UI rewriting, the
unit already serves its own complete web UI + REST API.

Units only support DHCP (no mDNS, no static IP). The IP captured when a unit
is added (web/app.py resolves it by actively pinging the given IP) seeds the
initial target; on a background timer each unit's last-known IP is re-pinged
directly to keep it fresh (DHCP leases are observed to stay stable here), and
only if that stops responding does it fall back to scanning the ARP table for
the unit's MAC (which only finds it if something else on the LAN has talked
to it recently – a purely passive table read is not reliable on its own).

Units are configured via the Time_lapse web UI (web/app.py's /api/units*),
stored in timelapse_config.json's "units" list, and this service is restarted
whenever that list changes (see web/app.py's restart_unit_forward()).
"""

import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client

CONFIG_FILE      = "/home/orangepi/timelapse/timelapse_config.json"
STATUS_FILE      = "/tmp/wifigw_status.json"
ARP_RECHECK_SEC  = 60
CONNECT_TIMEOUT  = 5.0

_HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate",
               "proxy-authorization", "te", "trailers",
               "transfer-encoding", "upgrade", "host"}


def load_units() -> list:
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        return cfg.get("units", [])
    except Exception:
        return []


def normalize_mac(mac: str) -> str:
    return mac.strip().lower().replace("-", ":")


def resolve_ip_by_mac(mac: str):
    """Look up the current IP for a MAC address via the kernel ARP table."""
    try:
        with open("/proc/net/arp") as f:
            lines = f.readlines()[1:]
        for line in lines:
            parts = line.split()
            if len(parts) >= 4 and parts[3].lower() == mac and parts[0] != "0.0.0.0":
                return parts[0]
    except Exception:
        pass
    return None


def mac_for_ip(ip: str):
    try:
        with open("/proc/net/arp") as f:
            lines = f.readlines()[1:]
        for line in lines:
            parts = line.split()
            if len(parts) >= 4 and parts[0] == ip and parts[3] != "00:00:00:00:00:00":
                return parts[3].lower()
    except Exception:
        pass
    return None


def ping_ok(ip: str, timeout: float = 1.5) -> bool:
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", str(int(timeout) or 1), ip],
                            capture_output=True, timeout=timeout + 1)
        return r.returncode == 0
    except Exception:
        return False


class UnitState:
    """Tracks the current resolved IP for one configured unit."""

    def __init__(self, unit: dict):
        self.name = unit["name"]
        self.mac  = normalize_mac(unit["mac"])
        self.port = unit["port"]
        self.ip   = None
        self.last_resolved = None
        self._lock = threading.Lock()
        self._load_cached_ip()
        if not self.ip and unit.get("ip"):
            # Seed from the IP captured when the unit was added, so forwarding
            # works immediately at startup instead of waiting on ARP discovery.
            self.ip = unit["ip"]

    def _cache_file(self):
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", self.name)
        return f"/tmp/wifigw_ip_{safe}.txt"

    def _load_cached_ip(self):
        try:
            with open(self._cache_file()) as f:
                ip = f.read().strip()
                if ip:
                    self.ip = ip
        except Exception:
            pass

    def _mark_resolved(self, ip: str):
        with self._lock:
            if ip != self.ip:
                print(f"[{self.name}] IP resolved: {ip}")
            self.ip = ip
            self.last_resolved = time.time()
        try:
            with open(self._cache_file(), "w") as f:
                f.write(ip)
        except Exception:
            pass

    def refresh(self):
        # Fast path: the last known IP is usually still correct (DHCP leases
        # are observed to stay stable for these units) -- ping it directly
        # instead of depending on incidental ARP traffic from elsewhere.
        if self.ip and ping_ok(self.ip):
            mac = mac_for_ip(self.ip)
            if mac is None or mac == self.mac:
                self._mark_resolved(self.ip)
                return
            print(f"[{self.name}] {self.ip} now answers as a different MAC "
                  f"({mac}); re-resolving by MAC")

        # Fallback: the unit may have moved to a new IP -- scan the ARP table
        # for its MAC (only works if something else recently talked to it).
        ip = resolve_ip_by_mac(self.mac)
        if ip:
            self._mark_resolved(ip)
        else:
            print(f"[{self.name}] not reachable at last known IP {self.ip}, "
                  f"and MAC {self.mac} not seen elsewhere; keeping last known IP")

    def current_ip(self):
        with self._lock:
            return self.ip


def make_handler(state: UnitState):
    class ForwardHandler(BaseHTTPRequestHandler):

        def log_message(self, *_):
            pass  # silence access log

        def _forward(self):
            ip = state.current_ip()
            if not ip:
                self._send_error(503, "Az egység IP-je még nincs feloldva "
                                       "(MAC nem látszik az ARP-táblában)")
                return

            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in _HOP_BY_HOP}

            try:
                conn = http.client.HTTPConnection(ip, 80, timeout=CONNECT_TIMEOUT)
                conn.request(self.command, self.path, body=body, headers=headers)
                resp = conn.getresponse()
                data = resp.read()
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() not in _HOP_BY_HOP:
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)
                conn.close()
            except Exception as exc:
                self._send_error(502, f"Nem sikerült elérni az egységet ({ip}): {exc}")

        def _send_error(self, code, message):
            body = json.dumps({"error": message}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_PUT = do_DELETE = do_OPTIONS = _forward

    return ForwardHandler


class ReusableHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve_unit(state: UnitState):
    handler = make_handler(state)
    server = ReusableHTTPServer(("0.0.0.0", state.port), handler)
    print(f"[{state.name}] forwarding :{state.port} -> MAC {state.mac}")
    server.serve_forever()


def refresh_loop(states):
    while True:
        for s in states:
            s.refresh()
        time.sleep(ARP_RECHECK_SEC)


def status_writer(states):
    while True:
        try:
            status = {s.name: {"port": s.port, "mac": s.mac, "ip": s.ip,
                                "last_resolved": s.last_resolved}
                      for s in states}
            tmp = STATUS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(status, f)
            os.replace(tmp, STATUS_FILE)
        except Exception as exc:
            print(f"[status] write failed: {exc}")
        time.sleep(5)


if __name__ == "__main__":
    units = load_units()
    if not units:
        print("[wifigw-forward] No units configured, idling.")

    states = [UnitState(u) for u in units]
    for s in states:
        s.refresh()  # resolve immediately at startup, not just after the first interval

    threading.Thread(target=status_writer, args=(states,), daemon=True).start()
    threading.Thread(target=refresh_loop, args=(states,), daemon=True).start()

    for s in states:
        threading.Thread(target=serve_unit, args=(s,), daemon=True).start()

    # Keep the main thread alive (and systemd happy) even with zero units.
    while True:
        time.sleep(3600)

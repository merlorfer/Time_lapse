#!/usr/bin/env python3
"""
precapture.py – control command before each timelapse frame, closing command after.

Use case: a fan next to the camera shakes the scene, so it is switched off a
few seconds before the frame and on again afterwards.

CLI (called by capture.sh, always exits 0 so it can never break a capture):
    precapture.py start [--max-lead SEC]   send the command, then wait the lead time
    precapture.py end                      send the closing command

The command is a rules-engine action ("off Fan", "on Fan", "set var3 1", ...) sent
to POST /api/rules/exec of the chosen unit over loopback: the serial unit through
esp32_proxy (:8083), a WiFiGateway through its forward port. web/app.py imports
send_command() for the UI test buttons.

Safety of the closing command: the moment the start phase begins, the closing
command is written to PENDING_FILE (SD card). `end` removes it only after a
successful send. If capture.sh dies or the Pi loses power in between, the next
`start` first sends the left-over closing command. A failed closing command is
retried and, if it still fails, reported by email (at most once an hour).
"""

import http.client
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import alert_lib  # noqa: E402

PENDING_FILE = "/home/orangepi/timelapse/precapture_pending.json"
ALERT_STATE  = "/home/orangepi/timelapse/precapture_alert.json"
STATUS_FILE  = "/tmp/precapture_status.json"
UART_PORT    = 8083
UART_KEY     = "UART"
CMD_TIMEOUT  = 20.0
END_ATTEMPTS = 3
END_BACKOFF  = (2, 5)          # seconds between the closing-command attempts
ALERT_MIN_GAP = 3600           # email at most once an hour


def log(msg: str):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [precapture] {msg}", flush=True)


def _read_json(path: str) -> dict:
    try:
        with open(path) as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: str, data: dict):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def get_cfg() -> dict:
    cfg = alert_lib.load_config()
    pc = cfg.get("precapture") or {}
    return {
        "enabled": bool(pc.get("enabled", False)),
        "lead_sec": max(0, int(pc.get("lead_sec", 0) or 0)),
        "target": str(pc.get("target", UART_KEY)),
        "cmd": str(pc.get("cmd", "")).strip(),
        "end_cmd": str(pc.get("end_cmd", "")).strip(),
    }


def source_port(target: str):
    """Loopback port serving the unit, or None if unknown / serial switched off."""
    cfg = alert_lib.load_config()
    if target == UART_KEY:
        return UART_PORT if cfg.get("serial_enabled", True) else None
    for u in cfg.get("units", []):
        if u.get("name") == target:
            return u.get("port")
    return None


def send_command(target: str, cmd: str, timeout: float = CMD_TIMEOUT) -> tuple:
    """POST the command to the unit. Returns (ok, message, seconds).
    ok means the gateway accepted/parsed it, not that the device physically switched."""
    port = source_port(target)
    if port is None:
        return False, f"ismeretlen vagy kikapcsolt egység: {target}", 0.0
    t0 = time.time()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            conn.request("POST", "/api/rules/exec", body=json.dumps({"cmd": cmd}),
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            data = resp.read()
        finally:
            conn.close()
        try:
            d = json.loads(data) if data else {}
        except ValueError:
            d = {}
        ok = resp.status == 200 and (d.get("status") == "ok" or d.get("success") is True)
        if ok:
            msg = "OK"
        elif resp.status == 200 and d.get("status") == "error":
            msg = d.get("message") or "a gateway nem értelmezte a parancsot (hibás parancs vagy eszköznév?)"
        else:
            msg = d.get("message") or d.get("error") or f"HTTP {resp.status}"
    except Exception as e:
        ok, msg = False, f"{type(e).__name__}: {e}"
    return ok, msg, round(time.time() - t0, 2)


def record(phase: str, ok: bool, msg: str, secs: float, cmd: str = ""):
    st = _read_json(STATUS_FILE)
    st[phase] = {"ts": time.time(), "ok": ok, "message": msg, "secs": secs, "cmd": cmd}
    try:
        _write_json(STATUS_FILE, st)
    except OSError:
        pass


def _alert_end_failed(p: dict, msg: str):
    state = _read_json(ALERT_STATE)
    if time.time() - state.get("last", 0) < ALERT_MIN_GAP:
        return
    try:
        _write_json(ALERT_STATE, {"last": time.time()})
    except OSError:
        pass
    alert_lib.enqueue(
        "precapture_end_failed",
        "A képrögzítés utáni lezáró parancs sikertelen",
        f"A(z) '{p.get('end_cmd', '')}' lezáró parancs ({p.get('target', '')}) nem ment ki a "
        f"képrögzítés után: {msg}. A vezérelt eszköz (pl. a ventilátor) kikapcsolva maradhatott! "
        f"Rendszer: {os.uname().nodename}")


def _finish(p: dict, attempts: int) -> bool:
    """Send the pending closing command; the marker is removed only on success."""
    ok, msg, secs = False, "", 0.0
    for i in range(attempts):
        ok, msg, secs = send_command(p.get("target", UART_KEY), p.get("end_cmd", ""))
        if ok:
            break
        log(f"lezáró parancs sikertelen ({i + 1}/{attempts}): {msg}")
        if i < attempts - 1:
            time.sleep(END_BACKOFF[min(i, len(END_BACKOFF) - 1)])
    record("end", ok, msg, secs, p.get("end_cmd", ""))
    if ok:
        try:
            os.remove(PENDING_FILE)
        except OSError:
            pass
        log(f"lezáró parancs elküldve: '{p.get('end_cmd')}' → {p.get('target')} ({secs}s)")
    else:
        _alert_end_failed(p, msg)
    return ok


def start(max_lead: int = 0):
    pc = get_cfg()
    if not pc["enabled"]:
        return

    leftover = _read_json(PENDING_FILE)
    if leftover.get("end_cmd"):
        log("az előző ciklus lezáró parancsa nem ment ki, újrapróbálom")
        _finish(leftover, attempts=1)

    if not pc["cmd"] and not pc["end_cmd"]:
        return

    lead = pc["lead_sec"]
    if max_lead and lead > max_lead:
        log(f"a várakozás ({lead} mp) nagyobb az intervallumnál, {max_lead} mp-re csökkentve")
        lead = max_lead

    if pc["end_cmd"]:
        _write_json(PENDING_FILE, {"target": pc["target"], "end_cmd": pc["end_cmd"],
                                   "started": time.time()})
    if not pc["cmd"]:
        return

    ok, msg, secs = send_command(pc["target"], pc["cmd"])
    record("start", ok, msg, secs, pc["cmd"])
    if not ok:
        log(f"előzetes parancs sikertelen, a kép így is elkészül: {msg}")
        return
    log(f"előzetes parancs elküldve: '{pc['cmd']}' → {pc['target']} ({secs}s), várakozás {lead} mp")
    if lead > 0:
        time.sleep(lead)


def end():
    p = _read_json(PENDING_FILE)
    if p.get("end_cmd"):
        _finish(p, attempts=END_ATTEMPTS)


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if action == "start":
            ml = 0
            if "--max-lead" in sys.argv:
                try:
                    ml = int(sys.argv[sys.argv.index("--max-lead") + 1])
                except (IndexError, ValueError):
                    ml = 0
            start(ml)
        elif action == "end":
            end()
        else:
            print("Használat: precapture.py start [--max-lead SEC] | end", file=sys.stderr)
    except Exception as exc:   # never let this break a capture
        log(f"hiba: {exc!r}")
    sys.exit(0)

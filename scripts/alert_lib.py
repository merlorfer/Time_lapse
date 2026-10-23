#!/usr/bin/env python3
"""
alert_lib.py – shared logic for email event notifications.

Events are never sent inline. send_alert.py only appends them to a persistent
queue (a JSON-lines file on the SD card, so it survives power loss) and
email_dispatcher.py delivers them as soon as the SMTP server is reachable.
This is what makes alerts raised during boot work even though the router (and
therefore the internet) often comes up later than the Pi.

The same file doubles as the "Email napló" shown in the web UI.
"""

import fcntl
import json
import os
import secrets
import smtplib
import socket
import subprocess
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

CONFIG_FILE     = "/home/orangepi/timelapse/timelapse_config.json"
EVENTS_FILE     = "/home/orangepi/timelapse/logs/email_events.jsonl"
STATUS_FILE     = "/tmp/email_dispatcher_status.json"
MAX_ENTRIES     = 200            # oldest finished entries are dropped beyond this
PENDING_MAX_AGE = 72 * 3600      # undeliverable for this long -> "expired"

# (id, label shown in the UI). Every event is enabled by default.
EVENTS = [
    ("boot",            "Rendszer elindult (indulás / újraindulás után)"),
    ("reboot_shutdown", "Újraindítás vagy leállítás indul (biztonságos / ütemezett)"),
    ("power_restored",  "Áramszünet után a felvétel visszaállt"),
    ("restart_failed",  "A felvétel automatikus újraindítása sikertelen (boot után)"),
    ("usb_boot_error",  "USB meghajtó nem elérhető boot után (SD fallback)"),
    ("render_error",    "Napi videó render hiba"),
    ("usb_fallback",    "Napi videó SD kártyára mentve (USB hiba)"),
    ("save_critical",   "Napi videó mentési hiba – KRITIKUS"),
]
EVENT_IDS = {e[0] for e in EVENTS}
EVENT_LABELS = dict(EVENTS)
EVENT_LABELS["test"] = "Teszt email"


# ── config ───────────────────────────────────────────────────────────────────

def load_config() -> dict:
    cfg = {"email_enabled": False, "email_to": "", "smtp_server": "smtp.gmail.com",
           "smtp_port": 587, "smtp_user": "", "smtp_password": "", "email_events": {}}
    try:
        with open(CONFIG_FILE) as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def event_enabled(cfg: dict, event_id: str) -> bool:
    flags = cfg.get("email_events") or {}
    return bool(flags.get(event_id, True))


def config_complete(cfg: dict) -> bool:
    return all(str(cfg.get(k, "")).strip() for k in ("email_to", "smtp_user", "smtp_password"))


# ── persistent event store (JSON lines, flock-protected) ─────────────────────

class _Locked:
    """Exclusive lock around read-modify-write of the events file."""
    def __enter__(self):
        os.makedirs(os.path.dirname(EVENTS_FILE), exist_ok=True)
        self._fh = open(EVENTS_FILE + ".lock", "w")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self._fh, fcntl.LOCK_UN)
        self._fh.close()


def _read() -> list:
    out = []
    try:
        with open(EVENTS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
    except FileNotFoundError:
        pass
    return out


def _write(entries: list):
    tmp = EVENTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(tmp, EVENTS_FILE)


def read_entries() -> list:
    with _Locked():
        return _read()


def modify_entries(fn) -> list:
    """fn(entries) mutates the list in place; the result is persisted."""
    with _Locked():
        entries = _read()
        fn(entries)
        finished = [e for e in entries if e.get("status") != "pending"]
        if len(entries) > MAX_ENTRIES and finished:
            drop = {id(e) for e in finished[: len(entries) - MAX_ENTRIES]}
            entries[:] = [e for e in entries if id(e) not in drop]
        _write(entries)
        return entries


def clear_finished() -> tuple:
    """Delete everything that is not still waiting for delivery."""
    removed = 0

    def fn(entries):
        nonlocal removed
        keep = [e for e in entries if e.get("status") == "pending"]
        removed = len(entries) - len(keep)
        entries[:] = keep
    kept = len(modify_entries(fn))
    return removed, kept


# ── clock helpers (the Pi has no battery clock: boot-time stamps can be wrong) ─

def boot_id() -> str:
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return ""


def uptime() -> float:
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError):
        return 0.0


def ntp_synced() -> bool:
    try:
        r = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() == "yes"
    except Exception:
        return False


def fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


# ── queue API ────────────────────────────────────────────────────────────────

def enqueue(event_id: str, subject: str, body: str):
    """Queue an event for delivery. Returns the entry, or None when email is
    disabled or this event type is switched off (nothing is logged then)."""
    cfg = load_config()
    if not cfg.get("email_enabled"):
        return None
    if event_id in EVENT_IDS and not event_enabled(cfg, event_id):
        return None
    entry = {
        "id": secrets.token_hex(6), "event": event_id, "subject": subject, "body": body,
        "ts": time.time(), "ts_fixed": ntp_synced(), "boot_id": boot_id(), "uptime": uptime(),
        "status": "pending", "attempts": 0, "last_error": "", "next_try": 0, "sent_ts": 0,
    }
    modify_entries(lambda entries: entries.append(entry))
    return entry


def record_direct(event_id: str, subject: str, ok: bool, error: str = ""):
    """Log an email that was sent synchronously (the UI test button)."""
    now = time.time()
    entry = {
        "id": secrets.token_hex(6), "event": event_id, "subject": subject, "body": "",
        "ts": now, "ts_fixed": True, "boot_id": boot_id(), "uptime": uptime(),
        "status": "sent" if ok else "error", "attempts": 1, "last_error": error,
        "next_try": 0, "sent_ts": now if ok else 0,
    }
    modify_entries(lambda entries: entries.append(entry))


# ── delivery ─────────────────────────────────────────────────────────────────

def smtp_reachable(cfg: dict, timeout: float = 5.0) -> bool:
    """DNS + route + port to the SMTP server: exactly what sending needs."""
    try:
        with socket.create_connection((cfg.get("smtp_server", ""), int(cfg.get("smtp_port", 587))),
                                      timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def send_now(subject: str, body: str, cfg: dict = None) -> tuple:
    """Send one email immediately. Returns (ok, error_text)."""
    cfg = cfg or load_config()
    if not config_complete(cfg):
        return False, "az email beállítás hiányos (címzett / feladó / jelszó)"
    srv, port = cfg.get("smtp_server", "smtp.gmail.com"), int(cfg.get("smtp_port", 587))
    user, pwd = cfg["smtp_user"].strip(), cfg["smtp_password"].strip()
    host = os.uname().nodename if hasattr(os, "uname") else "timelapse"

    msg = MIMEMultipart()
    msg["From"], msg["To"] = user, cfg["email_to"].strip()
    msg["Subject"] = f"[{host}] {subject}"
    msg.attach(MIMEText(body, "plain", "utf-8"))
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(srv, port, timeout=30)
        else:
            server = smtplib.SMTP(srv, port, timeout=30)
            server.ehlo()
            server.starttls()
        with server:
            server.login(user, pwd)
            server.sendmail(user, cfg["email_to"].strip(), msg.as_string())
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

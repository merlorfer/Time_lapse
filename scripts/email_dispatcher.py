#!/usr/bin/env python3
"""
email_dispatcher.py – delivers queued event emails once the internet is up.

Every TICK_SEC it checks whether the SMTP server is reachable; only then are
pending events sent (oldest first). Failures are retried with backoff and the
reason is stored with the entry, so the UI log shows why something is waiting.
Entries still undelivered after PENDING_MAX_AGE (3 days) are marked expired.

It also writes /tmp/email_dispatcher_status.json (internet state, pending
count) for the UI, and repairs event timestamps recorded before the clock was
NTP-synced (the Pi has no battery clock, so boot-time stamps can be hours off):
once synced, the real time is reconstructed from the uptime stored with the
event.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import alert_lib as lib  # noqa: E402

TICK_SEC      = 10
LATE_NOTE_SEC = 120      # mention in the mail when it was delayed by more than this


def write_status(online, pending: int):
    tmp = lib.STATUS_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump({"online": online, "pending": pending, "checked": time.time()}, f)
        os.replace(tmp, lib.STATUS_FILE)
    except OSError:
        pass


def fix_timestamps():
    """Correct stamps recorded while the clock was not yet synchronised."""
    if not any(not e.get("ts_fixed") for e in lib.read_entries()):
        return
    if not lib.ntp_synced():
        return
    now, cur_boot, cur_up = time.time(), lib.boot_id(), lib.uptime()

    def fn(entries):
        for e in entries:
            if e.get("ts_fixed"):
                continue
            if e.get("boot_id") and e["boot_id"] == cur_boot:
                e["ts"] = now - max(0.0, cur_up - e.get("uptime", cur_up))
            e["ts_fixed"] = True   # entries from an earlier boot can't be repaired
    lib.modify_entries(fn)


def expire_old():
    now = time.time()

    def fn(entries):
        for e in entries:
            if e["status"] == "pending" and now - e["ts"] > lib.PENDING_MAX_AGE:
                e["status"] = "expired"
                e["last_error"] = (e.get("last_error") or "") + " (3 napja nem sikerült elküldeni)"
    lib.modify_entries(fn)


def deliver(entry: dict, cfg: dict) -> tuple:
    body = entry.get("body", "")
    body += f"\n\nEsemény időpontja: {lib.fmt_ts(entry['ts'])}"
    if time.time() - entry["ts"] > LATE_NOTE_SEC:
        body += " (késve elküldve: az esemény idején nem volt internetkapcsolat)"
    return lib.send_now(entry["subject"], body, cfg)


def tick():
    fix_timestamps()
    expire_old()
    cfg = lib.load_config()
    pending = [e for e in lib.read_entries() if e["status"] == "pending"]

    if not cfg.get("email_enabled"):
        write_status(None, len(pending))
        return
    online = lib.smtp_reachable(cfg)
    write_status(online, len(pending))
    if not online or not pending or not lib.config_complete(cfg):
        if pending and online and not lib.config_complete(cfg):
            _note_all(pending, "az email beállítás hiányos (címzett / feladó / jelszó)")
        return

    now = time.time()
    for entry in sorted(pending, key=lambda e: e["ts"]):
        if entry.get("next_try", 0) > now:
            continue
        ok, err = deliver(entry, cfg)
        sent_ts = time.time()

        def fn(entries, eid=entry["id"], ok=ok, err=err):
            for e in entries:
                if e["id"] != eid:
                    continue
                e["attempts"] = e.get("attempts", 0) + 1
                if ok:
                    e.update(status="sent", sent_ts=sent_ts, last_error="")
                else:
                    e["last_error"] = err
                    e["next_try"] = time.time() + min(600, 10 * 2 ** e["attempts"])
        lib.modify_entries(fn)
        print(f"[dispatcher] {entry['event']}: {'sent' if ok else 'FAILED – ' + err}", flush=True)
        if not ok:
            break          # same reason will hit the rest; wait for the backoff
        time.sleep(1)


def _note_all(pending, text):
    def fn(entries):
        for e in entries:
            if e["status"] == "pending" and e.get("last_error") != text:
                e["last_error"] = text
    lib.modify_entries(fn)


def main():
    print("[dispatcher] started", flush=True)
    while True:
        try:
            tick()
        except Exception as exc:       # never die: alerts must keep working
            print(f"[dispatcher] error: {exc!r}", flush=True)
        time.sleep(TICK_SEC)


if __name__ == "__main__":
    main()

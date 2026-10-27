#!/usr/bin/env python3
"""
unit_errors.py – persistent error journal of the CLCode01 / WiFiGateway units.

Written by unit_watch.py, read and acknowledged from the web UI (app.py).

One entry per (unit, device, error). While an entry is NOT acknowledged, the
same error showing up again only raises its counter and last-seen time: no new
entry and no new email. After the entry has been acknowledged, the same error
appearing again creates a fresh entry (and a fresh email).

Entry: {id, key, unit, device, kind, message, details[], first_ts, last_ts,
        count, acked, acked_ts, cleared_ts}
  cleared_ts: the firmware no longer reports this device error (informational
              only; it never re-arms or closes the entry by itself).
"""

import fcntl
import json
import os
import secrets
import time

ERRORS_FILE = os.environ.get("UNIT_ERRORS_FILE", "/home/orangepi/timelapse/logs/unit_errors.json")
MAX_ENTRIES = 500
MAX_DETAILS = 12


class _Locked:
    def __enter__(self):
        os.makedirs(os.path.dirname(ERRORS_FILE), exist_ok=True)
        self._fh = open(ERRORS_FILE + ".lock", "w")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self._fh, fcntl.LOCK_UN)
        self._fh.close()


def _read() -> list:
    try:
        with open(ERRORS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write(entries: list):
    tmp = ERRORS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)
    os.replace(tmp, ERRORS_FILE)


def _trim(entries: list):
    """Over the cap, the oldest acknowledged entries go first."""
    extra = len(entries) - MAX_ENTRIES
    if extra <= 0:
        return
    acked = sorted((e for e in entries if e.get("acked")), key=lambda e: e.get("last_ts", 0))
    drop = {id(e) for e in acked[:extra]}
    entries[:] = [e for e in entries if id(e) not in drop]


def report(key: str, unit: str, device: str, kind: str, message: str, details=None, now=None,
           state_like: bool = False):
    """Record an occurrence. Returns (entry, is_new); is_new is True only when a
    new entry was created, i.e. when an email should go out.
    state_like: the error is a standing condition seen on every poll (a device
    error held by the firmware); while it stays present it is not a new occurrence,
    so it does not touch the open entry. Only a reappearance after it cleared counts."""
    now = now if now is not None else time.time()
    details = [str(d) for d in (details or [])][-MAX_DETAILS:]
    result = {}

    def fn(entries):
        for e in entries:
            if e["key"] == key and not e.get("acked"):
                if state_like and not e.get("cleared_ts"):
                    result["entry"], result["new"] = e, False
                    return
                e["count"] = e.get("count", 1) + 1
                e["last_ts"] = now
                e["cleared_ts"] = 0
                result["entry"], result["new"] = e, False
                return
        e = {"id": secrets.token_hex(6), "key": key, "unit": unit, "device": device,
             "kind": kind, "message": message, "details": details, "first_ts": now,
             "last_ts": now, "count": 1, "acked": False, "acked_ts": 0, "cleared_ts": 0}
        entries.append(e)
        _trim(entries)
        result["entry"], result["new"] = e, True

    _modify(fn)
    return result["entry"], result["new"]


def mark_cleared(unit: str, kinds, present_keys: set, now=None):
    """Open entries of this unit whose firmware error is gone get a cleared_ts."""
    now = now if now is not None else time.time()

    def fn(entries):
        for e in entries:
            if (e["unit"] == unit and e["kind"] in kinds and not e.get("acked")
                    and not e.get("cleared_ts") and e["key"] not in present_keys):
                e["cleared_ts"] = now

    _modify(fn)


def _modify(fn):
    with _Locked():
        entries = _read()
        fn(entries)
        _write(entries)
        return entries


def list_entries() -> list:
    with _Locked():
        return _read()


def ack(entry_id: str = "", all_entries: bool = False, now=None) -> int:
    now = now if now is not None else time.time()
    n = [0]

    def fn(entries):
        for e in entries:
            if not e.get("acked") and (all_entries or e["id"] == entry_id):
                e["acked"], e["acked_ts"] = True, now
                n[0] += 1

    _modify(fn)
    return n[0]


def clear_acked() -> int:
    n = [0]

    def fn(entries):
        keep = [e for e in entries if not e.get("acked")]
        n[0] = len(entries) - len(keep)
        entries[:] = keep

    _modify(fn)
    return n[0]

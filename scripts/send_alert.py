#!/usr/bin/env python3
"""
send_alert.py – queue an email notification for an event.
Usage: python3 send_alert.py [--wait SEC] <event_id> "Subject" "Message body"

Never blocks on the network: the event is appended to the persistent queue
(see alert_lib.py) and email_dispatcher.py delivers it once the internet is
reachable. Events whose checkbox is switched off in the UI (or with email
disabled) are silently skipped. --wait gives the dispatcher up to SEC seconds
to deliver it before returning (used right before a reboot/shutdown).
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import alert_lib  # noqa: E402


def main() -> int:
    args = sys.argv[1:]
    wait = 0.0
    if args and args[0] == "--wait":
        try:
            wait = float(args[1])
        except (IndexError, ValueError):
            pass
        args = args[2:]
    if len(args) < 3:
        print(f'Használat: {sys.argv[0]} [--wait SEC] <esemény_id> "Tárgy" "Üzenet"', file=sys.stderr)
        return 1

    entry = alert_lib.enqueue(args[0], args[1], args[2])
    if entry is None:
        return 0

    deadline = time.time() + wait
    while time.time() < deadline:
        cur = next((e for e in alert_lib.read_entries() if e["id"] == entry["id"]), None)
        if cur is None or cur.get("status") != "pending":
            break
        time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())

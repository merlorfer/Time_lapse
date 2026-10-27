#!/usr/bin/env python3
"""
session_days.py – which capture session does a frame belong to, and when is a
session ready to be compiled into its daily video?

A session is one run of the capture window. Its "day" is the date the window
STARTED on, for windows that stay within one day (08:00-23:59) and for windows
that cross midnight (22:00-06:00, or 08:00-00:00) alike:

    frame time <= window end and the window crosses midnight  -> previous date
    otherwise                                                 -> its own date

A session is due GRACE seconds after its window ended, whether or not the
timelapse is still running; frames of a session that has not ended yet are
never touched. Frames stay on the RAM disk until compile_daily.sh has saved
the video successfully and deleted exactly the frames it used.

Commands (used by compile_check.sh / compile_daily.sh):
    due [--grace SEC]     session days that are ready, oldest first
    frames DAY            frame paths of that session day, in time order
    try DAY               exit 0 if an automatic compile attempt is allowed now
                          (max 3 attempts, 20 min apart) and record it
    window                print the window in use (debugging)
Test overrides: --now "YYYY-MM-DD HH:MM", --frame-dir DIR, --window HH:MM-HH:MM,
                --state FILE
"""

import json
import os
import re
import sys
from datetime import date, datetime, time as dtime, timedelta

FRAME_DIR = "/tmp/timelapse_frames"
SESSION_CONF = "/tmp/timelapse_session.conf"
LAST_SESSION = "/home/orangepi/timelapse/last_session.json"
CONFIG_SH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "config.sh")
STATE_FILE = "/tmp/timelapse_compile_attempts.json"
FRAME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(\d{2})\.jpg$")
MAX_ATTEMPTS = 3
RETRY_GAP = 20 * 60


def _conf_value(path: str, key: str):
    try:
        with open(path) as f:
            m = re.search(rf'^{key}="?([0-9]{{2}}:[0-9]{{2}})"?', f.read(), re.M)
            return m.group(1) if m else None
    except OSError:
        return None


def get_window() -> tuple:
    """(start, end) as 'HH:MM': running session, else the last saved one, else defaults."""
    s, e = _conf_value(SESSION_CONF, "CAPTURE_START"), _conf_value(SESSION_CONF, "CAPTURE_END")
    if s and e:
        return s, e
    try:
        with open(LAST_SESSION) as f:
            d = json.load(f)
        if re.fullmatch(r"\d{2}:\d{2}", d.get("session_start", "")) and \
           re.fullmatch(r"\d{2}:\d{2}", d.get("session_end", "")):
            return d["session_start"], d["session_end"]
    except (OSError, ValueError):
        pass
    return (_conf_value(CONFIG_SH, "CAPTURE_START") or "06:00",
            _conf_value(CONFIG_SH, "CAPTURE_END") or "20:00")


def session_day(frame_date: date, hhmm: str, start: str, end: str) -> date:
    if start > end and hhmm <= end:      # window crosses midnight, frame is in its "after midnight" part
        return frame_date - timedelta(days=1)
    return frame_date


def window_end(day: date, start: str, end: str) -> datetime:
    h, m = map(int, end.split(":"))
    dt = datetime.combine(day, dtime(h, m))
    return dt + timedelta(days=1) if start > end else dt


def list_frames(frame_dir: str, start: str, end: str) -> dict:
    """{session day: [paths sorted by time]}"""
    out = {}
    try:
        names = sorted(os.listdir(frame_dir))
    except OSError:
        return out
    for name in names:
        m = FRAME_RE.match(name)
        if not m:
            continue
        y, mo, d, hh, mi, _ = map(int, m.groups())
        day = session_day(date(y, mo, d), f"{hh:02d}:{mi:02d}", start, end)
        out.setdefault(day, []).append(os.path.join(frame_dir, name))
    return out


def due_days(frames: dict, start: str, end: str, now: datetime, grace: int) -> list:
    return sorted(d for d in frames
                  if now >= window_end(d, start, end) + timedelta(seconds=grace))


def _attempt_allowed(day: str, state_file: str, now_ts: float) -> bool:
    try:
        with open(state_file) as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    rec = st.get(day, {"n": 0, "last": 0})
    if rec["n"] >= MAX_ATTEMPTS or now_ts - rec["last"] < RETRY_GAP:
        return False
    st[day] = {"n": rec["n"] + 1, "last": now_ts}
    tmp = state_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, state_file)
    return True


def main(argv: list) -> int:
    opts = {"--now": None, "--frame-dir": FRAME_DIR, "--window": None, "--state": STATE_FILE,
            "--grace": "60"}
    rest, i = [], 0
    while i < len(argv):
        if argv[i] in opts and i + 1 < len(argv):
            opts[argv[i]] = argv[i + 1]
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    if not rest:
        print(__doc__)
        return 1
    cmd = rest[0]

    if opts["--window"]:
        start, end = opts["--window"].split("-")
    else:
        start, end = get_window()
    now = datetime.strptime(opts["--now"], "%Y-%m-%d %H:%M") if opts["--now"] else datetime.now()

    if cmd == "window":
        print(f"{start}-{end}" + (" (crosses midnight)" if start > end else ""))
    elif cmd == "due":
        frames = list_frames(opts["--frame-dir"], start, end)
        for d in due_days(frames, start, end, now, int(opts["--grace"])):
            print(d.isoformat())
    elif cmd == "frames" and len(rest) > 1:
        frames = list_frames(opts["--frame-dir"], start, end)
        for p in frames.get(date.fromisoformat(rest[1]), []):
            print(p)
    elif cmd == "try" and len(rest) > 1:
        return 0 if _attempt_allowed(rest[1], opts["--state"], now.timestamp()) else 1
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

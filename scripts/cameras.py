#!/usr/bin/env python3
"""
cameras.py – the camera registry of the timelapse system.

Cameras work like the ESP units: a built-in USB webcam (renameable, exactly one)
plus any number of named IP cameras (RTSP). Every camera has its own capture
settings, its own parallel timelapse session, frames, videos and master.

Config (timelapse_config.json):
    "cameras": [
        {"name": "USB",    "type": "usb", "match": "C920", "device": "/dev/video0",
         <settings>, "precapture": {...}},
        {"name": "Garden", "type": "ip", "host": "192.168.1.10", "port": 554,
         "user": "admin", "password": "...",
         "rtsp_main": "rtsp://{host}:{port}/user={user}&password={password}&channel=1&stream=0.sdp?real_stream",
         "rtsp_sub":  "...stream=1.sdp?real_stream",
         <settings>, "precapture": {...}}
    ]

Per-camera runtime / storage layout (NAME = camera name):
    frames         /tmp/timelapse_frames/NAME/YYYY-MM-DD_HHMMSS.jpg
    active flag    /tmp/timelapse_active_NAME
    session conf   /tmp/timelapse_session_NAME.conf
    last session   <timelapse dir>/last_session_NAME.json   (power-loss restore)
    videos         <VIDEO_BASE>/archive/NAME/YYYY-MM-DD.mp4
    master         <VIDEO_BASE>/NAME_master.mp4
    renders        <VIDEO_BASE>/renders/NAME/*.mp4

CLI (used by the shell scripts):
    cameras.py env [NAME]       shell assignments for the camera (eval them)
    cameras.py names            camera names, one per line
    cameras.py default          name of the default (USB) camera
    cameras.py migrate          one-time move of the single-camera layout (idempotent)
    cameras.py restore-plan     boot restore: source line, then one TSV line per camera to restart
                                (an empty value is printed as "-")
    cameras.py capture-result NAME ok|fail [message]
                                track consecutive capture failures -> journal + email
"""

import fcntl
import json
import os
import re
import shlex
import sys
import time
from urllib.parse import quote

CONFIG_FILE = os.environ.get("TIMELAPSE_CONFIG", "/home/orangepi/timelapse/timelapse_config.json")
TL_DIR = os.path.dirname(CONFIG_FILE)
TMP = os.environ.get("TIMELAPSE_TMP", "/tmp")            # runtime state (tests point it elsewhere)
FRAMES_ROOT = os.environ.get("TIMELAPSE_FRAMES_ROOT", f"{TMP}/timelapse_frames")
VIDEO_BASES = os.environ.get("TIMELAPSE_VIDEO_BASES", f"/mnt/timelapse:{TL_DIR}/videos").split(":")
FAIL_STATE_FILE = f"{TMP}/timelapse_capture_failures.json"
FAIL_ALERT_AFTER = 3            # consecutive failed captures before the journal/email entry

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
RES_RE = re.compile(r"^\d{3,5}x\d{3,5}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")

DEFAULT_USB_NAME = "USB"
XM_MAIN = "rtsp://{host}:{port}/user={user}&password={password}&channel=1&stream=0.sdp?real_stream"
XM_SUB = "rtsp://{host}:{port}/user={user}&password={password}&channel=1&stream=1.sdp?real_stream"

SETTINGS_DEFAULTS = {
    "start": "06:00", "end": "20:00", "interval": 60,
    "capture_resolution": "1920x1080",   # usb: webcam mode; ip: "native" or WxH (scaled)
    "jpeg_quality": 85,                  # 1..100
    "camera_skip": 5,                    # usb only: frames dropped at start (white balance)
    "overlay": True, "font_size": 48,    # timestamp overlay; size is relative to a 1080p frame
    "video_resolution": "1920x1080",     # "native" keeps the frame size
    "fps": 25, "crf": 23, "preset": "medium",
}
PRECAPTURE_DEFAULTS = {"enabled": False, "lead_sec": 0, "target": "UART", "cmd": "", "end_cmd": ""}


# ── config ───────────────────────────────────────────────────────────────────

def load_config() -> dict:
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def update_config(fn) -> dict:
    """Read-modify-write of the config under a lock (atomic replace)."""
    lock = open(f"{TMP}/timelapse_config.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cfg = load_config()
        fn(cfg)
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CONFIG_FILE)
        return cfg
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def normalize(cam: dict, legacy: dict = None) -> dict:
    """A camera entry with every setting present. legacy: values that replace the
    built-in defaults (used for the USB camera on first creation)."""
    out = dict(cam)
    ctype = "ip" if cam.get("type") == "ip" else "usb"
    merged = dict(SETTINGS_DEFAULTS)
    if ctype == "ip":
        merged["capture_resolution"] = "native"
    merged.update(legacy or {})
    merged.update({k: v for k, v in cam.items() if k in SETTINGS_DEFAULTS and v not in (None, "")})
    out.update(merged)
    out["type"] = ctype
    pc = dict(PRECAPTURE_DEFAULTS)
    pc.update(cam.get("precapture") or {})
    out["precapture"] = pc
    if ctype == "ip":
        out.setdefault("port", 554)
        out.setdefault("user", "admin")
        out.setdefault("password", "")
        out.setdefault("rtsp_main", XM_MAIN)
        out.setdefault("rtsp_sub", XM_SUB)
    else:
        out.setdefault("match", "C920")
        out.setdefault("device", "/dev/video0")
    return out


def get_cameras(cfg: dict = None) -> list:
    """All cameras, USB first. The USB camera always exists (created from the legacy
    single-camera values on first use)."""
    cfg = cfg if cfg is not None else load_config()
    raw = [c for c in (cfg.get("cameras") or []) if isinstance(c, dict) and c.get("name")]
    cams = []
    if not any(c.get("type", "usb") == "usb" for c in raw):
        legacy_pc = cfg.get("precapture") or {}
        cams.append(normalize({"name": DEFAULT_USB_NAME, "type": "usb", "precapture": legacy_pc}))
    for c in raw:
        cams.append(normalize(c))
    cams.sort(key=lambda c: (c["type"] != "usb",))
    return cams


def default_name(cfg: dict = None) -> str:
    for c in get_cameras(cfg):
        if c["type"] == "usb":
            return c["name"]
    return DEFAULT_USB_NAME


def get_camera(name: str = "", cfg: dict = None):
    cams = get_cameras(cfg)
    if not name:
        name = default_name(cfg)
    for c in cams:
        if c["name"] == name:
            return c
    return None


def rtsp_url(cam: dict, stream: str = "main") -> str:
    tpl = cam.get("rtsp_main" if stream == "main" else "rtsp_sub") or ""
    return (tpl.replace("{host}", str(cam.get("host", "")))
               .replace("{port}", str(cam.get("port", 554)))
               .replace("{user}", quote(str(cam.get("user", "")), safe=""))
               .replace("{password}", quote(str(cam.get("password", "")), safe="")))


def paths(name: str) -> dict:
    return {
        "frame_dir": f"{FRAMES_ROOT}/{name}",
        "active_flag": f"{TMP}/timelapse_active_{name}",
        "session_conf": f"{TMP}/timelapse_session_{name}.conf",
        "last_capture": f"{TMP}/timelapse_last_capture_{name}",
        "capture_lock": f"{TMP}/timelapse_capture_{name}.lock",
        "last_session": f"{TL_DIR}/last_session_{name}.json",
        "compile_attempts": f"{TMP}/timelapse_compile_attempts_{name}.json",
    }


# ── validation (used by the web UI) ──────────────────────────────────────────

def validate_settings(s: dict) -> tuple:
    """(clean settings dict, error or None). Only keys present in s are checked."""
    clean = {}
    try:
        for k in ("start", "end"):
            if k in s:
                if not TIME_RE.match(str(s[k])):
                    return {}, f"Érvénytelen időpont ({k}): {s[k]}"
                clean[k] = str(s[k])
        for k, lo, hi in (("interval", 1, 86400), ("jpeg_quality", 1, 100), ("camera_skip", 0, 60),
                          ("font_size", 8, 200), ("fps", 1, 120), ("crf", 0, 51)):
            if k in s:
                n = int(s[k])
                if not lo <= n <= hi:
                    return {}, f"{k}: {lo}–{hi} között lehet"
                clean[k] = n
    except (TypeError, ValueError):
        return {}, "Számot vártam az egyik mezőben"
    for k in ("capture_resolution", "video_resolution"):
        if k in s:
            v = str(s[k]).strip().lower()
            if v != "native" and not RES_RE.match(v):
                return {}, f"{k}: 'native' vagy SZÉLESSÉGxMAGASSÁG (pl. 1920x1080)"
            clean[k] = v
    if "preset" in s:
        if s["preset"] not in PRESETS:
            return {}, "Ismeretlen ffmpeg preset"
        clean["preset"] = s["preset"]
    if "overlay" in s:
        clean["overlay"] = bool(s["overlay"])
    return clean, None


def validate_name(name: str, cfg: dict = None, ignore: str = "") -> str:
    """Error text, or '' when the name is usable."""
    if not NAME_RE.match(name or ""):
        return "A név csak betűt, számot, kötőjelet és aláhúzást tartalmazhat (1–32 karakter)"
    for c in get_cameras(cfg):
        if c["name"].lower() == name.lower() and c["name"] != ignore:
            return "Ilyen nevű kamera már van"
    return ""


# ── shell interface ──────────────────────────────────────────────────────────

def _bool(v) -> str:
    return "1" if v else "0"


def shell_env(name: str) -> str:
    cam = get_camera(name)
    if cam is None:
        return f"echo 'Ismeretlen kamera: {name}' >&2; false"
    p = paths(cam["name"])
    v = {
        "CAM": cam["name"], "CAM_TYPE": cam["type"],
        "FRAME_DIR": p["frame_dir"], "ACTIVE_FLAG": p["active_flag"],
        "SESSION_CONF": p["session_conf"], "LAST_CAPTURE_FILE": p["last_capture"],
        "CAPTURE_LOCK": p["capture_lock"], "LAST_SESSION_JSON": p["last_session"],
        "CAPTURE_START": cam["start"], "CAPTURE_END": cam["end"],
        "CAPTURE_INTERVAL": cam["interval"], "CAPTURE_RESOLUTION": cam["capture_resolution"],
        "JPEG_QUALITY": cam["jpeg_quality"], "CAMERA_SKIP": cam["camera_skip"],
        "OVERLAY": _bool(cam["overlay"]), "FONT_SIZE": cam["font_size"],
        "RESOLUTION": cam["video_resolution"], "FFMPEG_FPS": cam["fps"],
        "FFMPEG_CRF": cam["crf"], "FFMPEG_PRESET": cam["preset"],
    }
    if cam["type"] == "usb":
        v["CAMERA_DEVICE_PREFERRED"] = cam.get("device", "/dev/video0")
        v["CAMERA_NAME_MATCH"] = cam.get("match", "C920")
    else:
        v["RTSP_URL_MAIN"] = rtsp_url(cam, "main")
        v["RTSP_URL_SUB"] = rtsp_url(cam, "sub")
        v["CAM_HOST"] = cam.get("host", "")
    return "\n".join(f"{k}={shlex.quote(str(x))}" for k, x in v.items())


# ── capture failure tracking (journal + email) ───────────────────────────────

def capture_result(name: str, ok: bool, message: str = ""):
    """Called after every capture of an IP camera. After FAIL_ALERT_AFTER consecutive
    failures an entry goes into the error journal and an email is queued; a success
    marks the entry as cleared (the entry itself stays until acknowledged)."""
    lock = open(f"{TMP}/timelapse_capture_failures.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(FAIL_STATE_FILE) as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        prev = st.get(name, 0)
        n = 0 if ok else prev + 1
        if prev != n:
            st[name] = n
            tmp = FAIL_STATE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(st, f)
            os.replace(tmp, FAIL_STATE_FILE)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()

    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    import unit_errors
    if ok:
        if prev >= FAIL_ALERT_AFTER:
            unit_errors.mark_cleared(name, {"camera_error"}, set())
        return
    if n < FAIL_ALERT_AFTER:
        return
    msg = f"A képrögzítés {n} egymás utáni próbálkozásra sikertelen" + (f": {message}" if message else "")
    entry, is_new = unit_errors.report(f"camera|{name}|capture", name, "", "camera_error", msg,
                                       [message] if message else [], state_like=True)
    if is_new:
        import alert_lib
        alert_lib.enqueue("camera_error", f"{name}: a kamera képrögzítése sikertelen",
                          f"Kamera: {name}\n{msg}\nIdőpont: {alert_lib.fmt_ts(entry['first_ts'])}\n\n"
                          f"Ugyanez a hiba nem kerül újra elküldésre, amíg a webes felület Hibanapló "
                          f"részében nem nyugtázod. Rendszer: {os.uname().nodename}")


# ── one-time migration of the single-camera layout ───────────────────────────

def _move(src: str, dst: str) -> bool:
    if os.path.exists(src) and not os.path.exists(dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.rename(src, dst)
        return True
    return False


def _legacy_session_values() -> dict:
    """start/end/interval of the running (or last) single-camera session."""
    vals = {}
    try:
        with open(f"{TMP}/timelapse_session.conf") as f:
            for line in f:
                m = re.match(r'^(CAPTURE_START|CAPTURE_END|CAPTURE_INTERVAL)="?([^"\n]+)"?', line)
                if m:
                    vals[{"CAPTURE_START": "start", "CAPTURE_END": "end",
                          "CAPTURE_INTERVAL": "interval"}[m.group(1)]] = m.group(2)
    except OSError:
        pass
    if not vals:
        try:
            with open(f"{TL_DIR}/last_session.json") as f:
                d = json.load(f)
            for src, dst in (("session_start", "start"), ("session_end", "end"),
                             ("session_interval", "interval")):
                if d.get(src):
                    vals[dst] = d[src]
        except (OSError, ValueError):
            pass
    if "interval" in vals:
        try:
            vals["interval"] = int(vals["interval"])
        except ValueError:
            del vals["interval"]
    return vals


def migrate() -> list:
    """Move the old single-camera files/folders to the USB camera's names. Idempotent:
    only things that exist in the old place and not yet in the new one are moved."""
    done = []
    cfg = load_config()
    cams = get_cameras(cfg)
    usb = next(c for c in cams if c["type"] == "usb")
    name = usb["name"]
    p = paths(name)

    if not cfg.get("cameras"):
        seed = _legacy_session_values()
        usb = normalize({"name": name, "type": "usb", "precapture": cfg.get("precapture") or {}}, seed)

        def add(c):
            c["cameras"] = [usb]
        update_config(add)
        done.append("config: cameras[] létrehozva")

    # runtime state (/tmp)
    if _move(f"{TMP}/timelapse_active", p["active_flag"]):
        done.append("active flag")
    if _move(f"{TMP}/timelapse_session.conf", p["session_conf"]):
        # the per-camera conf keeps only the session values; paths are derived
        try:
            with open(p["session_conf"]) as f:
                keep = [l for l in f if not l.startswith(("ARCHIVE_DIR=", "MASTER_VIDEO="))]
            with open(p["session_conf"], "w") as f:
                f.writelines(keep)
        except OSError:
            pass
        done.append("session conf")
    if _move(f"{TMP}/timelapse_last_capture", p["last_capture"]):
        done.append("last capture")
    # frames
    moved = 0
    try:
        for fn in os.listdir(FRAMES_ROOT):
            fp = os.path.join(FRAMES_ROOT, fn)
            if fn.endswith(".jpg") and os.path.isfile(fp):
                os.makedirs(p["frame_dir"], exist_ok=True)
                os.rename(fp, os.path.join(p["frame_dir"], fn))
                moved += 1
    except OSError:
        pass
    if moved:
        done.append(f"{moved} kép áthelyezve")
    # persistent session state
    if _move(f"{TL_DIR}/last_session.json", p["last_session"]):
        done.append("last_session.json")
    for old, new in ((f"{TL_DIR}/precapture_pending.json", f"{TL_DIR}/precapture_pending_{name}.json"),
                     (f"{TL_DIR}/precapture_alert.json", f"{TL_DIR}/precapture_alert_{name}.json"),
                     (f"{TMP}/precapture_status.json", f"{TMP}/precapture_status_{name}.json")):
        if _move(old, new):
            done.append(os.path.basename(old))
    # videos on every storage that has the old layout
    for base in VIDEO_BASES:
        arch = os.path.join(base, "archive")
        if os.path.isdir(arch):
            n = 0
            for fn in os.listdir(arch):
                if fn.endswith(".mp4") and _move(os.path.join(arch, fn), os.path.join(arch, name, fn)):
                    n += 1
            if n:
                done.append(f"{base}: {n} napi videó → archive/{name}/")
        if _move(os.path.join(base, "master.mp4"), os.path.join(base, f"{name}_master.mp4")):
            done.append(f"{base}: master.mp4 → {name}_master.mp4")
        rend = os.path.join(base, "renders")
        if os.path.isdir(rend):
            n = 0
            for fn in os.listdir(rend):
                if fn.endswith(".mp4") and _move(os.path.join(rend, fn), os.path.join(rend, name, fn)):
                    n += 1
            if n:
                done.append(f"{base}: {n} render → renders/{name}/")
    return done


# ── editing the camera list (used by the web UI) ─────────────────────────────

HOST_RE = re.compile(r"^[A-Za-z0-9.\-]{1,253}$")
USER_RE = re.compile(r"^[\w\-.@]{0,64}$")
URL_PLACEHOLDERS = ("{host}", "{port}", "{user}", "{password}")


def _ensure_usb(cfg: dict):
    """The USB camera is persisted in the list as soon as the list is edited, so a rename sticks."""
    raw = cfg.setdefault("cameras", [])
    if not any(isinstance(c, dict) and c.get("type", "usb") == "usb" for c in raw):
        raw.insert(0, normalize({"name": DEFAULT_USB_NAME, "type": "usb",
                                 "precapture": cfg.get("precapture") or {}}, _legacy_session_values()))


def _edit(fn):
    def wrapper(cfg):
        _ensure_usb(cfg)
        fn(cfg["cameras"])
    return update_config(wrapper)


def _validate_conn(d: dict, require_host: bool = True) -> tuple:
    """(clean connection fields, error). Only keys present in d are checked."""
    clean = {}
    if "host" in d or require_host:
        host = str(d.get("host", "")).strip()
        if not HOST_RE.match(host):
            return {}, "Érvénytelen IP cím / gépnév"
        clean["host"] = host
    if "port" in d:
        try:
            port = int(d["port"])
        except (TypeError, ValueError):
            return {}, "A port nem szám"
        if not 1 <= port <= 65535:
            return {}, "A port 1–65535 között lehet"
        clean["port"] = port
    if "user" in d:
        user = str(d["user"]).strip()
        if not USER_RE.match(user):
            return {}, "Érvénytelen felhasználónév"
        clean["user"] = user
    if d.get("password"):
        pw = str(d["password"])
        if len(pw) > 64 or any(ord(c) < 32 for c in pw):
            return {}, "Érvénytelen jelszó (legfeljebb 64 karakter, egy sor)"
        clean["password"] = pw
    for k in ("rtsp_main", "rtsp_sub"):
        if d.get(k):
            url = str(d[k]).strip()
            rest = url
            for ph in URL_PLACEHOLDERS:
                rest = rest.replace(ph, "")
            if not url.startswith("rtsp://") or len(url) > 300 or re.search(r"[\s{}]", rest):
                return {}, f"Érvénytelen {k} (rtsp://… cím, a {{host}} {{port}} {{user}} {{password}} helyettesítők használhatók)"
            clean[k] = url
    return clean, None


def add_ip_camera(data: dict) -> tuple:
    name = str(data.get("name", "")).strip()
    err = validate_name(name)
    if err:
        return False, err
    conn, err = _validate_conn(data)
    if err:
        return False, err
    entry = normalize(dict({"name": name, "type": "ip"}, **conn))
    _edit(lambda cams: cams.append(entry))
    return True, ""


def update_camera(name: str, data: dict) -> tuple:
    """Change connection fields, capture/video settings and/or the pre-capture command of
    a camera, and optionally rename it (data["new_name"])."""
    cam = get_camera(name)
    if cam is None:
        return False, "Ismeretlen kamera"
    changes = {}
    if cam["type"] == "ip":
        conn, err = _validate_conn(data, require_host=False)
        if err:
            return False, err
        changes.update(conn)
    settings, err = validate_settings(data.get("settings") or {})
    if err:
        return False, err
    changes.update(settings)
    if isinstance(data.get("precapture"), dict):
        changes["precapture"] = data["precapture"]
    new_name = str(data.get("new_name", name)).strip() or name
    if new_name != name:
        err = validate_name(new_name, ignore=name) or _rename_blocker(name)
        if err:
            return False, err

    def apply(cams):
        for c in cams:
            if isinstance(c, dict) and c.get("name") == name:
                c.update(changes)
                break

    _edit(apply)
    if os.path.exists(paths(name)["active_flag"]):
        _sync_session(name, settings)
    if new_name != name:
        _rename_files(name, new_name)

        def rename(cams):
            for c in cams:
                if isinstance(c, dict) and c.get("name") == name:
                    c["name"] = new_name
        _edit(rename)
    return True, ""


def remove_camera(name: str) -> tuple:
    cam = get_camera(name)
    if cam is None:
        return False, "Ismeretlen kamera"
    if cam["type"] == "usb":
        return False, "Az USB kamera nem törölhető (átnevezhető)"
    if os.path.exists(paths(name)["active_flag"]):
        return False, "A kamera timelapse-a fut, előbb állítsd le"
    _edit(lambda cams: cams.__setitem__(slice(None), [c for c in cams
                                                       if not (isinstance(c, dict) and c.get("name") == name)]))
    return True, ""        # frames and videos stay on disk


def _sync_session(name: str, settings: dict):
    """A running session picks up a changed window / interval at once."""
    p = paths(name)
    mapping = {"start": "CAPTURE_START", "end": "CAPTURE_END", "interval": "CAPTURE_INTERVAL"}
    wanted = {mapping[k]: str(v) for k, v in settings.items() if k in mapping}
    if not wanted:
        return
    try:
        with open(p["session_conf"]) as f:
            lines = f.read().splitlines()
        seen = set()
        for i, line in enumerate(lines):
            key = line.split("=", 1)[0]
            if key in wanted:
                lines[i] = f'{key}="{wanted[key]}"'
                seen.add(key)
        lines += [f'{k}="{v}"' for k, v in wanted.items() if k not in seen]
        with open(p["session_conf"], "w") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass
    try:
        with open(p["last_session"]) as f:
            d = json.load(f)
        for k, v in settings.items():
            if k in ("start", "end", "interval"):
                d["session_" + k] = str(v)
        tmp = p["last_session"] + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, p["last_session"])
    except (OSError, ValueError):
        pass


def _rename_blocker(name: str) -> str:
    if os.path.exists(paths(name)["active_flag"]):
        return "Átnevezés előtt állítsd le a kamera timelapse-át"
    lock = open(f"{TMP}/timelapse_compile.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return "Éppen összefűzés fut, próbáld később"
    finally:
        lock.close()
    return ""


def _rename_files(old: str, new: str):
    """Move everything that is named after the camera. The frames/videos stay on disk."""
    po, pn = paths(old), paths(new)
    for key in ("frame_dir", "last_session", "last_capture", "compile_attempts", "session_conf"):
        _move(po[key], pn[key])
    for old_f, new_f in ((f"{TL_DIR}/precapture_pending_{old}.json", f"{TL_DIR}/precapture_pending_{new}.json"),
                         (f"{TL_DIR}/precapture_alert_{old}.json", f"{TL_DIR}/precapture_alert_{new}.json"),
                         (f"{TMP}/precapture_status_{old}.json", f"{TMP}/precapture_status_{new}.json")):
        _move(old_f, new_f)
    for base in VIDEO_BASES:
        _move(os.path.join(base, "archive", old), os.path.join(base, "archive", new))
        _move(os.path.join(base, "renders", old), os.path.join(base, "renders", new))
        _move(os.path.join(base, f"{old}_master.mp4"), os.path.join(base, f"{new}_master.mp4"))



def restore_plan() -> tuple:
    """Which timelapses have to be restarted after a boot.
    Returns (source, entries): source is "pre_reboot" (planned reboot, pre_reboot_state.json),
    "last_session" (power loss: the cameras whose last_session file says active) or ""
    (nothing to restore). entries: [{name, start, end, interval, storage}] of cameras that
    were active."""
    def entry(name, d):
        return {"name": name, "start": str(d.get("session_start", "")), "end": str(d.get("session_end", "")),
                "interval": str(d.get("session_interval", "")), "storage": str(d.get("session_storage", ""))}

    known = {c["name"] for c in get_cameras()}
    try:
        with open(f"{TL_DIR}/pre_reboot_state.json") as f:
            pre = json.load(f)
    except (OSError, ValueError):
        pre = None
    if isinstance(pre, dict):
        if isinstance(pre.get("cameras"), dict):
            items = list(pre["cameras"].items())
        else:       # state written before multi-camera support: it was the USB camera
            items = [(default_name(), pre)]
        return "pre_reboot", [entry(n, d) for n, d in items
                              if n in known and isinstance(d, dict) and d.get("timelapse_active") in (True, "true", "True")]
    out = []
    for c in get_cameras():
        files = [paths(c["name"])["last_session"]]
        if c["type"] == "usb":
            files.append(f"{TL_DIR}/last_session.json")      # not migrated yet
        for fp in files:
            try:
                with open(fp) as f:
                    d = json.load(f)
            except (OSError, ValueError):
                continue
            if d.get("active") in (True, "true", "True"):
                out.append(entry(c["name"], d))
            break
    return ("last_session" if out else ""), out


def main(argv: list) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "env":
        out = shell_env(argv[1] if len(argv) > 1 else "")
        print(out)
        return 0 if not out.endswith("false") else 1
    if cmd == "names":
        for c in get_cameras():
            print(c["name"])
    elif cmd == "default":
        print(default_name())
    elif cmd == "restore-plan":
        source, entries = restore_plan()
        print(source)
        for e in entries:
            print(chr(9).join(x or "-" for x in (e["name"], e["start"], e["end"], e["interval"], e["storage"])))
    elif cmd == "migrate":
        for line in migrate():
            print("  " + line)
        print("kész")
    elif cmd == "capture-result" and len(argv) >= 3:
        capture_result(argv[1], argv[2] == "ok", " ".join(argv[3:]))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

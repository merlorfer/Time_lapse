#!/usr/bin/env python3
"""
Timelapse Web UI – Python stdlib only, no extra dependencies
Port: 8082
"""

import csv
import http.server
import json
import subprocess
import os
import glob
import re
import threading
from datetime import date, timedelta
from urllib.parse import urlparse, parse_qs

SCRIPT_DIR      = "/home/orangepi/timelapse/scripts"
LOG_DIR         = "/home/orangepi/timelapse/logs"
FRAME_DIR       = "/tmp/timelapse_frames"
SENSOR_RAM_DIR  = "/tmp/sensor_data"
SERIAL_LOG_FILE = "/tmp/esp32_serial.log"
PORT            = 8082
HOST            = "0.0.0.0"

ALLOWED_SCRIPTS = {
    "start_timelapse", "stop_timelapse",
    "preview_start",   "preview_stop",
    "render_now",      "compile_daily",
    "test_capture",    "test_compile",
}

# ── Parameter validators ────────────────────────────────────────────────────
_TIME = re.compile(r"^\d{2}:\d{2}$")
_INT  = re.compile(r"^\d+$")
_PATH = re.compile(r"^[/\w\-_.]+$")

def build_args(script: str, params: dict) -> list[str]:
    args = []
    if script == "start_timelapse":
        if "start"    in params and _TIME.match(params["start"]):
            args += ["--start", params["start"]]
        if "end"      in params and _TIME.match(params["end"]):
            args += ["--end",   params["end"]]
        if "interval" in params and _INT.match(str(params["interval"])):
            args += ["--interval", str(params["interval"])]
        if "storage"  in params and params["storage"] and _PATH.match(params["storage"]):
            args += ["--storage", params["storage"]]
    elif script == "render_now":
        if "output" in params and params["output"] and _PATH.match(params["output"]):
            args += ["--output", params["output"]]
    return args

# ── Video base helper ────────────────────────────────────────────────────────
def get_video_base() -> str:
    # Check session override first
    session_conf = "/tmp/timelapse_session.conf"
    if os.path.isfile(session_conf):
        with open(session_conf) as f:
            for line in f:
                if line.startswith("VIDEO_BASE="):
                    val = line.strip().split("=", 1)[1].strip('"\'')
                    if val:
                        return val
    # Auto-detect: USB pendrive preferred, SD fallback
    usb = "/mnt/timelapse"
    if os.path.ismount(usb) and os.path.isdir(usb):
        try:
            testfile = os.path.join(usb, ".write_test")
            with open(testfile, "w") as f:
                f.write("x")
            os.unlink(testfile)
            return usb
        except OSError:
            pass
    return "/home/orangepi/timelapse/videos"

def get_videos() -> dict:
    video_base   = get_video_base()
    archive_dir  = os.path.join(video_base, "archive")
    renders_dir  = os.path.join(video_base, "renders")

    def list_dir(directory: str, pattern: str) -> list:
        videos = []
        if not os.path.isdir(directory):
            return videos
        for name in sorted(glob.glob(os.path.join(directory, pattern)), reverse=True):
            try:
                st = os.stat(name)
                videos.append({
                    "name":  os.path.basename(name),
                    "size":  st.st_size,
                    "mtime": int(st.st_mtime),
                })
            except Exception:
                pass
        return videos

    # master.mp4 külön
    master = None
    master_path = os.path.join(video_base, "master.mp4")
    if os.path.isfile(master_path):
        try:
            st = os.stat(master_path)
            master = {"name": "master.mp4", "size": st.st_size, "mtime": int(st.st_mtime)}
        except Exception:
            pass

    return {
        "archive": list_dir(archive_dir, "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].mp4"),
        "renders": list_dir(renders_dir, "*.mp4"),
        "master":  master,
    }

# ── Sensor data helpers ───────────────────────────────────────────────────────

def get_sensor_archive_dir() -> str:
    return os.path.join(get_video_base(), "sensor_data")

def get_sensor_day_dir(day: str) -> str:
    """Return path to sensor data for a given YYYY-MM-DD, checking ramdisk then archive."""
    ram = os.path.join(SENSOR_RAM_DIR, day)
    if os.path.isdir(ram):
        return ram
    return os.path.join(get_sensor_archive_dir(), day)

def list_sensor_dates() -> list:
    dates = set()
    for base in [SENSOR_RAM_DIR, get_sensor_archive_dir()]:
        if os.path.isdir(base):
            for d in os.listdir(base):
                if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
                    dates.add(d)
    return sorted(dates, reverse=True)

def read_sensor_csv(filepath: str) -> list:
    rows = []
    try:
        with open(filepath, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
    except Exception:
        pass
    return rows

def get_sensor_data(from_date: str, to_date: str) -> dict:
    """Return sensor readings for a date range. {sensor_name: [rows]}"""
    result = {}
    try:
        d_from = date.fromisoformat(from_date)
        d_to   = date.fromisoformat(to_date)
    except ValueError:
        return result
    cur = d_from
    while cur <= d_to:
        day_str = cur.isoformat()
        day_dir = get_sensor_day_dir(day_str)
        if os.path.isdir(day_dir):
            for csv_file in glob.glob(os.path.join(day_dir, "*.csv")):
                name = os.path.splitext(os.path.basename(csv_file))[0]
                rows = read_sensor_csv(csv_file)
                if name not in result:
                    result[name] = []
                result[name].extend(rows)
        cur += timedelta(days=1)
    return result


# ── Status helper ────────────────────────────────────────────────────────────
def get_status() -> dict:
    # Timelapse active?
    tl_active  = os.path.exists("/tmp/timelapse_active")
    started_at = ""
    if tl_active:
        try:
            with open("/tmp/timelapse_active") as f:
                started_at = f.read().strip()
        except Exception:
            pass

    # Preview active?
    preview_active = False
    pidfile = "/tmp/timelapse_preview.pid"
    if os.path.exists(pidfile):
        try:
            with open(pidfile) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            preview_active = True
        except Exception:
            pass

    # Frame count
    frame_count = len(glob.glob(f"{FRAME_DIR}/*.jpg"))

    # Disk usage (SD root)
    disk_sd = {}
    try:
        r = subprocess.run(["df", "-h", "/"], capture_output=True, text=True)
        p = r.stdout.strip().split("\n")[1].split()
        disk_sd = {"total": p[1], "used": p[2], "free": p[3], "pct": p[4]}
    except Exception:
        pass

    # USB storage
    usb_ok = os.path.ismount("/mnt/timelapse")
    disk_usb = {}
    if usb_ok:
        try:
            r = subprocess.run(["df", "-h", "/mnt/timelapse"], capture_output=True, text=True)
            p = r.stdout.strip().split("\n")[1].split()
            disk_usb = {"total": p[1], "used": p[2], "free": p[3], "pct": p[4]}
        except Exception:
            pass

    return {
        "timelapse_active": tl_active,
        "started_at":       started_at,
        "preview_active":   preview_active,
        "frame_count":      frame_count,
        "disk_sd":          disk_sd,
        "usb_ok":           usb_ok,
        "disk_usb":         disk_usb,
    }

# ── HTTP handler ─────────────────────────────────────────────────────────────
class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, *_):
        pass  # silence access log

    # ── GET ──────────────────────────────────────────────────────────────────
    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        if path in ("/", "/index.html"):
            self._serve_static("index.html", "text/html; charset=utf-8")
        elif path == "/api/status":
            self._send_json(get_status())
        elif path == "/api/videos":
            self._send_json(get_videos())
        elif path == "/api/logs":
            qs       = parse_qs(parsed.query)
            log_type = qs.get("type", ["capture"])[0]
            lines    = min(int(qs.get("lines", ["200"])[0]), 500)
            self._serve_logs(log_type, lines)
        elif path == "/api/serial-log":
            qs    = parse_qs(parsed.query)
            lines = min(int(qs.get("lines", ["200"])[0]), 1000)
            self._serve_serial_log(lines)
        elif path == "/api/sensor-dates":
            self._send_json({"dates": list_sensor_dates()})
        elif path == "/api/sensor-data":
            qs      = parse_qs(parsed.query)
            today   = date.today().isoformat()
            f_date  = qs.get("from", [today])[0]
            t_date  = qs.get("to",   [today])[0]
            self._send_json(get_sensor_data(f_date, t_date))
        else:
            self.send_error(404)

    # ── POST ─────────────────────────────────────────────────────────────────
    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/run":
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length)) if length else {}
            self._run_script(body.get("script", ""), body.get("params", {}))
        else:
            self.send_error(404)

    # ── helpers ──────────────────────────────────────────────────────────────
    def _serve_static(self, filename: str, mime: str):
        p = os.path.join(os.path.dirname(__file__), filename)
        try:
            with open(p, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", len(data))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404)

    def _send_json(self, data: dict):
        content = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(content))
        self.end_headers()
        self.wfile.write(content)

    def _serve_serial_log(self, lines: int):
        try:
            r = subprocess.run(["tail", f"-{lines}", SERIAL_LOG_FILE],
                               capture_output=True, text=True, errors="replace")
            available = os.path.isfile(SERIAL_LOG_FILE)
            self._send_json({"available": available, "lines": r.stdout.splitlines()})
        except Exception:
            self._send_json({"available": False, "lines": []})

    def _serve_logs(self, log_type: str, lines: int):
        if log_type not in ("capture", "compile"):
            self.send_error(400)
            return
        log_file = os.path.join(LOG_DIR, f"{log_type}.log")
        try:
            r = subprocess.run(["tail", f"-{lines}", log_file],
                               capture_output=True, text=True)
            self._send_json({"lines": r.stdout.splitlines()})
        except Exception:
            self._send_json({"lines": []})

    def _run_script(self, script: str, params: dict):
        if script not in ALLOWED_SCRIPTS:
            self._send_json({"ok": False, "output": f"Ismeretlen script: {script}"})
            return

        script_path = os.path.join(SCRIPT_DIR, f"{script}.sh")
        if not os.path.isfile(script_path):
            self._send_json({"ok": False, "output": f"Script nem található: {script_path}"})
            return

        args = build_args(script, params)
        cmd  = ["bash", script_path] + args

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            self._send_json({
                "ok":     result.returncode == 0,
                "output": (result.stdout + result.stderr).strip(),
            })
        except subprocess.TimeoutExpired:
            self._send_json({"ok": False, "output": "Timeout (300 mp)"})
        except Exception as e:
            self._send_json({"ok": False, "output": str(e)})


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Timelapse Web UI → http://{HOST}:{PORT}")
    server.serve_forever()

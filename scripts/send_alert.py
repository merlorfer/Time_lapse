#!/usr/bin/env python3
"""
send_alert.py – Email értesítés timelapse hibákhoz
Használat: python3 send_alert.py "Tárgy" "Üzenet törzse"
"""

import json
import smtplib
import sys
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

CONFIG_FILE = "/home/orangepi/timelapse/timelapse_config.json"
LOG_FILE    = "/home/orangepi/timelapse/logs/alert.log"

def log(msg: str):
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

def load_config() -> dict:
    defaults = {
        "email_enabled": False,
        "email_to": "",
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_user": "",
        "smtp_password": "",
    }
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        defaults.update(cfg)
    except Exception:
        pass
    return defaults

def send_email(subject: str, body: str) -> bool:
    cfg = load_config()

    if not cfg.get("email_enabled"):
        return True

    to_addr   = cfg.get("email_to", "").strip()
    smtp_srv  = cfg.get("smtp_server", "smtp.gmail.com")
    smtp_port = int(cfg.get("smtp_port", 587))
    smtp_user = cfg.get("smtp_user", "").strip()
    smtp_pass = cfg.get("smtp_password", "").strip()

    if not all([to_addr, smtp_user, smtp_pass]):
        log("ERROR: email config hiányos (to/user/password), küldés kihagyva")
        return False

    hostname = os.uname().nodename if hasattr(os, "uname") else "timelapse"
    full_subject = f"[{hostname}] {subject}"

    msg = MIMEMultipart()
    msg["From"]    = smtp_user
    msg["To"]      = to_addr
    msg["Subject"] = full_subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP(smtp_srv, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_addr, msg.as_string())
        log(f"OK: email elküldve → {to_addr} | {full_subject}")
        return True
    except Exception as e:
        log(f"ERROR: email küldés sikertelen: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Használat: {sys.argv[0]} \"Tárgy\" \"Üzenet\"", file=sys.stderr)
        sys.exit(1)
    subject = sys.argv[1]
    body    = sys.argv[2]
    ok = send_email(subject, body)
    sys.exit(0 if ok else 1)

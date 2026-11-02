#!/bin/bash
# =============================================================================
# safe_reboot.sh – Adatok mentése, majd újraindítás
# Menti a ramdisken lévő képkockákat (kamerákra külön) és a szenzor adatokat, majd rebootol.
# SAFE_ACTION=poweroff környezeti változóval leállít (lásd safe_shutdown.sh).
# =============================================================================

SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/config.sh"

ACTION="${SAFE_ACTION:-reboot}"   # reboot | poweroff
LOG="${LOG_DIR}/safe_reboot.log"
mkdir -p "$LOG_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }

log "=== safe_reboot.sh indítva ==="
load_camera "" || exit 1
BASE_MAIN="$VIDEO_BASE"        # a szenzor adatok és a naplók helye (az USB kamera munkamenetéé)
log "VIDEO_BASE: $BASE_MAIN"

CAMERAS=$(python3 "$SCRIPT_DIR/cameras.py" names)

# ── 1. Képkockák mentése (kameránként) ───────────────────────────────────────
for CAMN in $CAMERAS; do
    load_camera "$CAMN" || continue
    FRAME_COUNT=$(ls "$FRAME_DIR/"*.jpg 2>/dev/null | wc -l)
    if [ "$FRAME_COUNT" -gt 0 ]; then
        BACKUP_DIR="${VIDEO_BASE}/frames_backup_${CAM}_$(date '+%Y-%m-%d_%H%M%S')"
        mkdir -p "$BACKUP_DIR"
        cp "$FRAME_DIR/"*.jpg "$BACKUP_DIR/"
        COPIED=$(ls "$BACKUP_DIR/"*.jpg 2>/dev/null | wc -l)
        log "[$CAM] Képkockák mentve: $COPIED / $FRAME_COUNT → $BACKUP_DIR"
    else
        log "[$CAM] Nincs képkocka a ramdisken – kihagyva"
    fi
done

# ── 2. Szenzor adatok mentése ────────────────────────────────────────────────
SENSOR_RAM_DIR="/tmp/sensor_data"
SENSOR_ARCHIVE_DIR="$BASE_MAIN/sensor_data"

if [ -d "$SENSOR_RAM_DIR" ]; then
    for DAY_DIR in "$SENSOR_RAM_DIR"/*/; do
        DAY=$(basename "$DAY_DIR")
        CSV_COUNT=$(ls "$DAY_DIR"*.csv 2>/dev/null | wc -l)
        if [ "$CSV_COUNT" -gt 0 ]; then
            mkdir -p "$SENSOR_ARCHIVE_DIR/$DAY"
            cp "$DAY_DIR"*.csv "$SENSOR_ARCHIVE_DIR/$DAY/"
            log "Szenzor adatok mentve: $DAY ($CSV_COUNT fájl) → $SENSOR_ARCHIVE_DIR/$DAY/"
        fi
    done
else
    log "Nincs szenzor adat – kihagyva"
fi

# ── 3. Session confok mentése ────────────────────────────────────────────────
for CAMN in $CAMERAS; do
    load_camera "$CAMN" nosession || continue
    if [ -f "$SESSION_CONF" ]; then
        cp "$SESSION_CONF" "$BASE_MAIN/timelapse_session_backup_${CAM}.conf"
        log "[$CAM] Session conf mentve → $BASE_MAIN/timelapse_session_backup_${CAM}.conf"
    fi
done

# ── 4. Pre-reboot state mentése visszaállításhoz (kameránként) ───────────────
PRE_REBOOT_STATE="/home/orangepi/timelapse/pre_reboot_state.json"
REBOOT_REASON="$([ "$ACTION" = poweroff ] && echo shutdown || echo scheduled)"

SCRIPT_DIR="$SCRIPT_DIR" PRE_REBOOT_STATE="$PRE_REBOOT_STATE" REBOOT_REASON="$REBOOT_REASON" python3 - <<'PYEOF'
import json, os, re, sys
from datetime import datetime

sys.path.insert(0, os.environ["SCRIPT_DIR"])
import cameras

keys = {"CAPTURE_START": "session_start", "CAPTURE_END": "session_end",
        "CAPTURE_INTERVAL": "session_interval", "VIDEO_BASE": "session_storage"}
state = {"cameras": {}, "reboot_time": datetime.now().astimezone().isoformat(timespec="seconds"),
         "reboot_reason": os.environ["REBOOT_REASON"]}
for cam in cameras.get_cameras():
    p = cameras.paths(cam["name"])
    entry = {"timelapse_active": os.path.exists(p["active_flag"]),
             "session_start": "", "session_end": "", "session_interval": "", "session_storage": ""}
    try:
        with open(p["session_conf"]) as f:
            for line in f:
                m = re.match(r'^([A-Z_]+)="?([^"\n]*)"?', line)
                if m and m.group(1) in keys:
                    entry[keys[m.group(1)]] = m.group(2)
    except OSError:
        pass
    state["cameras"][cam["name"]] = entry
tmp = os.environ["PRE_REBOOT_STATE"] + ".tmp"
with open(tmp, "w") as f:
    json.dump(state, f, indent=2)
os.replace(tmp, os.environ["PRE_REBOOT_STATE"])
active = [n for n, e in state["cameras"].items() if e["timelapse_active"]]
print("aktív kamerák: " + (", ".join(active) if active else "nincs"))
PYEOF
log "Pre-reboot state mentve → $PRE_REBOOT_STATE"

# Értesítés a leállítás/újraindítás előtt: ha van internet, kimegy még előtte (max. 20 mp várakozás),
# egyébként a sorban marad, és a következő bootnál, az eredeti időponttal megy ki.
if [ "$ACTION" = "poweroff" ]; then
    python3 "$SCRIPT_DIR/send_alert.py" --wait 20 reboot_shutdown "Leállítás indul" \
        "Biztonságos leállítás indult, az Orange Pi a mentés után leáll. Rendszer: $(hostname)" >> "$LOG" 2>&1 || true
else
    python3 "$SCRIPT_DIR/send_alert.py" --wait 20 reboot_shutdown "Újraindítás indul" \
        "Biztonságos újraindítás indult, az Orange Pi a mentés után újraindul. Rendszer: $(hostname)" >> "$LOG" 2>&1 || true
fi

if [ "$ACTION" = "poweroff" ]; then
    log "=== Mentés kész, leállítás... ==="
    sync
    sudo systemctl poweroff
else
    log "=== Mentés kész, újraindítás... ==="
    sudo reboot
fi

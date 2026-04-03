#!/bin/bash
# =============================================================================
# safe_reboot.sh – Adatok mentése, majd újraindítás
# Menti a ramdisken lévő képkockákat és szenzor adatokat, majd rebootol.
# =============================================================================

SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/config.sh"
[ -f /tmp/timelapse_session.conf ] && source /tmp/timelapse_session.conf

LOG="${LOG_DIR}/safe_reboot.log"
mkdir -p "$LOG_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }

log "=== safe_reboot.sh indítva ==="
log "VIDEO_BASE: $VIDEO_BASE"

# ── 1. Képkockák mentése ─────────────────────────────────────────────────────
FRAME_COUNT=$(ls "$FRAME_DIR/"*.jpg 2>/dev/null | wc -l)
if [ "$FRAME_COUNT" -gt 0 ]; then
    BACKUP_DIR="${VIDEO_BASE}/frames_backup_$(date '+%Y-%m-%d_%H%M%S')"
    mkdir -p "$BACKUP_DIR"
    cp "$FRAME_DIR/"*.jpg "$BACKUP_DIR/"
    COPIED=$(ls "$BACKUP_DIR/"*.jpg 2>/dev/null | wc -l)
    log "Képkockák mentve: $COPIED / $FRAME_COUNT → $BACKUP_DIR"
else
    log "Nincs képkocka a ramdisken – kihagyva"
fi

# ── 2. Szenzor adatok mentése ────────────────────────────────────────────────
SENSOR_RAM_DIR="/tmp/sensor_data"
SENSOR_ARCHIVE_DIR="$VIDEO_BASE/sensor_data"

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

# ── 3. Session conf mentése ──────────────────────────────────────────────────
if [ -f /tmp/timelapse_session.conf ]; then
    cp /tmp/timelapse_session.conf "$VIDEO_BASE/timelapse_session_backup.conf"
    log "Session conf mentve → $VIDEO_BASE/timelapse_session_backup.conf"
fi

log "=== Mentés kész, újraindítás... ==="
sudo reboot

#!/bin/bash
# =============================================================================
# capture.sh – Egyetlen képkocka rögzítése
# Cron: * * * * * orangepi /home/orangepi/timelapse/scripts/capture.sh
# =============================================================================

source "$(dirname "$0")/config.sh"
[ -f /tmp/timelapse_session.conf ] && source /tmp/timelapse_session.conf

LOG="${LOG_DIR}/capture.log"
mkdir -p "$FRAME_DIR" "$LOG_DIR"

# --- Manuális indítás ellenőrzés (flag fájl) ---
if [ ! -f /tmp/timelapse_active ]; then
    exit 0
fi

# --- Időablak ellenőrzés ---
NOW=$(date +%H:%M)
if [[ "$NOW" < "$CAPTURE_START" || "$NOW" > "$CAPTURE_END" ]]; then
    exit 0
fi

# --- Intervallum ellenőrzés ---
LAST_CAPTURE_FILE="/tmp/timelapse_last_capture"
if [ -f "$LAST_CAPTURE_FILE" ]; then
    LAST_TS=$(cat "$LAST_CAPTURE_FILE")
    NOW_TS=$(date +%s)
    ELAPSED=$(( NOW_TS - LAST_TS ))
    if [ "$ELAPSED" -lt "$CAPTURE_INTERVAL" ]; then
        exit 0
    fi
fi

# --- Ramdisk szabad hely ellenőrzés ---
FREE_MB=$(df -m "$FRAME_DIR" | awk 'NR==2 {print $4}')
if [ "$FREE_MB" -lt "$RAMDISK_MIN_FREE_MB" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') WARNING: Ramdisk low space (${FREE_MB}MB free), skipping capture" >> "$LOG"
    exit 1
fi

# --- Képrögzítés ---
TIMESTAMP=$(date '+%Y-%m-%d_%H%M%S')
OUTPUT="${FRAME_DIR}/${TIMESTAMP}.jpg"

# Ha a preview fut, ustreamer /snapshot endpointból vesszük a képet (kamera megosztva),
# egyébként fswebcam nyúl a kamerához közvetlenül.
PREVIEW_PIDFILE="/tmp/timelapse_preview.pid"
PREVIEW_RUNNING=false
if [ -f "$PREVIEW_PIDFILE" ] && kill -0 "$(cat "$PREVIEW_PIDFILE")" 2>/dev/null; then
    PREVIEW_RUNNING=true
fi

if $PREVIEW_RUNNING; then
    curl -sf "http://localhost:8081/snapshot" -o "$OUTPUT" 2>> "$LOG"
    CAPTURE_OK=$?
    METHOD="ustreamer snapshot"
else
    fswebcam \
        --device "$CAMERA_DEVICE" \
        --resolution "$RESOLUTION" \
        --skip "$CAMERA_SKIP" \
        --jpeg "$JPEG_QUALITY" \
        --top-banner \
        --banner-colour "#00000000" \
        --font "${FONT_FILE}:${FONT_SIZE}" \
        --subtitle "$(date '+%Y-%m-%d %H:%M:%S')" \
        --no-timestamp \
        "$OUTPUT" 2>> "$LOG"
    CAPTURE_OK=$?
    METHOD="fswebcam"
fi

if [ $CAPTURE_OK -eq 0 ]; then
    date +%s > "$LAST_CAPTURE_FILE"
    echo "$(date '+%Y-%m-%d %H:%M:%S') Captured: $OUTPUT [${METHOD}] (${FREE_MB}MB free)" >> "$LOG"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') ERROR: capture failed [${METHOD}]" >> "$LOG"
    exit 1
fi

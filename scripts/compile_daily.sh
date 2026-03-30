#!/bin/bash
# =============================================================================
# compile_daily.sh – Napi timelapse videó + master concat
# Cron: 5 0 * * * orangepi /home/orangepi/timelapse/scripts/compile_daily.sh
# =============================================================================

source "$(dirname "$0")/config.sh"
[ -f /tmp/timelapse_session.conf ] && source /tmp/timelapse_session.conf

LOG="${LOG_DIR}/compile.log"
mkdir -p "$LOG_DIR" "$ARCHIVE_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

# Külső tároló állapot logolása
if [ "$VIDEO_BASE" = "/home/orangepi/timelapse/videos" ]; then
    log "WARNING: External storage not available, falling back to SD card"
fi

# --- Az előző nap dátuma (explicit számítás, DST-biztos) ---
YESTERDAY=$(date -d "$(date '+%Y-%m-%d') - 1 day" '+%Y-%m-%d')
DAILY_VIDEO="${ARCHIVE_DIR}/${YESTERDAY}.mp4"

log "=== Compiling daily video for ${YESTERDAY} ==="

# --- Frame lista összeállítása (csak az előző nap képei) ---
FRAME_LIST=$(mktemp)
ls "${FRAME_DIR}/${YESTERDAY}_"*.jpg 2>/dev/null | sort > "$FRAME_LIST"

FRAME_COUNT=$(wc -l < "$FRAME_LIST")
if [ "$FRAME_COUNT" -eq 0 ]; then
    log "No frames found for ${YESTERDAY}, skipping"
    rm -f "$FRAME_LIST"
    exit 0
fi
log "Found ${FRAME_COUNT} frames"

# --- FFmpeg input fájllista formátum ---
FFMPEG_INPUT=$(mktemp)
while IFS= read -r f; do
    echo "file '$f'" >> "$FFMPEG_INPUT"
done < "$FRAME_LIST"
rm -f "$FRAME_LIST"

# --- Napi videó generálása ---
ffmpeg -y \
    -f concat -safe 0 -i "$FFMPEG_INPUT" \
    -framerate "$FFMPEG_FPS" \
    -vf "scale=${RESOLUTION},format=yuv420p" \
    -c:v libx264 \
    -crf "$FFMPEG_CRF" \
    -preset "$FFMPEG_PRESET" \
    "$DAILY_VIDEO" \
    >> "$LOG" 2>&1

rm -f "$FFMPEG_INPUT"

if [ $? -ne 0 ]; then
    log "ERROR: ffmpeg failed to create daily video"
    exit 1
fi

DAILY_SIZE=$(du -sh "$DAILY_VIDEO" | cut -f1)
log "Daily video created: $DAILY_VIDEO (${DAILY_SIZE})"

# --- Framek törlése ramdiskről ---
DELETED=$(ls "${FRAME_DIR}/${YESTERDAY}_"*.jpg 2>/dev/null | wc -l)
rm -f "${FRAME_DIR}/${YESTERDAY}_"*.jpg
log "Deleted ${DELETED} frames from ramdisk"

# --- Master videó újraépítése archívból ---
log "Rebuilding master.mp4 from archive..."

MASTER_LIST=$(mktemp)
ls "${ARCHIVE_DIR}/"[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].mp4 2>/dev/null | sort | while IFS= read -r f; do
    echo "file '$f'"
done > "$MASTER_LIST"

SEGMENT_COUNT=$(wc -l < "$MASTER_LIST")
if [ "$SEGMENT_COUNT" -eq 0 ]; then
    log "No archive segments found, skipping master rebuild"
    rm -f "$MASTER_LIST"
    exit 0
fi

MASTER_TMP="${VIDEO_BASE}/master_tmp.mp4"

ffmpeg -y \
    -f concat -safe 0 -i "$MASTER_LIST" \
    -c copy \
    "$MASTER_TMP" \
    >> "$LOG" 2>&1

rm -f "$MASTER_LIST"

if [ $? -ne 0 ]; then
    log "ERROR: ffmpeg failed to rebuild master"
    exit 1
fi

mv -f "$MASTER_TMP" "$MASTER_VIDEO"
MASTER_SIZE=$(du -sh "$MASTER_VIDEO" | cut -f1)
log "Master rebuilt: $MASTER_VIDEO (${MASTER_SIZE}, ${SEGMENT_COUNT} segments)"
log "=== Done ==="

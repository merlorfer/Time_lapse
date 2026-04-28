#!/bin/bash
# =============================================================================
# compile_daily.sh – Napi timelapse videó + master concat
# Cron: 5 0 * * * orangepi /home/orangepi/timelapse/scripts/compile_daily.sh
# =============================================================================

source "$(dirname "$0")/config.sh"
[ -f /tmp/timelapse_session.conf ] && source /tmp/timelapse_session.conf

LOG="${LOG_DIR}/compile.log"
mkdir -p "$LOG_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

send_alert() {
    python3 "${SCRIPT_DIR}/send_alert.py" "$1" "$2" >> "$LOG" 2>&1 || true
}

# SD fallback útvonalak (mindig elérhetők)
SD_BASE="/home/orangepi/timelapse/videos"
SD_ARCHIVE="${SD_BASE}/archive"

YESTERDAY=$(date -d "yesterday" '+%Y-%m-%d')

log "=== Compiling daily video for ${YESTERDAY} ==="

# ── USB állapot ellenőrzés ────────────────────────────────────────────────────
USB_MOUNT="/mnt/timelapse"
USB_OK=false
if mountpoint -q "$USB_MOUNT" && [ -w "$USB_MOUNT" ]; then
    USB_OK=true
    log "USB rendben: $USB_MOUNT"
else
    log "WARNING: USB nem elérhető ($USB_MOUNT), SD fallbackre vált"
fi

# ── Frame lista összeállítása ─────────────────────────────────────────────────
FRAME_LIST=$(mktemp)
ls "${FRAME_DIR}/${YESTERDAY}_"*.jpg 2>/dev/null | sort > "$FRAME_LIST"

FRAME_COUNT=$(wc -l < "$FRAME_LIST")
if [ "$FRAME_COUNT" -eq 0 ]; then
    log "No frames found for ${YESTERDAY}, skipping"
    rm -f "$FRAME_LIST"
    exit 0
fi
log "Found ${FRAME_COUNT} frames"

# ── FFmpeg input fájllista ────────────────────────────────────────────────────
FFMPEG_INPUT=$(mktemp)
while IFS= read -r f; do
    echo "file '$f'" >> "$FFMPEG_INPUT"
done < "$FRAME_LIST"
rm -f "$FRAME_LIST"

# ── Napi videó generálása RAMDISKRE ──────────────────────────────────────────
TMP_DAILY="/tmp/timelapse_compile_${YESTERDAY}.mp4"

log "Renderelés ramdiskre: $TMP_DAILY"
ffmpeg -y \
    -f concat -safe 0 -i "$FFMPEG_INPUT" \
    -framerate "$FFMPEG_FPS" \
    -vf "scale=${RESOLUTION},format=yuv420p" \
    -c:v libx264 \
    -crf "$FFMPEG_CRF" \
    -preset "$FFMPEG_PRESET" \
    "$TMP_DAILY" \
    >> "$LOG" 2>&1

rm -f "$FFMPEG_INPUT"

if [ $? -ne 0 ] || [ ! -f "$TMP_DAILY" ]; then
    log "ERROR: ffmpeg sikertelen, napi videó nem készült el"
    rm -f "$TMP_DAILY"
    exit 1
fi

TMP_SIZE=$(stat -c%s "$TMP_DAILY" 2>/dev/null || echo 0)
if [ "$TMP_SIZE" -lt 10240 ]; then
    log "ERROR: renderelt fájl túl kicsi (${TMP_SIZE} bájt), valószínűleg hibás"
    rm -f "$TMP_DAILY"
    send_alert "Timelapse render hiba" \
        "A(z) ${YESTERDAY} napi videó renderelése sikertelen (fájlméret: ${TMP_SIZE} bájt). Rendszer: $(hostname)"
    exit 1
fi
log "Render kész: $(du -sh "$TMP_DAILY" | cut -f1)"

# ── Napi videó kimásolása a célhelyre ────────────────────────────────────────
DEST_DAILY=""

if [ "$USB_OK" = true ]; then
    USB_ARCHIVE="${USB_MOUNT}/archive"
    mkdir -p "$USB_ARCHIVE"
    DEST_DAILY="${USB_ARCHIVE}/${YESTERDAY}.mp4"

    log "Másolás USB-re: $DEST_DAILY"
    cp "$TMP_DAILY" "$DEST_DAILY" 2>> "$LOG"

    # Méret-egyezés ellenőrzés
    DEST_SIZE=$(stat -c%s "$DEST_DAILY" 2>/dev/null || echo 0)
    if [ "$DEST_SIZE" -eq "$TMP_SIZE" ] && [ "$DEST_SIZE" -gt 0 ]; then
        log "USB mentés OK: $DEST_DAILY ($(du -sh "$DEST_DAILY" | cut -f1))"
        rm -f "$TMP_DAILY"
    else
        log "ERROR: USB fájl mérete eltér (forrás: ${TMP_SIZE}, cél: ${DEST_SIZE}), SD fallback"
        rm -f "$DEST_DAILY"
        USB_OK=false
    fi
fi

# SD fallback, ha USB sikertelen vagy nem elérhető
if [ "$USB_OK" = false ]; then
    mkdir -p "$SD_ARCHIVE"
    DEST_DAILY="${SD_ARCHIVE}/${YESTERDAY}.mp4"

    log "Mentés SD kártyára: $DEST_DAILY"
    cp "$TMP_DAILY" "$DEST_DAILY" 2>> "$LOG"
    rm -f "$TMP_DAILY"

    SD_SIZE=$(stat -c%s "$DEST_DAILY" 2>/dev/null || echo 0)
    if [ "$SD_SIZE" -eq "$TMP_SIZE" ] && [ "$SD_SIZE" -gt 0 ]; then
        log "SD mentés OK: $DEST_DAILY"
    else
        log "ERROR: SD mentés is sikertelen (méret: ${SD_SIZE})"
        send_alert "Timelapse mentési hiba – KRITIKUS" \
            "A(z) ${YESTERDAY} napi videó sem USB-re, sem SD kártyára nem mentható. Azonnali beavatkozás szükséges! Rendszer: $(hostname)"
        exit 1
    fi

    send_alert "Timelapse USB hiba – SD fallback" \
        "A(z) ${YESTERDAY} napi videó USB helyett SD kártyára mentve (${DEST_DAILY}). Az USB meghajtó ellenőrzése szükséges. Rendszer: $(hostname)"
fi

# ── Framek törlése ramdiskről ─────────────────────────────────────────────────
DELETED=$(ls "${FRAME_DIR}/${YESTERDAY}_"*.jpg 2>/dev/null | wc -l)
rm -f "${FRAME_DIR}/${YESTERDAY}_"*.jpg
log "Deleted ${DELETED} frames from ramdisk"

# ── Master videó újraépítése archívból ───────────────────────────────────────
# A master csak akkor épül újra, ha a napi mentés sikeres volt.
# A DEST_DAILY változó tartalmazza a sikeres mentés elérési útját.

DEST_BASE=$(dirname "$(dirname "$DEST_DAILY")")  # archive szülőkönyvtára
DEST_ARCHIVE=$(dirname "$DEST_DAILY")
DEST_MASTER="${DEST_BASE}/master.mp4"

log "Rebuilding master.mp4 from archive: $DEST_ARCHIVE"

MASTER_LIST=$(mktemp)
ls "${DEST_ARCHIVE}/"[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].mp4 2>/dev/null | sort | while IFS= read -r f; do
    echo "file '$f'"
done > "$MASTER_LIST"

SEGMENT_COUNT=$(wc -l < "$MASTER_LIST")
if [ "$SEGMENT_COUNT" -eq 0 ]; then
    log "No archive segments found, skipping master rebuild"
    rm -f "$MASTER_LIST"
else
    MASTER_TMP="${DEST_BASE}/master_tmp.mp4"

    ffmpeg -y \
        -f concat -safe 0 -i "$MASTER_LIST" \
        -c copy \
        "$MASTER_TMP" \
        >> "$LOG" 2>&1

    rm -f "$MASTER_LIST"

    if [ $? -ne 0 ]; then
        log "WARNING: master rebuild sikertelen (a következő napi futáskor újrapróbálja)"
    else
        mv -f "$MASTER_TMP" "$DEST_MASTER"
        MASTER_SIZE=$(du -sh "$DEST_MASTER" | cut -f1)
        log "Master rebuilt: $DEST_MASTER (${MASTER_SIZE}, ${SEGMENT_COUNT} segments)"
    fi
fi

log "=== Done ==="

# Szenzor adatok mentése
bash "$(dirname "$0")/sensor_backup.sh" >> "$LOG" 2>&1

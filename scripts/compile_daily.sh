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
USB_DEV=""
if mountpoint -q "$USB_MOUNT" && [ -w "$USB_MOUNT" ]; then
    USB_OK=true
    USB_DEV=$(findmnt -n -o SOURCE "$USB_MOUNT" 2>/dev/null)
    log "USB rendben: $USB_MOUNT (eszköz: ${USB_DEV:-ismeretlen})"
else
    log "WARNING: USB nem elérhető ($USB_MOUNT), SD fallbackre vált"
fi

# Pendrive remount kísérlet (ha USB_DEV ismert és az írás sikertelen)
remount_usb() {
    if [ -z "$USB_DEV" ]; then
        log "Remount kihagyva: eszközazonosító ismeretlen"
        return 1
    fi
    log "Remount kísérlet: umount $USB_MOUNT → mount $USB_DEV $USB_MOUNT"
    sync
    sudo umount -l "$USB_MOUNT" 2>/dev/null
    sleep 2
    FS=$(lsblk -o FSTYPE -n "$USB_DEV" 2>/dev/null | head -1)
    if [ "$FS" = "ntfs" ] || [ "$FS" = "ntfs-3g" ]; then
        sudo mount -t ntfs-3g -o uid=1000,gid=1000,umask=022 "$USB_DEV" "$USB_MOUNT" 2>>/tmp/remount_err
    else
        sudo mount -o uid=1000,gid=1000 "$USB_DEV" "$USB_MOUNT" 2>>/tmp/remount_err
    fi
    if mountpoint -q "$USB_MOUNT" && [ -w "$USB_MOUNT" ]; then
        log "Remount sikeres: $USB_MOUNT"
        return 0
    else
        log "Remount sikertelen: $(cat /tmp/remount_err 2>/dev/null)"
        return 1
    fi
}

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
        log "ERROR: USB fájl mérete eltér (forrás: ${TMP_SIZE}, cél: ${DEST_SIZE}), remount és újrapróbálkozás"
        rm -f "$DEST_DAILY"

        # Remount + újrapróbálkozás
        if remount_usb; then
            mkdir -p "$USB_ARCHIVE"
            log "Újrapróbálkozás USB írással remount után"
            cp "$TMP_DAILY" "$DEST_DAILY" 2>> "$LOG"
            DEST_SIZE2=$(stat -c%s "$DEST_DAILY" 2>/dev/null || echo 0)
            if [ "$DEST_SIZE2" -eq "$TMP_SIZE" ] && [ "$DEST_SIZE2" -gt 0 ]; then
                log "USB mentés OK (remount után): $DEST_DAILY"
                rm -f "$TMP_DAILY"
            else
                log "ERROR: USB mentés remount után is sikertelen (méret: ${DEST_SIZE2}), SD fallback"
                rm -f "$DEST_DAILY"
                USB_OK=false
            fi
        else
            log "Remount sikertelen, SD fallback"
            USB_OK=false
        fi
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

MASTER_DAYS_ENABLED=$(python3 -c "
import json, sys
try:
    c = json.load(open('${TIMELAPSE_CONFIG}'))
    print('1' if c.get('master_days_enabled') else '0')
except: print('0')
" 2>/dev/null)

MASTER_DAYS=$(python3 -c "
import json, sys
try:
    c = json.load(open('${TIMELAPSE_CONFIG}'))
    print(int(c.get('master_days', 0)))
except: print('0')
" 2>/dev/null)

if [ "$MASTER_DAYS_ENABLED" = "1" ] && [ "${MASTER_DAYS:-0}" -gt 0 ]; then
    log "Rebuilding master.mp4 (last ${MASTER_DAYS} days)"
    MASTER_OUT=$("$SCRIPT_DIR/build_master.sh" --days "$MASTER_DAYS" 2>&1)
else
    log "Rebuilding master.mp4 (all segments)"
    MASTER_OUT=$("$SCRIPT_DIR/build_master.sh" 2>&1)
fi

if [ $? -eq 0 ]; then
    log "$MASTER_OUT"
else
    log "WARNING: master rebuild sikertelen: $MASTER_OUT"
fi

log "=== Done ==="

# Szenzor adatok mentése
bash "$(dirname "$0")/sensor_backup.sh" >> "$LOG" 2>&1

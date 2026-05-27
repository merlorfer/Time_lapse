#!/bin/bash
# =============================================================================
# build_master.sh – Master videó újraépítése archív szegmensekből
# Használat: build_master.sh [--days N]
#   --days N  : csak az utolsó N nap szegmenseit fűzi össze (0 = összes)
# =============================================================================

source "$(dirname "$0")/config.sh"

DAYS=0
while [[ $# -gt 0 ]]; do
    case $1 in
        --days) DAYS="$2"; shift 2 ;;
        *) shift ;;
    esac
done

# Session conf felülírja a config.sh VIDEO_BASE értékét
if [ -f /tmp/timelapse_session.conf ]; then
    SESSION_BASE=$(grep "^VIDEO_BASE=" /tmp/timelapse_session.conf | cut -d'"' -f2)
    [ -n "$SESSION_BASE" ] && VIDEO_BASE="$SESSION_BASE"
fi

ARCHIVE_DIR="${VIDEO_BASE}/archive"
MASTER="${VIDEO_BASE}/master.mp4"
MASTER_TMP="${VIDEO_BASE}/master_tmp.mp4"

echo "Archív könyvtár: ${ARCHIVE_DIR}"
echo "Kimeneti fájl:   ${MASTER}"

if [ ! -d "$ARCHIVE_DIR" ]; then
    echo "HIBA: az archív könyvtár nem létezik: ${ARCHIVE_DIR}"
    exit 1
fi

MASTER_LIST=$(mktemp)

if [ "$DAYS" -gt 0 ]; then
    CUTOFF=$(date -d "${DAYS} days ago" +%Y-%m-%d)
    echo "Szűrés: ${CUTOFF} utáni napok (utolsó ${DAYS} nap)"
    ls "${ARCHIVE_DIR}/"[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].mp4 2>/dev/null | sort | \
    while IFS= read -r f; do
        fname=$(basename "$f" .mp4)
        if [[ "$fname" > "$CUTOFF" ]] || [[ "$fname" == "$CUTOFF" ]]; then
            echo "file '$f'"
        fi
    done > "$MASTER_LIST"
else
    echo "Szűrés: összes archív szegmens"
    ls "${ARCHIVE_DIR}/"[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].mp4 2>/dev/null | sort | \
    while IFS= read -r f; do
        echo "file '$f'"
    done > "$MASTER_LIST"
fi

SEGMENT_COUNT=$(wc -l < "$MASTER_LIST")
if [ "$SEGMENT_COUNT" -eq 0 ]; then
    echo "Nincs archív szegmens a megadott feltételek alapján."
    rm -f "$MASTER_LIST"
    exit 1
fi

echo "Szegmensek száma: ${SEGMENT_COUNT}"
echo "FFmpeg concat indul..."

ffmpeg -y \
    -f concat -safe 0 -i "$MASTER_LIST" \
    -c copy \
    "$MASTER_TMP" 2>&1

FFMPEG_EXIT=$?
rm -f "$MASTER_LIST"

if [ $FFMPEG_EXIT -ne 0 ]; then
    rm -f "$MASTER_TMP"
    echo "HIBA: ffmpeg sikertelen (exit ${FFMPEG_EXIT})"
    exit 1
fi

mv -f "$MASTER_TMP" "$MASTER"
SIZE=$(du -sh "$MASTER" | cut -f1)
echo "Master elkészült: ${MASTER} (${SIZE}, ${SEGMENT_COUNT} szegmens)"

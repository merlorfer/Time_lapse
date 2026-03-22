#!/bin/bash
# =============================================================================
# render_now.sh – Azonnali videó renderelés a ramdisken lévő képekből
# Használat: render_now.sh [--output /mnt/timelapse/archive/valami.mp4]
# Ha nincs --output megadva, automatikus névvel menti az archívba.
# =============================================================================

source "$(dirname "$0")/config.sh"
[ -f /tmp/timelapse_session.conf ] && source /tmp/timelapse_session.conf

LOG="${LOG_DIR}/compile.log"
mkdir -p "$LOG_DIR" "$ARCHIVE_DIR"

# --- Opcionális --output paraméter ---
OUTPUT=""
if [ "$1" = "--output" ] && [ -n "$2" ]; then
    OUTPUT="$2"
fi

if [ -z "$OUTPUT" ]; then
    OUTPUT="${ARCHIVE_DIR}/render_$(date '+%Y-%m-%d_%H%M%S').mp4"
fi

# --- Frame lista ---
FRAME_COUNT=$(ls "$FRAME_DIR/"*.jpg 2>/dev/null | wc -l)
if [ "$FRAME_COUNT" -eq 0 ]; then
    echo "Nincs kép a ramdisken ($FRAME_DIR), nincs mit renderelni."
    exit 1
fi

echo "Renderelés: $FRAME_COUNT kép → $OUTPUT"

FFMPEG_INPUT=$(mktemp)
ls "$FRAME_DIR/"*.jpg 2>/dev/null | sort | while IFS= read -r f; do
    echo "file '$f'"
done > "$FFMPEG_INPUT"

ffmpeg -y \
    -f concat -safe 0 -i "$FFMPEG_INPUT" \
    -vf "scale=${RESOLUTION},format=yuv420p" \
    -c:v libx264 \
    -crf "$FFMPEG_CRF" \
    -preset "$FFMPEG_PRESET" \
    "$OUTPUT" \
    >> "$LOG" 2>&1

rm -f "$FFMPEG_INPUT"

if [ $? -eq 0 ]; then
    SIZE=$(du -sh "$OUTPUT" | cut -f1)
    DURATION=$(ffprobe -v quiet -show_entries format=duration -of csv=p=0 "$OUTPUT" 2>/dev/null | xargs printf "%.1f")
    echo "Kész: $OUTPUT"
    echo "  Méret: ${SIZE}, Hossz: ${DURATION} mp"
else
    echo "ERROR: ffmpeg sikertelen. Részletek: $LOG"
    exit 1
fi

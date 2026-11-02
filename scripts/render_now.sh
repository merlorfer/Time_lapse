#!/bin/bash
# =============================================================================
# render_now.sh – Azonnali videó renderelés a ramdisken lévő képekből
# Használat: render_now.sh [--camera NÉV] [--output /mnt/timelapse/renders/valami.mp4]
# Ha nincs --output megadva, automatikus névvel menti a renders/<kamera>/ mappába.
# A renders/ mappa ELKÜLÖNÜL az archive/-tól, nem kerül bele a napi masterbe.
# =============================================================================

source "$(dirname "$0")/config.sh"

LOG="${LOG_DIR}/compile.log"
mkdir -p "$LOG_DIR"

# --- Paraméterek ---
OUTPUT=""
CAMERA_ARG=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --output) OUTPUT="$2"; shift 2 ;;
        --camera) CAMERA_ARG="$2"; shift 2 ;;
        *) shift ;;
    esac
done

load_camera "$CAMERA_ARG" || exit 1
mkdir -p "$RENDERS_DIR"

if [ -z "$OUTPUT" ]; then
    OUTPUT="${RENDERS_DIR}/render_$(date '+%Y-%m-%d_%H%M%S').mp4"
fi

# --- Frame lista ---
FRAME_COUNT=$(ls "$FRAME_DIR/"*.jpg 2>/dev/null | wc -l)
if [ "$FRAME_COUNT" -eq 0 ]; then
    echo "[$CAM] Nincs kép a ramdisken ($FRAME_DIR), nincs mit renderelni."
    exit 1
fi

echo "[$CAM] Renderelés: $FRAME_COUNT kép → $OUTPUT"

FFMPEG_INPUT=$(mktemp)
ls "$FRAME_DIR/"*.jpg 2>/dev/null | sort | while IFS= read -r f; do
    echo "file '$f'"
done > "$FFMPEG_INPUT"

if [ "$RESOLUTION" = "native" ]; then
    VIDEO_FILTER="format=yuv420p"
else
    VIDEO_FILTER="scale=${RESOLUTION},format=yuv420p"
fi

ffmpeg -y \
    -r "$FFMPEG_FPS" -f concat -safe 0 -i "$FFMPEG_INPUT" \
    -vf "$VIDEO_FILTER" \
    -c:v libx264 \
    -crf "$FFMPEG_CRF" \
    -preset "$FFMPEG_PRESET" \
    "$OUTPUT" \
    >> "$LOG" 2>&1
FF_RC=$?

rm -f "$FFMPEG_INPUT"

if [ $FF_RC -eq 0 ]; then
    SIZE=$(du -sh "$OUTPUT" | cut -f1)
    DURATION=$(ffprobe -v quiet -show_entries format=duration -of csv=p=0 "$OUTPUT" 2>/dev/null | xargs printf "%.1f")
    echo "Kész: $OUTPUT"
    echo "  Méret: ${SIZE}, Hossz: ${DURATION} mp"
else
    echo "ERROR: ffmpeg sikertelen. Részletek: $LOG"
    exit 1
fi

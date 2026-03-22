#!/bin/bash
# Teszt: a /tmp/timelapse_frames/-ben lévő képekből videót készít
FRAME_DIR="/tmp/timelapse_frames"
OUTPUT="/mnt/timelapse/archive/test_$(date +%Y-%m-%d_%H%M%S).mp4"

FFMPEG_INPUT=$(mktemp)
ls "$FRAME_DIR/"*.jpg 2>/dev/null | sort | while IFS= read -r f; do
    echo "file '$f'"
done > "$FFMPEG_INPUT"

COUNT=$(wc -l < "$FFMPEG_INPUT")
echo "Compiling $COUNT frames -> $OUTPUT"

ffmpeg -y \
    -f concat -safe 0 -i "$FFMPEG_INPUT" \
    -vf "scale=1920x1080,format=yuv420p" \
    -c:v libx264 \
    -crf 23 \
    -preset medium \
    "$OUTPUT" 2>&1

rm -f "$FFMPEG_INPUT"

if [ -f "$OUTPUT" ]; then
    echo "--- Success ---"
    ls -lh "$OUTPUT"
    ffprobe -v quiet -show_entries format=duration -of csv=p=0 "$OUTPUT" | xargs -I{} echo "Duration: {} sec"
else
    echo "FAILED"
fi

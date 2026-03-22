#!/bin/bash
# Teszt: 10 kép 1 másodpercenként, időablak bypass
FRAME_DIR="/tmp/timelapse_frames"
mkdir -p "$FRAME_DIR"
rm -f "$FRAME_DIR"/*.jpg

for i in 1 2 3 4 5 6 7 8 9 10; do
    TS=$(date +%Y-%m-%d_%H%M%S)
    fswebcam -d /dev/video0 -r 1920x1080 --skip 2 --jpeg 85 --no-banner \
        "$FRAME_DIR/${TS}.jpg" 2>/dev/null \
        && echo "OK: $TS" || echo "FAIL: $TS"
    sleep 1
done

echo "--- Frames ---"
ls -lh "$FRAME_DIR/"

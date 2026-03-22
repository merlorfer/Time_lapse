#!/bin/bash
# =============================================================================
# stop_timelapse.sh – Timelapse rögzítés leállítása
# Használat: /home/orangepi/timelapse/scripts/stop_timelapse.sh
# =============================================================================

source "$(dirname "$0")/config.sh"

if [ ! -f /tmp/timelapse_active ]; then
    echo "Timelapse nem fut."
    exit 0
fi

STARTED=$(cat /tmp/timelapse_active)
FRAME_COUNT=$(ls "$FRAME_DIR"/*.jpg 2>/dev/null | wc -l)

rm -f /tmp/timelapse_active

echo "Timelapse leállítva."
echo "Indítva: ${STARTED}"
echo "Rögzített képek: ${FRAME_COUNT} (${FRAME_DIR})"

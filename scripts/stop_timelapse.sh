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

# --- Perzisztens session frissítése: már nem aktív ---
python3 - <<PYEOF 2>/dev/null || true
import json, os
path = "/home/orangepi/timelapse/last_session.json"
try:
    with open(path) as f:
        data = json.load(f)
except Exception:
    data = {}
data["active"] = False
data["stopped_at"] = "$(date '+%Y-%m-%d %H:%M:%S')"
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)
os.replace(tmp, path)
PYEOF

echo "Timelapse leállítva."
echo "Indítva: ${STARTED}"
echo "Rögzített képek: ${FRAME_COUNT} (${FRAME_DIR})"

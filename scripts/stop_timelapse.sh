#!/bin/bash
# =============================================================================
# stop_timelapse.sh – Timelapse rögzítés leállítása
# Használat: stop_timelapse.sh [--camera NÉV | --all]
#   (paraméter nélkül az USB kamerát állítja le)
# =============================================================================

source "$(dirname "$0")/config.sh"

if [ "$1" = "--all" ]; then
    for cam in $(python3 "${SCRIPT_DIR}/cameras.py" names); do
        bash "$0" --camera "$cam"
    done
    exit 0
fi

CAMERA_ARG=""
[ "$1" = "--camera" ] && CAMERA_ARG="$2"
load_camera "$CAMERA_ARG" || exit 1

if [ ! -f "$ACTIVE_FLAG" ]; then
    echo "[$CAM] Timelapse nem fut."
    exit 0
fi

STARTED=$(cat "$ACTIVE_FLAG")
FRAME_COUNT=$(ls "$FRAME_DIR"/*.jpg 2>/dev/null | wc -l)

rm -f "$ACTIVE_FLAG"

# --- Perzisztens session frissítése: már nem aktív ---
python3 - <<PYEOF 2>/dev/null || true
import json, os
path = "${LAST_SESSION_JSON}"
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

echo "Timelapse leállítva: ${CAM}"
echo "Indítva: ${STARTED}"
echo "Rögzített képek: ${FRAME_COUNT} (${FRAME_DIR})"

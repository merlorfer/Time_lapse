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

# Értesítés: aki nem ő állította le, így azonnal észreveszi (a sorból az email_dispatcher küldi)
python3 "${SCRIPT_DIR}/send_alert.py" timelapse_stopped "${CAM}: a timelapse rögzítés le lett állítva" \
    "A(z) ${CAM} kamera timelapse rögzítése le lett állítva ($(date '+%Y-%m-%d %H:%M:%S')). Kezdeményező: ${TL_CALLER:-parancssor}. Rögzített képek a ramdisken: ${FRAME_COUNT}. Újraindítás: a weboldal Indítás gombja. Rendszer: $(hostname)" \
    >/dev/null 2>&1 || true

echo "Timelapse leállítva: ${CAM}"
echo "Indítva: ${STARTED}"
echo "Rögzített képek: ${FRAME_COUNT} (${FRAME_DIR})"

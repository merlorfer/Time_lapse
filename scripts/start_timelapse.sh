#!/bin/bash
# =============================================================================
# start_timelapse.sh – Timelapse rögzítés indítása egy kamerán
# Használat: start_timelapse.sh [OPCIÓK]
# =============================================================================

source "$(dirname "$0")/config.sh"

usage() {
    cat <<EOF
Használat: start_timelapse.sh [OPCIÓK]

Opciók:
  --camera   NÉV      Melyik kamera           (alapért.: az USB kamera)
  --start    HH:MM    Rögzítés kezdete        (alapért.: a kamera mentett beállítása)
  --end      HH:MM    Rögzítés vége           (alapért.: a kamera mentett beállítása)
  --interval SEC      Képek közötti szünet mp. (alapért.: a kamera mentett beállítása)
  --storage  PATH     Videók mentési helye     (alapért.: auto USB/SD)
  --help              Ez a súgó

Példák:
  start_timelapse.sh
  start_timelapse.sh --camera Garden --start 08:00 --end 16:00
  start_timelapse.sh --interval 120
  start_timelapse.sh --start 07:30 --end 19:00 --interval 30 --storage /mnt/timelapse
EOF
}

# --- Paraméterek feldolgozása ---
CAMERA_ARG=""
SESSION_START=""; SESSION_END=""; SESSION_INTERVAL=""
SESSION_STORAGE="$VIDEO_BASE"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --camera)
            CAMERA_ARG="$2"; shift 2 ;;
        --start)
            SESSION_START="$2"; shift 2 ;;
        --end)
            SESSION_END="$2"; shift 2 ;;
        --interval)
            SESSION_INTERVAL="$2"; shift 2 ;;
        --storage)
            SESSION_STORAGE="$2"; shift 2 ;;
        --help)
            usage; exit 0 ;;
        *)
            echo "Ismeretlen opció: $1"; usage; exit 1 ;;
    esac
done

# A kamera mentett beállításai az alapértékek (a korábbi munkamenet fájlja nem számít)
load_camera "$CAMERA_ARG" nosession || exit 1
SESSION_START="${SESSION_START:-$CAPTURE_START}"
SESSION_END="${SESSION_END:-$CAPTURE_END}"
SESSION_INTERVAL="${SESSION_INTERVAL:-$CAPTURE_INTERVAL}"

# Perzisztens konfig (SD kártyán, túléli a rebootot)
TIMELAPSE_CONFIG="/home/orangepi/timelapse/timelapse_config.json"

# --- Már fut? ---
if [ -f "$ACTIVE_FLAG" ]; then
    echo "[$CAM] Timelapse már fut (indítva: $(cat "$ACTIVE_FLAG"))"
    echo "Leállításhoz: stop_timelapse.sh --camera $CAM"
    exit 0
fi

# --- Könyvtárak ---
mkdir -p "$FRAME_DIR" "$LOG_DIR" "${SESSION_STORAGE}/archive/${CAM}"

# --- Session konfig mentése (capture.sh és compile_daily.sh olvassa) ---
# A videó/master útvonalakat a load_camera() a VIDEO_BASE-ből és a kamera nevéből adja.
cat > "$SESSION_CONF" <<EOF
CAPTURE_START="${SESSION_START}"
CAPTURE_END="${SESSION_END}"
CAPTURE_INTERVAL="${SESSION_INTERVAL}"
VIDEO_BASE="${SESSION_STORAGE}"
EOF

# --- Flag fájl ---
date '+%Y-%m-%d %H:%M:%S' > "$ACTIVE_FLAG"

# --- Perzisztens session mentés (áramszünet utáni visszaállításhoz) ---
python3 - <<PYEOF
import json, os
data = {
    "camera":           "${CAM}",
    "active":           True,
    "session_start":    "${SESSION_START}",
    "session_end":      "${SESSION_END}",
    "session_interval": "${SESSION_INTERVAL}",
    "session_storage":  "${SESSION_STORAGE}",
    "saved_at":         "$(date '+%Y-%m-%d %H:%M:%S')"
}
path = "${LAST_SESSION_JSON}"
tmp  = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)
os.replace(tmp, path)
PYEOF

echo "Timelapse elindítva: ${CAM}"
echo "  Időablak: ${SESSION_START} – ${SESSION_END}"
echo "  Intervallum: ${SESSION_INTERVAL} másodperc"
echo "  Videók:   ${SESSION_STORAGE}/archive/${CAM}/"
echo "  Log:      tail -f ${LOG_DIR}/capture.log"
echo "Leállítás:  stop_timelapse.sh --camera ${CAM}"

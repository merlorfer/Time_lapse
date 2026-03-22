#!/bin/bash
# =============================================================================
# start_timelapse.sh – Timelapse rögzítés indítása
# Használat: start_timelapse.sh [OPCIÓK]
# =============================================================================

source "$(dirname "$0")/config.sh"

usage() {
    cat <<EOF
Használat: start_timelapse.sh [OPCIÓK]

Opciók:
  --start    HH:MM    Rögzítés kezdete          (alapért.: ${CAPTURE_START})
  --end      HH:MM    Rögzítés vége             (alapért.: ${CAPTURE_END})
  --interval SEC      Képek közötti szünet mp.  (alapért.: ${CAPTURE_INTERVAL})
  --storage  PATH     Videók mentési helye       (alapért.: auto USB/SD)
  --help              Ez a súgó

Példák:
  start_timelapse.sh
  start_timelapse.sh --start 08:00 --end 16:00
  start_timelapse.sh --interval 120
  start_timelapse.sh --start 07:30 --end 19:00 --interval 30 --storage /mnt/timelapse
EOF
}

# --- Paraméterek feldolgozása ---
SESSION_START="$CAPTURE_START"
SESSION_END="$CAPTURE_END"
SESSION_INTERVAL="$CAPTURE_INTERVAL"
SESSION_STORAGE="$VIDEO_BASE"

while [[ $# -gt 0 ]]; do
    case "$1" in
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

# --- Már fut? ---
if [ -f /tmp/timelapse_active ]; then
    echo "Timelapse már fut (indítva: $(cat /tmp/timelapse_active))"
    echo "Leállításhoz: stop_timelapse.sh"
    exit 0
fi

# --- Könyvtárak ---
mkdir -p "$FRAME_DIR" "$LOG_DIR" "${SESSION_STORAGE}/archive"

# --- Session konfig mentése (capture.sh és compile_daily.sh olvassa) ---
cat > /tmp/timelapse_session.conf <<EOF
CAPTURE_START="${SESSION_START}"
CAPTURE_END="${SESSION_END}"
CAPTURE_INTERVAL="${SESSION_INTERVAL}"
VIDEO_BASE="${SESSION_STORAGE}"
ARCHIVE_DIR="${SESSION_STORAGE}/archive"
MASTER_VIDEO="${SESSION_STORAGE}/master.mp4"
EOF

# --- Flag fájl ---
date '+%Y-%m-%d %H:%M:%S' > /tmp/timelapse_active

echo "Timelapse elindítva."
echo "  Időablak: ${SESSION_START} – ${SESSION_END}"
echo "  Intervallum: ${SESSION_INTERVAL} másodperc"
echo "  Videók:   ${SESSION_STORAGE}/archive/"
echo "  Log:      tail -f ${LOG_DIR}/capture.log"
echo "Leállítás:  stop_timelapse.sh"

#!/bin/bash
# =============================================================================
# config.sh – Timelapse rendszer közös konfiguráció
# Minden script source-olja ezt a fájlt.
# =============================================================================

# Felvételi időablak (alapértelmezés; kamerára a load_camera() állítja be)
CAPTURE_START="06:00"
CAPTURE_END="20:00"

# Könyvtárak
FRAMES_ROOT="/tmp/timelapse_frames"
FRAME_DIR="$FRAMES_ROOT"      # a load_camera() kamerára külön mappára állítja (FRAMES_ROOT/<kamera>)
SCRIPT_DIR="/home/orangepi/timelapse/scripts"
LOG_DIR="/home/orangepi/timelapse/logs"

# FFmpeg beállítások
FFMPEG_FPS=24
FFMPEG_CRF=23
FFMPEG_PRESET="medium"
RESOLUTION="1920x1080"

# Kamera eszköz
# A /dev/videoX sorszámozás kernel/driver frissítés után eltolódhat (pl. a C920
# UVC metaadat node-ja beékelődik a tényleges capture node elé), ezért a fix
# útvonal helyett detektáljuk: a preferált node-tól indulva megkeressük az
# elsőt, aminek ténylegesen van "Video Capture" képessége (nem csak metaadat
# vagy memory-to-memory, mint a cedrus dekóder) és a névben egyezik a kamerával.
CAMERA_DEVICE_PREFERRED="/dev/video0"
CAMERA_NAME_MATCH="C920"

detect_camera_device() {
    local preferred="$1" name_match="$2" dev caps

    command -v v4l2-ctl >/dev/null 2>&1 || { echo "$preferred"; return; }

    for dev in "$preferred" /dev/video*; do
        [ -c "$dev" ] || continue
        caps=$(v4l2-ctl -d "$dev" --info 2>/dev/null)
        echo "$caps" | grep -q "$name_match" || continue
        if echo "$caps" | awk '/^\tDevice Caps/{f=1;next} /^[A-Za-z].*:/{f=0} f' | grep -q "Video Capture"; then
            echo "$dev"
            return
        fi
    done

    echo "$preferred"
}

CAPTURE_INTERVAL=60  # képek közötti szünet másodpercben
CAMERA_SKIP=5        # eldobott képkocka induláskor (fehéregyensúly stabilizálás)
JPEG_QUALITY=85
FONT_FILE="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_SIZE=48

# Ramdisk minimális szabad hely (MB) – ha kevesebb, skip
RAMDISK_MIN_FREE_MB=50

# Tárhelydetektálás – USB elsődleges, SD fallback
if mountpoint -q /mnt/timelapse && [ -w /mnt/timelapse ]; then
    VIDEO_BASE="/mnt/timelapse"
else
    VIDEO_BASE="/home/orangepi/timelapse/videos"
fi

ARCHIVE_DIR="${VIDEO_BASE}/archive"
MASTER_VIDEO="${VIDEO_BASE}/master.mp4"

# Perzisztens konfig (SD kártyán, túléli a rebootot)
TIMELAPSE_CONFIG="/home/orangepi/timelapse/timelapse_config.json"

# --- Kamera betöltése -------------------------------------------------------
# load_camera [NÉV] [nosession]   (üres név = az USB kamera)
# A kamera mentett beállításai (cameras.py), majd az éppen futó / utoljára indított
# munkamenet értékei (CAPTURE_START/END/INTERVAL, VIDEO_BASE; "nosession" esetén ezek kimaradnak,
# pl. új munkamenet indításakor), végül a kamerához tartozó
# könyvtárak: FRAME_DIR, ARCHIVE_DIR=<tár>/archive/<kamera>, MASTER_VIDEO=<tár>/<kamera>_master.mp4,
# RENDERS_DIR=<tár>/renders/<kamera>. USB kameránál a CAMERA_DEVICE is.
load_camera() {
    eval "$(python3 "${SCRIPT_DIR}/cameras.py" env "${1:-}")" || return 1
    if [ "${2:-}" != "nosession" ] && [ -f "$SESSION_CONF" ]; then
        source "$SESSION_CONF"
    fi
    ARCHIVE_DIR="${VIDEO_BASE}/archive/${CAM}"
    MASTER_VIDEO="${VIDEO_BASE}/${CAM}_master.mp4"
    RENDERS_DIR="${VIDEO_BASE}/renders/${CAM}"
    if [ "$CAM_TYPE" = "usb" ]; then
        CAMERA_DEVICE="$(detect_camera_device "$CAMERA_DEVICE_PREFERRED" "$CAMERA_NAME_MATCH")"
    fi
    return 0
}

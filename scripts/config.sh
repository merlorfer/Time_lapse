#!/bin/bash
# =============================================================================
# config.sh – Timelapse rendszer közös konfiguráció
# Minden script source-olja ezt a fájlt.
# =============================================================================

# Felvételi időablak
CAPTURE_START="06:00"
CAPTURE_END="20:00"

# Könyvtárak
FRAME_DIR="/tmp/timelapse_frames"
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

CAMERA_DEVICE="$(detect_camera_device "$CAMERA_DEVICE_PREFERRED" "$CAMERA_NAME_MATCH")"
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

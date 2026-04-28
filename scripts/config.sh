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
CAMERA_DEVICE="/dev/video1"
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

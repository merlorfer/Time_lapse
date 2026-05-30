#!/bin/bash
# sensor_backup.sh – Szenzor adatok napi mentése ramdiskről pendrive/SD-re
# Hívja: compile_daily.sh (00:05 cron)

SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/config.sh"
[ -f /tmp/timelapse_session.conf ] && source /tmp/timelapse_session.conf

SENSOR_RAM_DIR="/tmp/sensor_data"
SENSOR_ARCHIVE_DIR="$VIDEO_BASE/sensor_data"
YESTERDAY=$(date -d "yesterday" +%Y-%m-%d)

if [ ! -d "$SENSOR_RAM_DIR/$YESTERDAY" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') Nincs szenzor adat: $YESTERDAY – kihagyva"
    exit 0
fi

mkdir -p "$SENSOR_ARCHIVE_DIR/$YESTERDAY"
cp "$SENSOR_RAM_DIR/$YESTERDAY/"*.csv "$SENSOR_ARCHIVE_DIR/$YESTERDAY/" 2>/dev/null
echo "$(date '+%Y-%m-%d %H:%M:%S') Szenzor mentve: $SENSOR_ARCHIVE_DIR/$YESTERDAY/"

rm -rf "$SENSOR_RAM_DIR/$YESTERDAY"
echo "$(date '+%Y-%m-%d %H:%M:%S') Ramdisk törölve: $SENSOR_RAM_DIR/$YESTERDAY"

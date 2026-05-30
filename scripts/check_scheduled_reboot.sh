#!/bin/bash
# =============================================================================
# check_scheduled_reboot.sh – Ütemezett reboot ellenőrzés
# Cron: 0 * * * * orangepi /home/orangepi/timelapse/scripts/check_scheduled_reboot.sh
# =============================================================================

SCRIPT_DIR="/home/orangepi/timelapse/scripts"
TIMELAPSE_CONFIG="/home/orangepi/timelapse/timelapse_config.json"
LOG="/home/orangepi/timelapse/logs/reboot_check.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

mkdir -p "$(dirname "$LOG")"

# Konfig olvasása Python segítségével
read_cfg() {
    python3 -c "
import json, sys
try:
    d = json.load(open('$TIMELAPSE_CONFIG'))
    print(d.get('$1', '$2'))
except Exception:
    print('$2')
"
}

REBOOT_ENABLED=$(read_cfg reboot_enabled false)
if [ "$REBOOT_ENABLED" != "True" ] && [ "$REBOOT_ENABLED" != "true" ]; then
    exit 0
fi

INTERVAL_DAYS=$(read_cfg reboot_interval_days 7)
REBOOT_TIME=$(read_cfg reboot_time "03:00")
LAST_REBOOT=$(read_cfg last_reboot_date "")

TODAY=$(date '+%Y-%m-%d')
CURRENT_TIME=$(date '+%H:%M')

# Következő reboot dátum kiszámítása
NEXT_REBOOT=$(python3 -c "
from datetime import date, timedelta
last = '$LAST_REBOOT'
interval = int('$INTERVAL_DAYS')
try:
    d = date.fromisoformat(last) if last else date.today()
    print((d + timedelta(days=interval)).isoformat())
except Exception:
    print(date.today().isoformat())
")

# Időpont-ellenőrzés ±5 perc toleranciával
TIME_MATCH=$(python3 -c "
from datetime import datetime
current = '$CURRENT_TIME'
target  = '$REBOOT_TIME'
try:
    c = datetime.strptime(current, '%H:%M')
    t = datetime.strptime(target,  '%H:%M')
    diff = abs((c - t).total_seconds())
    print('true' if diff <= 300 else 'false')
except Exception:
    print('false')
")

if [ "$TODAY" \< "$NEXT_REBOOT" ]; then
    exit 0
fi

if [ "$TIME_MATCH" != "true" ]; then
    exit 0
fi

log "=== Ütemezett reboot indítása (next=$NEXT_REBOOT, time=$REBOOT_TIME) ==="
bash "${SCRIPT_DIR}/safe_reboot.sh"

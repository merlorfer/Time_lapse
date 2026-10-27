#!/bin/bash
# =============================================================================
# compile_check.sh – a rögzítési ablak vége után 1 perccel összefűzi a nap képeit
# Cron: * * * * * orangepi /home/orangepi/timelapse/scripts/compile_check.sh
#
# Percenként fut, és szinte semmit nem csinál, amíg nincs esedékes munkamenet.
# Esedékes: a ramdisken vannak a munkamenethez tartozó képek, és az ablak vége óta
# eltelt 1 perc (éjfélen átnyúló ablaknál is; a nap a rögzítési ablak KEZDŐ napja).
# Amíg az ablak tart, a képek a ramdisken maradnak. Sikertelen összefűzést legfeljebb
# háromszor próbál újra automatikusan (20 percenként), a képek addig megmaradnak.
# =============================================================================

SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/config.sh"
LOG="${LOG_DIR}/compile.log"
mkdir -p "$LOG_DIR"

# Már fut egy összefűzés (vagy másik ellenőrzés)
exec 8>/tmp/timelapse_compile_check.lock
flock -n 8 || exit 0

# Ha éppen készül egy kép (előzetes parancs + várakozás), annak a végét kivárjuk.
exec 7>/tmp/timelapse_capture.lock
flock -n 7 || exit 0
flock -u 7

DUE=$(python3 "$SCRIPT_DIR/session_days.py" due --grace 60)
[ -z "$DUE" ] && exit 0

for D in $DUE; do
    if ! python3 "$SCRIPT_DIR/session_days.py" try "$D"; then
        continue    # már próbáltuk, a következő próba később (vagy elfogyott a 3 kísérlet)
    fi
    bash "$SCRIPT_DIR/compile_daily.sh" --day "$D" >> "$LOG" 2>&1
done

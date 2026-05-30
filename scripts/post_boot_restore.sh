#!/bin/bash
# =============================================================================
# post_boot_restore.sh – Boot utáni session visszaállítás
# Systemd: timelapse-restore.service futtatja boot után
# =============================================================================

SCRIPT_DIR="/home/orangepi/timelapse/scripts"
PRE_REBOOT_STATE="/home/orangepi/timelapse/pre_reboot_state.json"
LAST_SESSION="/home/orangepi/timelapse/last_session.json"
TIMELAPSE_CONFIG="/home/orangepi/timelapse/timelapse_config.json"
LOG="/home/orangepi/timelapse/logs/restore.log"
USB_MOUNT="/mnt/timelapse"
SD_BASE="/home/orangepi/timelapse/videos"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }
send_alert() { python3 "${SCRIPT_DIR}/send_alert.py" "$1" "$2" >> "$LOG" 2>&1 || true; }

mkdir -p "$(dirname "$LOG")"

log "=== post_boot_restore.sh indítva ==="

# Értékek kiolvasása egy JSON fájlból (python3 stdlib)
read_json_field() {
    local file="$1" field="$2"
    python3 -c "
import json
try:
    d = json.load(open('${file}'))
    print(d.get('${field}', ''))
except Exception:
    print('')
"
}

# ── Forrás meghatározása: tervezett reboot vagy áramszünet utáni visszaállítás ─
STATE_FILE=""
STATE_SOURCE=""

if [ -f "$PRE_REBOOT_STATE" ]; then
    STATE_FILE="$PRE_REBOOT_STATE"
    STATE_SOURCE="tervezett reboot (pre_reboot_state.json)"
elif [ -f "$LAST_SESSION" ]; then
    # Áramszünet eset: csak akkor állítjuk vissza, ha aktív volt
    WAS_ACTIVE=$(read_json_field "$LAST_SESSION" active)
    if [ "$WAS_ACTIVE" = "True" ] || [ "$WAS_ACTIVE" = "true" ]; then
        STATE_FILE="$LAST_SESSION"
        STATE_SOURCE="áramszünet utáni visszaállítás (last_session.json)"
        log "Áramszünet detektálva – last_session.json alapján visszaállítás"
        send_alert "Timelapse áramszünet után visszaállva" \
            "Áramszünet vagy váratlan leállás után a timelapse automatikusan visszaállt a mentett paraméterekkel. Rendszer: $(hostname)"
    else
        log "Nincs visszaállítani való (last_session.json active=false) – normál boot"
        exit 0
    fi
else
    log "Nincs pre_reboot_state.json és last_session.json sem – normál boot, kihagyva"
    exit 0
fi

log "${STATE_SOURCE} – visszaállítás kezdődik"

TL_ACTIVE=$(read_json_field "$STATE_FILE" timelapse_active)
SESSION_START=$(read_json_field "$STATE_FILE" session_start)
SESSION_END=$(read_json_field "$STATE_FILE" session_end)
SESSION_INTERVAL=$(read_json_field "$STATE_FILE" session_interval)
SESSION_STORAGE=$(read_json_field "$STATE_FILE" session_storage)

# last_session.json-ból az active mező neve "active", nem "timelapse_active"
if [ -z "$TL_ACTIVE" ]; then
    TL_ACTIVE=$(read_json_field "$STATE_FILE" active)
fi

log "Mentett állapot: timelapse_active=$TL_ACTIVE, start=$SESSION_START, end=$SESSION_END, interval=$SESSION_INTERVAL, storage=$SESSION_STORAGE"

# ── USB mount ellenőrzés (retry) ─────────────────────────────────────────────
USB_OK=false
for attempt in 1 2 3; do
    if mountpoint -q "$USB_MOUNT" && [ -w "$USB_MOUNT" ]; then
        USB_OK=true
        log "USB elérhető: $USB_MOUNT"
        break
    fi
    log "USB nem elérhető (kísérlet $attempt/3), várakozás 10 mp..."
    sleep 10
done

# ── Tárhely meghatározása ─────────────────────────────────────────────────────
if [ "$USB_OK" = true ]; then
    RESTORE_STORAGE="$USB_MOUNT"
else
    RESTORE_STORAGE="$SD_BASE"
    log "USB nem elérhető – SD fallback: $RESTORE_STORAGE"
    send_alert "Timelapse USB hiba boot után" \
        "Boot után az USB meghajtó ($USB_MOUNT) nem elérhető. A timelapse SD kártyán folytatódik ($RESTORE_STORAGE). Rendszer: $(hostname)"
fi

# ── last_reboot_date frissítése a konfigban ───────────────────────────────────
TODAY=$(date '+%Y-%m-%d')
python3 - <<PYEOF
import json, os
cfg_file = "$TIMELAPSE_CONFIG"
try:
    with open(cfg_file) as f:
        cfg = json.load(f)
except Exception:
    cfg = {}
cfg["last_reboot_date"] = "$TODAY"
tmp = cfg_file + ".tmp"
with open(tmp, "w") as f:
    json.dump(cfg, f, indent=2)
os.replace(tmp, cfg_file)
print("last_reboot_date frissítve: $TODAY")
PYEOF
log "timelapse_config.json frissítve (last_reboot_date=$TODAY)"

# ── Timelapse újraindítása ha aktív volt ─────────────────────────────────────
if [ "$TL_ACTIVE" = "True" ] || [ "$TL_ACTIVE" = "true" ]; then
    log "Timelapse újraindítása a mentett paraméterekkel..."

    ARGS=()
    [ -n "$SESSION_START" ]    && ARGS+=(--start    "$SESSION_START")
    [ -n "$SESSION_END" ]      && ARGS+=(--end      "$SESSION_END")
    [ -n "$SESSION_INTERVAL" ] && ARGS+=(--interval "$SESSION_INTERVAL")
    ARGS+=(--storage "$RESTORE_STORAGE")

    log "Indítás: bash start_timelapse.sh ${ARGS[*]}"
    bash "${SCRIPT_DIR}/start_timelapse.sh" "${ARGS[@]}" >> "$LOG" 2>&1

    if [ $? -eq 0 ]; then
        log "Timelapse sikeresen újraindítva"
    else
        log "ERROR: timelapse újraindítás sikertelen"
        send_alert "Timelapse újraindítás hiba" \
            "Boot után a timelapse automatikus újraindítása sikertelen. Kézi beavatkozás szükséges. Rendszer: $(hostname)"
    fi
else
    log "Timelapse nem volt aktív leállás előtt – nem indítjuk újra"
fi

# ── Takarítás ────────────────────────────────────────────────────────────────
if [ -f "$PRE_REBOOT_STATE" ]; then
    rm -f "$PRE_REBOOT_STATE"
    log "pre_reboot_state.json törölve"
fi
# last_session.json marad – a következő stop_timelapse.sh írja active=false-ra
log "=== Visszaállítás kész ==="

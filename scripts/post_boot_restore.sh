#!/bin/bash
# =============================================================================
# post_boot_restore.sh – Boot utáni session visszaállítás
# Systemd: timelapse-restore.service futtatja boot után
# =============================================================================

SCRIPT_DIR="/home/orangepi/timelapse/scripts"
PRE_REBOOT_STATE="/home/orangepi/timelapse/pre_reboot_state.json"
TIMELAPSE_CONFIG="/home/orangepi/timelapse/timelapse_config.json"
LOG="/home/orangepi/timelapse/logs/restore.log"
USB_MOUNT="/mnt/timelapse"
SD_BASE="/home/orangepi/timelapse/videos"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }
send_alert() { python3 "${SCRIPT_DIR}/send_alert.py" "$1" "$2" >> "$LOG" 2>&1 || true; }

mkdir -p "$(dirname "$LOG")"

log "=== post_boot_restore.sh indítva ==="

# Ha nincs pre_reboot_state.json, normál boot, nincs teendő
if [ ! -f "$PRE_REBOOT_STATE" ]; then
    log "Nincs pre_reboot_state.json – normál boot, kihagyva"
    exit 0
fi

log "pre_reboot_state.json megtalálva, visszaállítás kezdődik"

# Értékek kiolvasása a JSON-ból (python3 stdlib)
read_json() {
    python3 -c "
import json, sys
try:
    d = json.load(open('$PRE_REBOOT_STATE'))
    print(d.get('$1', ''))
except Exception:
    print('')
"
}

TL_ACTIVE=$(read_json timelapse_active)
SESSION_START=$(read_json session_start)
SESSION_END=$(read_json session_end)
SESSION_INTERVAL=$(read_json session_interval)
SESSION_STORAGE=$(read_json session_storage)

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

# ── pre_reboot_state.json törlése ────────────────────────────────────────────
rm -f "$PRE_REBOOT_STATE"
log "pre_reboot_state.json törölve"
log "=== Visszaállítás kész ==="

#!/bin/bash
# =============================================================================
# post_boot_restore.sh – Boot utáni session visszaállítás (kamerákra külön)
# Systemd: timelapse-restore.service futtatja boot után
# =============================================================================

SCRIPT_DIR="/home/orangepi/timelapse/scripts"
PRE_REBOOT_STATE="/home/orangepi/timelapse/pre_reboot_state.json"
TIMELAPSE_CONFIG="/home/orangepi/timelapse/timelapse_config.json"
LOG="/home/orangepi/timelapse/logs/restore.log"
USB_MOUNT="/mnt/timelapse"
SD_BASE="/home/orangepi/timelapse/videos"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }
# send_alert <esemény_id> "Tárgy" "Üzenet" – sorba állítja; az email_dispatcher küldi el, ha van internet
send_alert() { python3 "${SCRIPT_DIR}/send_alert.py" "$1" "$2" "$3" >> "$LOG" 2>&1 || true; }

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

# ── Mit kell visszaállítani? ──────────────────────────────────────────────────
# Az első sor a forrás (pre_reboot = tervezett reboot, last_session = áramszünet), utána
# soronként egy kamera: név<TAB>kezdet<TAB>vég<TAB>intervallum<TAB>tár
PLAN=$(python3 "${SCRIPT_DIR}/cameras.py" restore-plan)
PLAN_SOURCE=$(echo "$PLAN" | head -1)
PLAN_CAMS=$(echo "$PLAN" | tail -n +2)

# ── Indulás értesítés: minden bootnál (a levelet csak akkor küldi ki az email_dispatcher, ha van internet) ─
BOOT_REASON="normál indulás"
if [ -f "$PRE_REBOOT_STATE" ]; then
    case "$(read_json_field "$PRE_REBOOT_STATE" reboot_reason)" in
        shutdown) BOOT_REASON="biztonságos leállítás után" ;;
        *)        BOOT_REASON="tervezett újraindítás után" ;;
    esac
elif [ "$PLAN_SOURCE" = "last_session" ]; then
    BOOT_REASON="váratlan leállás (áramszünet?) után"
fi
log "Indulás oka: ${BOOT_REASON}"
send_alert boot "Rendszer elindult" \
    "Az Orange Pi elindult (${BOOT_REASON}). Rendszer: $(hostname)"

# ── Forrás meghatározása: tervezett reboot vagy áramszünet utáni visszaállítás ─
if [ "$PLAN_SOURCE" = "pre_reboot" ]; then
    STATE_SOURCE="tervezett reboot (pre_reboot_state.json)"
elif [ "$PLAN_SOURCE" = "last_session" ]; then
    # Áramszünet eset: csak akkor állítjuk vissza, ha valamelyik kamera aktív volt
    STATE_SOURCE="áramszünet utáni visszaállítás (last_session_<kamera>.json)"
    log "Áramszünet detektálva – a mentett munkamenetek alapján visszaállítás"
    send_alert power_restored "Timelapse áramszünet után visszaállva" \
        "Áramszünet vagy váratlan leállás után a timelapse automatikusan visszaállt a mentett paraméterekkel. Rendszer: $(hostname)"
else
    log "Nincs pre_reboot_state.json, és egyik kamera sem volt aktív – normál boot"
    exit 0
fi

log "${STATE_SOURCE} – visszaállítás kezdődik"
log "Visszaállítandó kamerák: $(echo "$PLAN_CAMS" | cut -f1 | tr '\n' ' ')"

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
    send_alert usb_boot_error "Timelapse USB hiba boot után" \
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

# ── Timelapse újraindítása azoknál a kameráknál, amelyek aktívak voltak ───────
if [ -n "$PLAN_CAMS" ]; then
    while IFS=$'\t' read -r CAMN SESSION_START SESSION_END SESSION_INTERVAL SESSION_STORAGE; do
        [ -z "$CAMN" ] && continue
        for v in SESSION_START SESSION_END SESSION_INTERVAL SESSION_STORAGE; do
            [ "${!v}" = "-" ] && printf -v "$v" ''
        done
        log "[$CAMN] Mentett állapot: start=$SESSION_START, end=$SESSION_END, interval=$SESSION_INTERVAL, storage=$SESSION_STORAGE"
        log "[$CAMN] Timelapse újraindítása a mentett paraméterekkel..."

        ARGS=(--camera "$CAMN")
        [ -n "$SESSION_START" ]    && ARGS+=(--start    "$SESSION_START")
        [ -n "$SESSION_END" ]      && ARGS+=(--end      "$SESSION_END")
        [ -n "$SESSION_INTERVAL" ] && ARGS+=(--interval "$SESSION_INTERVAL")
        ARGS+=(--storage "$RESTORE_STORAGE")

        log "Indítás: bash start_timelapse.sh ${ARGS[*]}"
        bash "${SCRIPT_DIR}/start_timelapse.sh" "${ARGS[@]}" >> "$LOG" 2>&1

        if [ $? -eq 0 ]; then
            log "[$CAMN] Timelapse sikeresen újraindítva"
        else
            log "ERROR: [$CAMN] timelapse újraindítás sikertelen"
            send_alert restart_failed "$CAMN: timelapse újraindítás hiba" \
                "Boot után a(z) $CAMN kamera timelapse-ának automatikus újraindítása sikertelen. Kézi beavatkozás szükséges. Rendszer: $(hostname)"
        fi
    done <<< "$PLAN_CAMS"
else
    log "Egyik timelapse sem volt aktív leállás előtt – nem indítjuk újra"
fi

# ── Takarítás ────────────────────────────────────────────────────────────────
if [ -f "$PRE_REBOOT_STATE" ]; then
    rm -f "$PRE_REBOOT_STATE"
    log "pre_reboot_state.json törölve"
fi
# a last_session_<kamera>.json fájlok maradnak – a következő stop_timelapse.sh írja active=false-ra
log "=== Visszaállítás kész ==="

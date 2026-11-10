#!/bin/bash
# =============================================================================
# usb_heal.sh – A /mnt/timelapse tároló (pendrive) automatikus javítása
#
# Ugyanazt csinálja, mint a weboldalon a "USB leválasztás", majd a "mountolás":
# leválasztja a beragadt csatlakozást, és újra mountolja a pendrive-ot, majd
# ellenőrzi, hogy tud-e írni rá.
#
# Használat:
#   usb_heal.sh --check    cron (5 percenként): ha a tároló rendben van, semmit nem tesz;
#                          ha nem, megjavítja, és ha nem sikerül, emailt küld (6 óránként)
#   usb_heal.sh --ensure   rendben van? ha nem, javít (compile_daily.sh hívja, email nélkül)
#   usb_heal.sh --force    mindenképp leválaszt és újra mountol (írási hiba után)
# Az ellenőrzés (--check, --ensure) CSAK OLVAS: nem ír a pendrive-ra, így a gyakori futás nem
# koptatja. Írásteszt csak egy javítás után van (egyszer, annak igazolására, hogy működik).
# A csendes írási hibát az összefűzés másolás-ellenőrzése fogja meg, és az --force javítja.
# Kilépési kód: 0 = rendben / megjavítva, 1 = nem sikerült, 2 = kihagyva (kézzel leválasztva)
#
# Melyik pendrive: az /etc/fstab-ban a /mnt/timelapse sor első mezője (UUID=...).
# Pendrive csere: lásd HASZNALAT.md ("Automatikus USB-javítás és pendrive csere").
# Ha a weboldalon (vagy storage_select.sh-val) kézzel leválasztod, a javítás addig nem
# nyúl hozzá, amíg újra nem mountolod (jelzőfájl: /tmp/timelapse_usb_detached).
# =============================================================================

MODE="${1:---check}"
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
MOUNT="${USB_HEAL_MOUNT:-/mnt/timelapse}"
FSTAB="${USB_HEAL_FSTAB:-/etc/fstab}"
LOG="${USB_HEAL_LOG:-/home/orangepi/timelapse/logs/usb_heal.log}"
DEVROOT="${USB_HEAL_DEVROOT:-/dev}"
DETACH_FLAG="${USB_HEAL_DETACH_FLAG:-/tmp/timelapse_usb_detached}"
ALERT_STATE="${USB_HEAL_ALERT_STATE:-/tmp/timelapse_usb_heal_alert}"
ALERT_GAP=21600          # legfeljebb 6 óránként küld emailt, amíg a hiba tart
mkdir -p "$(dirname "$LOG")"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [usb_heal] $*" >> "$LOG"; }

# Olvasással ellenőrzi (NEM ír): mountolva van, nem csak olvasható (a hibás fájlrendszer
# "errors=remount-ro" miatt erre vált), és a gyökérkönyvtára időkorláttal olvasható
usb_readable() {
    mountpoint -q "$MOUNT" || return 1
    findmnt -no OPTIONS "$MOUNT" 2>/dev/null | tr ',' '\n' | grep -qx ro && return 1
    timeout 10 ls "$MOUNT" >/dev/null 2>&1
}

# Írásteszt (egy kis fájl létrehozása és törlése): csak javítás után használjuk
usb_ok() {
    usb_readable || return 1
    local t="$MOUNT/.usb_heal_test"
    timeout 10 sh -c "echo x > '$t' && rm -f '$t'" 2>/dev/null
}

# Az /etc/fstab sora a tárolóhoz: első mező (UUID=..., LABEL=... vagy /dev/...)
fstab_source() {
    awk -v m="$MOUNT" '$1 !~ /^#/ && $2 == m { print $1; exit }' "$FSTAB" 2>/dev/null
}

# Tartalék: az első USB-s lemez első partíciója, fájlrendszerrel (ha az fstab nem segít)
scan_usb_partition() {
    lsblk -J -o PATH,TYPE,TRAN,FSTYPE,PKNAME 2>/dev/null | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)["blockdevices"]
except Exception:
    sys.exit(0)
usb_disks = {x["path"].split("/")[-1] for x in d if x.get("type") == "disk" and x.get("tran") == "usb"}
for x in d:
    if x.get("type") == "part" and x.get("fstype") and x.get("pkname") in usb_disks:
        print(x["path"])
        break
'
}

mount_by_scan() {
    local dev fs
    dev=$(scan_usb_partition)
    [ -z "$dev" ] && { log "nincs USB-s partíció (a pendrive nincs bedugva?)"; return 1; }
    fs=$(lsblk -no FSTYPE "$dev" 2>/dev/null | head -1)
    log "az fstab nem használható, mountolás keresés alapján: $dev ($fs)"
    sudo mkdir -p "$MOUNT"
    if [ "$fs" = "ntfs" ] || [ "$fs" = "ntfs-3g" ]; then
        sudo mount -t ntfs-3g -o uid=1000,gid=1000,umask=022 "$dev" "$MOUNT" 2>>"$LOG"
    else
        sudo mount -o uid=1000,gid=1000 "$dev" "$MOUNT" 2>>"$LOG"
    fi
}

heal() {
    local src dev
    log "javítás indul (mód: $MODE)"
    sync
    if mountpoint -q "$MOUNT"; then
        sudo umount -l "$MOUNT" 2>>"$LOG"
        sleep 2
    fi

    src=$(fstab_source)
    case "$src" in
        UUID=*)  dev="$DEVROOT/disk/by-uuid/${src#UUID=}" ;;
        LABEL=*) dev="$DEVROOT/disk/by-label/${src#LABEL=}" ;;
        /dev/*)  dev="$src" ;;
        *)       dev="" ;;
    esac

    if [ -n "$dev" ] && [ -e "$dev" ]; then
        sudo mkdir -p "$MOUNT"
        sudo mount "$MOUNT" 2>>"$LOG"          # az fstab sora szerint (UUID, opciók)
        # /dev/sdX név szerinti fstab-sor mellett a név megváltozhatott: ekkor keresés
        if ! usb_readable && [[ "$src" == /dev/* ]]; then
            sudo umount -l "$MOUNT" 2>/dev/null
            mount_by_scan || return 1
        fi
    elif [[ "$src" == UUID=* || "$src" == LABEL=* ]]; then
        log "nincs ilyen eszköz: $src – a pendrive nincs bedugva, vagy más az UUID (HASZNALAT.md)"
        return 1
    else
        mount_by_scan || return 1
    fi

    if usb_ok; then
        mkdir -p "$MOUNT/archive" "$MOUNT/renders" 2>/dev/null
        sudo systemctl restart timelapse-http 2>/dev/null    # az új mountot szolgálja ki
        log "kész: $MOUNT rendben ($(findmnt -no SOURCE "$MOUNT" 2>/dev/null))"
        return 0
    fi
    log "a mountolás után sem írható a tároló"
    return 1
}

alert_failed() {
    local now last
    now=$(date +%s)
    last=$(cat "$ALERT_STATE" 2>/dev/null || echo 0)
    [ $(( now - last )) -lt "$ALERT_GAP" ] && return
    echo "$now" > "$ALERT_STATE"
    python3 "${SCRIPT_DIR}/send_alert.py" usb_unavailable "USB tároló nem elérhető" \
        "A(z) ${MOUNT} tároló nem írható, az automatikus javítás (leválasztás + újra mountolás) sikertelen. A videók addig az SD kártyára kerülnek. Ellenőrizd a pendrive-ot (be van-e dugva, nem sérült-e), és hogy az /etc/fstab UUID-ja egyezik-e vele (HASZNALAT.md). A részletek: logs/usb_heal.log. Rendszer: $(hostname)" \
        >/dev/null 2>&1 || true
}

# Kézzel leválasztott pendrive-hoz nem nyúlunk
if [ -f "$DETACH_FLAG" ]; then
    [ "$MODE" != "--check" ] && log "kihagyva: a pendrive kézzel le van választva ($DETACH_FLAG)"
    exit 2
fi

case "$MODE" in
    --check)
        if usb_readable; then
            rm -f "$ALERT_STATE"            # helyreállt: a következő hibáról újra jön email
            exit 0
        fi
        exec 9>/tmp/timelapse_usb_heal.lock
        flock -n 9 || exit 0
        # Futó összefűzést nem zavarunk (az maga is javít, ha kell)
        exec 8>/tmp/timelapse_compile.lock
        flock -n 8 || { log "összefűzés fut, a javítás kihagyva"; exit 0; }
        flock -u 8
        if heal; then exit 0; fi
        alert_failed
        exit 1
        ;;
    --ensure)
        usb_readable && exit 0
        heal && exit 0
        exit 1
        ;;
    --force)
        heal && exit 0
        exit 1
        ;;
    *)
        echo "Használat: $0 --check | --ensure | --force" >&2
        exit 1
        ;;
esac

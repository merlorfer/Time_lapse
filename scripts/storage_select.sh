#!/bin/bash
# =============================================================================
# storage_select.sh – Timelapse tárhely kiválasztása / átváltása
# Használat: storage_select.sh
# =============================================================================

source "$(dirname "$0")/config.sh"

CURRENT="${VIDEO_BASE}"
USB_PATH="/mnt/timelapse"
SD_PATH="/home/orangepi/timelapse/videos"

# --- Státusz összegyűjtése ---
if mountpoint -q "$USB_PATH" && [ -w "$USB_PATH" ]; then
    USB_TAG="[OK]"
else
    USB_TAG="[--]"
fi

SD_FREE=$(df -h / | awk 'NR==2 {print $4}')
CURRENT_LABEL="USB pendrive"
[ "$CURRENT" = "$SD_PATH" ] && CURRENT_LABEL="SD kártya (fallback)"

# --- Főmenü ---
CHOICE=$(whiptail --title "Timelapse – Tárhely kiválasztása" \
    --menu "Jelenlegi: ${CURRENT_LABEL}\n\nVálassz tárolót:" 18 60 4 \
    "1" "USB pendrive  ${USB_TAG}  ${USB_PATH}" \
    "2" "SD kártya               ${SD_PATH}" \
    "3" "USB eszköz mountolása (lista)" \
    "4" "USB leválasztása (umount)" \
    "5" "Kilépés" \
    3>&1 1>&2 2>&3)

case "$CHOICE" in
    1)
        if ! mountpoint -q "$USB_PATH"; then
            whiptail --title "Hiba" --msgbox \
                "Az USB pendrive nincs mountolva.\nHasználd a 3-as opciót a mountoláshoz." 9 52
            exec "$0"
        fi
        NEW_BASE="$USB_PATH"
        ;;

    2)
        mkdir -p "${SD_PATH}/archive"
        NEW_BASE="$SD_PATH"
        ;;

    3)
        # --- Elérhető USB/külső eszközök listázása (nem SD, nem ramdisk) ---
        DEVICE_LIST=()
        while IFS= read -r line; do
            DEV=$(echo "$line" | awk '{print $1}')
            SIZE=$(echo "$line" | awk '{print $2}')
            FSTYPE=$(echo "$line" | awk '{print $3}')
            MOUNT=$(echo "$line" | awk '{print $4}')
            LABEL="$SIZE  $FSTYPE  ${MOUNT:-nincs mountolva}"
            DEVICE_LIST+=("$DEV" "$LABEL")
        done < <(lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT -p -n -l | \
                 grep -v 'mmcblk\|zram\|mtdblock\|loop' | \
                 grep -v '^$' | \
                 awk '$3 != ""')  # csak formázott partíciók

        if [ ${#DEVICE_LIST[@]} -eq 0 ]; then
            whiptail --title "Nincs eszköz" --msgbox \
                "Nem található csatlakoztatott USB eszköz.\nDugd be a pendrive-ot és próbáld újra." 9 52
            exec "$0"
        fi

        # Eszköz kiválasztása
        DEV_CHOICE=$(whiptail --title "USB eszköz kiválasztása" \
            --menu "Válassz eszközt a mountoláshoz:" 18 65 8 \
            "${DEVICE_LIST[@]}" \
            3>&1 1>&2 2>&3)

        [ -z "$DEV_CHOICE" ] && exec "$0"

        # Ha már mountolva van valahol, unmountoljuk
        CURRENT_MOUNT=$(lsblk -o NAME,MOUNTPOINT -p -n "$DEV_CHOICE" 2>/dev/null | awk '{print $2}')
        if [ -n "$CURRENT_MOUNT" ] && [ "$CURRENT_MOUNT" != "$USB_PATH" ]; then
            sudo umount "$DEV_CHOICE" 2>/dev/null
        fi

        # Fájlrendszer típus detektálása
        FS=$(lsblk -o FSTYPE -n "$DEV_CHOICE" 2>/dev/null | head -1)

        # Mountolás
        sudo mkdir -p "$USB_PATH"
        if [ "$FS" = "ntfs" ] || [ "$FS" = "ntfs-3g" ]; then
            MOUNT_CMD="sudo mount -t ntfs-3g -o uid=1000,gid=1000,umask=022 $DEV_CHOICE $USB_PATH"
        else
            MOUNT_CMD="sudo mount -o uid=1000,gid=1000 $DEV_CHOICE $USB_PATH"
        fi

        if $MOUNT_CMD 2>/tmp/mount_err; then
            # UUID lekérése fstab frissítéshez
            NEW_UUID=$(blkid -s UUID -o value "$DEV_CHOICE")
            whiptail --title "Siker" --msgbox \
                "Sikeresen mountolva:\n${DEV_CHOICE} → ${USB_PATH}\nFájlrendszer: ${FS}\nUUID: ${NEW_UUID}" \
                10 58
        else
            ERR=$(cat /tmp/mount_err)
            whiptail --title "Hiba" --msgbox "Mountolás sikertelen:\n${ERR}" 10 58
            exec "$0"
        fi

        exec "$0"   # visszatér a főmenübe
        ;;

    4)
        if ! mountpoint -q "$USB_PATH"; then
            whiptail --title "Info" --msgbox "Az USB nincs mountolva, nincs mit leválasztani." 8 52
            exec "$0"
        fi
        # Ha a timelapse az USB-t használja, átváltunk SD-re
        if [ "$CURRENT" = "$USB_PATH" ]; then
            mkdir -p "${SD_PATH}/archive"
            if [ -f /tmp/timelapse_session.conf ]; then
                sed -i "s|VIDEO_BASE=.*|VIDEO_BASE=\"${SD_PATH}\"|" /tmp/timelapse_session.conf
                sed -i "s|ARCHIVE_DIR=.*|ARCHIVE_DIR=\"${SD_PATH}/archive\"|" /tmp/timelapse_session.conf
                sed -i "s|MASTER_VIDEO=.*|MASTER_VIDEO=\"${SD_PATH}/master.mp4\"|" /tmp/timelapse_session.conf
            fi
            sudo systemctl restart timelapse-http 2>/dev/null
            SWITCH_MSG="Tárhely átváltva SD kártyára."
        else
            SWITCH_MSG=""
        fi
        sudo systemctl stop timelapse-http 2>/dev/null
        if sudo umount "$USB_PATH" 2>/tmp/umount_err; then
            sudo systemctl start timelapse-http 2>/dev/null
            whiptail --title "Kész" --msgbox \
                "USB leválasztva: ${USB_PATH}\n${SWITCH_MSG}\nBiztonságosan kihúzható." 10 52
        else
            sudo systemctl start timelapse-http 2>/dev/null
            ERR=$(cat /tmp/umount_err)
            whiptail --title "Hiba" --msgbox "Leválasztás sikertelen:\n${ERR}" 9 55
        fi
        exec "$0"
        ;;

    5|"")
        exit 0
        ;;
esac

# --- Session konfig és HTTP szerver frissítése ---
if [ -n "$NEW_BASE" ] && [ "$NEW_BASE" != "$CURRENT" ]; then
    mkdir -p "${NEW_BASE}/archive"

    if [ -f /tmp/timelapse_session.conf ]; then
        sed -i "s|VIDEO_BASE=.*|VIDEO_BASE=\"${NEW_BASE}\"|" /tmp/timelapse_session.conf
        sed -i "s|ARCHIVE_DIR=.*|ARCHIVE_DIR=\"${NEW_BASE}/archive\"|" /tmp/timelapse_session.conf
        sed -i "s|MASTER_VIDEO=.*|MASTER_VIDEO=\"${NEW_BASE}/master.mp4\"|" /tmp/timelapse_session.conf
        SESSION_MSG="A futó session is frissítve."
    else
        SESSION_MSG="(Nincs futó timelapse session.)"
    fi

    sudo systemctl restart timelapse-http 2>/dev/null

    LABEL="USB pendrive"
    [ "$NEW_BASE" = "$SD_PATH" ] && LABEL="SD kártya"

    whiptail --title "Kész" --msgbox \
        "Tárhely átváltva: ${LABEL}\n${NEW_BASE}\n\n${SESSION_MSG}\nHTTP szerver újraindítva." \
        11 55
else
    whiptail --title "Info" --msgbox "Nem történt változás." 8 40
fi

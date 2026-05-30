#!/bin/bash
# flash_esp32.sh – ESP32C6 firmware felírása az Orange Pi-ről
# Használat: flash_esp32.sh [firmware_dir]
# Alapértelmezett könyvtár: /home/orangepi/esp32/
#
# A script leállítja a screen serial monitort flashelés előtt,
# majd újraindítja utána.

FIRMWARE_DIR="${1:-$HOME/esp32firmware}"
ESPTOOL="$HOME/.local/bin/esptool"
PORT="/dev/ttyACM0"
BAUD=460800

echo "=== ESP32C6 Flash ==="
echo "Firmware: $FIRMWARE_DIR"

# Fájlok ellenőrzése
for f in bootloader.bin partition-table.bin esp32c6_zigbee_gateway.bin; do
    if [ ! -f "$FIRMWARE_DIR/$f" ]; then
        echo "HIBA: Hiányzó fájl: $FIRMWARE_DIR/$f"
        exit 1
    fi
done

# Serial monitor leállítása
echo "Serial monitor leállítása..."
sudo systemctl stop esp32-serial 2>/dev/null || true
sleep 1

# Flashelés
echo "Flashelés ($BAUD baud)..."
"$ESPTOOL" --chip esp32c6 --port "$PORT" --baud "$BAUD" \
    write_flash --flash_mode dio --flash_freq 80m --flash_size 4MB \
    0x0       "$FIRMWARE_DIR/bootloader.bin" \
    0x8000    "$FIRMWARE_DIR/partition-table.bin" \
    0x10000   "$FIRMWARE_DIR/esp32c6_zigbee_gateway.bin" \
    0x1f5000  "$FIRMWARE_DIR/storage.bin"

STATUS=$?

# Serial monitor újraindítása
echo "Serial monitor újraindítása..."
sleep 2
sudo systemctl start esp32-serial 2>/dev/null || true

if [ $STATUS -eq 0 ]; then
    echo "=== Flash sikeres ==="
else
    echo "=== Flash SIKERTELEN (kód: $STATUS) ==="
    exit $STATUS
fi

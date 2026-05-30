#!/bin/bash
# sync_proxy_web.sh – ESP32 proxy weboldalak szinkronizálása
#
# Akkor kell futtatni, ha a CLCode01/web/ fájljai megváltoztak,
# és a változtatásokat a proxy weboldalán is érvényesíteni kell.
#
# Használat:
#   ./scripts/sync_proxy_web.sh
#
# A script a Time_lapse projekt gyökéréből futtatandó.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SOURCE_DIR="$PROJECT_DIR/../CLCode01/web"
PROXY_DIR="$SCRIPT_DIR/esp32-web"

if [ ! -d "$SOURCE_DIR" ]; then
    echo "HIBA: Nem található a forráskönyvtár: $SOURCE_DIR"
    exit 1
fi

echo "Forrás:  $SOURCE_DIR"
echo "Cél:     $PROXY_DIR"
echo ""

# ── 1. Változatlanul másolt fájlok ───────────────────────────────────────────
for f in ble-service.js style.css manifest.json service-worker.js icon-192.png icon-512.png; do
    if [ -f "$SOURCE_DIR/$f" ]; then
        cp "$SOURCE_DIR/$f" "$PROXY_DIR/$f"
        echo "  Másolva: $f"
    fi
done

# ── 2. script.js – másolás + let → var csere ─────────────────────────────────
cp "$SOURCE_DIR/script.js" "$PROXY_DIR/script.js"
# bleConnected és zigbeeActive globálissá tétele (window property),
# hogy proxy-patch.js közvetlenül tudja állítani őket.
sed -i 's/^let zigbeeActive = false;/var zigbeeActive = false;   \/\/ var = window property (proxy-patch.js sets this)/' "$PROXY_DIR/script.js"
sed -i 's/^let bleConnected = false;/var bleConnected = false;   \/\/ var = window property (proxy-patch.js sets this)/' "$PROXY_DIR/script.js"

# Ellenőrzés
if grep -q "^var bleConnected" "$PROXY_DIR/script.js"; then
    echo "  Módosítva: script.js (bleConnected, zigbeeActive → var)"
else
    echo "  FIGYELEM: script.js – a let→var csere nem sikerült! Kézzel ellenőrizd."
fi

# ── 3. index.html – másolás + proxy-patch.js sor visszarakása ────────────────
cp "$SOURCE_DIR/index.html" "$PROXY_DIR/index.html"
# proxy-patch.js script tag hozzáadása a script.js sor után
sed -i 's|<script src="script\.js"></script>|<script src="script.js"></script>\n    <script src="proxy-patch.js"></script>|' "$PROXY_DIR/index.html"

if grep -q "proxy-patch.js" "$PROXY_DIR/index.html"; then
    echo "  Módosítva: index.html (proxy-patch.js tag hozzáadva)"
else
    echo "  FIGYELEM: index.html – a proxy-patch.js tag hozzáadása nem sikerült! Kézzel ellenőrizd."
fi

echo ""
echo "Szinkronizálás kész."
echo ""
echo "Következő lépés – deploy az Orange Pi-ra:"
echo "  pscp -pw orangepi $PROXY_DIR/*.js  orangepi@100.68.70.151:/home/orangepi/esp32/web/"
echo "  pscp -pw orangepi $PROXY_DIR/*.css orangepi@100.68.70.151:/home/orangepi/esp32/web/"
echo "  pscp -pw orangepi $PROXY_DIR/*.html orangepi@100.68.70.151:/home/orangepi/esp32/web/"
echo "  pscp -pw orangepi $PROXY_DIR/*.png  orangepi@100.68.70.151:/home/orangepi/esp32/web/"
echo "  plink -pw orangepi -batch orangepi@100.68.70.151 'echo orangepi | sudo -S systemctl restart esp32-proxy'"

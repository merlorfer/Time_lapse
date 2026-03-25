# Timelapse – Indítási útmutató

## Timelapse indítása boot után

### 1. Bejelentkezés
PuTTY-ban csatlakozz: `192.168.68.177`, user: `orangepi`, jelszó: `orangepi`

---

### 2. (Opcionális) Preview – nézd meg mit lát a kamera
```bash
/home/orangepi/timelapse/scripts/preview_start.sh
```
Böngészőben: **http://192.168.68.177:8081/stream**

Ha megvan a beállítás, állítsd le:
```bash
/home/orangepi/timelapse/scripts/preview_stop.sh
```

---

### 3. Timelapse indítása
Alapértelmezett beállításokkal (06:00–20:00, 60mp intervallum):
```bash
/home/orangepi/timelapse/scripts/start_timelapse.sh
```

Vagy egyéni paraméterekkel:
```bash
/home/orangepi/timelapse/scripts/start_timelapse.sh --start 08:00 --end 18:00 --interval 120
```

A cron a háttérben fut, ettől kezdve percenként ellenőrzi, és az időablakban minden `--interval` másodpercenként készít egy képet.

**Összes paraméter:**

| Paraméter | Leírás | Alapértelmezett |
|---|---|---|
| `--start HH:MM` | Rögzítés kezdete | `06:00` |
| `--end HH:MM` | Rögzítés vége | `20:00` |
| `--interval SEC` | Képek közötti szünet (mp.) | `60` |
| `--storage PATH` | Videók mentési helye | auto (USB vagy SD fallback) |
| `--help` | Súgó | – |

---

### 4. Ellenőrzés – készülnek-e képek?
```bash
tail -f /home/orangepi/timelapse/logs/capture.log
```
Ctrl+C a kilépéshez. Helyes működésnél ilyesmit látsz:
```
2026-03-21 08:01:03 Captured: /tmp/timelapse_frames/2026-03-21_080103.jpg (1950MB free)
2026-03-21 08:02:03 Captured: /tmp/timelapse_frames/2026-03-21_080203.jpg (1949MB free)
```

---

### 5. Timelapse leállítása
```bash
/home/orangepi/timelapse/scripts/stop_timelapse.sh
```
A már elkészült képek megmaradnak a ramdisken – a következő éjféli compile feldolgozza őket.

---

### 6. Kész videók megtekintése
Böngészőben: **http://192.168.68.177:8080/**

---

## Tárhely átváltása (ha pendrive nem volt bent bootoláskor)

```bash
/home/orangepi/timelapse/scripts/storage_select.sh
```

Interaktív menü jelenik meg:
- **1 – USB pendrive**: átváltás a pendrive-ra (ha be van dugva és mountolva)
- **2 – SD kártya**: átváltás az SD kártyára
- **3 – USB mountolása**: listázza az elérhető USB eszközöket, kiválasztod melyiket mountolja `/mnt/timelapse`-re (NTFS és ext4 automatikusan felismeri)
- **4 – USB leválasztása**: biztonságosan leválasztja a pendrive-ot, ha a timelapse azt használta, automatikusan átváltja SD kártyára
- **5 – Kilépés**

Az átváltás azonnal érvényes: a futó timelapse session és a HTTP szerver is az új helyet használja.

---

## HTTP szerver

Bootoláskor automatikusan indul. Kész videók böngészőből: **http://192.168.68.177:8080/**

```bash
# Státusz
systemctl status timelapse-http

# Újraindítás (pl. pendrive csere után)
sudo systemctl restart timelapse-http

# Le/felállítás
sudo systemctl stop timelapse-http
sudo systemctl start timelapse-http
```

**Eltávolítás (ha le akarod szedni):**
```bash
sudo systemctl stop timelapse-http
sudo systemctl disable timelapse-http
sudo rm /etc/systemd/system/timelapse-http.service
sudo systemctl daemon-reload
```

---

## ESP32C6 vezérlőfelület (BLE proxy)

Az Orange Pi BLE-n keresztül csatlakozik az ESP32C6 Zigbee koordinátorhoz, és böngészőből elérhető webes felületet biztosít.

**Elérés:** http://100.68.70.151:8083/  (NordVPN Meshnet)

Bootoláskor automatikusan indul. Az ESP32C6-ot **nem kell USB-n csatlakoztatni** – BLE-n keresztül kommunikál.

```bash
# Státusz
systemctl status esp32-proxy

# Újraindítás
sudo systemctl restart esp32-proxy

# BLE kapcsolat állapota
journalctl -u esp32-proxy -n 20

# Le/felállítás
sudo systemctl stop esp32-proxy
sudo systemctl start esp32-proxy
```

**Megjegyzés:** Ha az ESP32C6 nem elérhető, a proxy 503-as hibaüzenetet ad vissza. Az ESP32C6 újraindítása után a proxy automatikusan visszacsatlakozik.

**Eltávolítás:**
```bash
sudo systemctl stop esp32-proxy
sudo systemctl disable esp32-proxy
sudo rm /etc/systemd/system/esp32-proxy.service
sudo systemctl daemon-reload
```

---

## ESP32C6 firmware frissítése (távoli flash)

Az ESP32C6 az Orange Pi USB portjára csatlakoztatva távolról is felflashelhető.

**Előkészítés** – másold fel az új firmware fájlokat az Orange Pi-re:
```bash
pscp -pw orangepi build/bootloader/bootloader.bin           orangepi@100.68.70.151:/home/orangepi/esp32firmware/
pscp -pw orangepi build/partition_table/partition-table.bin  orangepi@100.68.70.151:/home/orangepi/esp32firmware/
pscp -pw orangepi build/esp32c6_zigbee_gateway.bin           orangepi@100.68.70.151:/home/orangepi/esp32firmware/
pscp -pw orangepi build/storage.bin                          orangepi@100.68.70.151:/home/orangepi/esp32firmware/
```

**Flashelés** – SSH-n keresztül (egy sorban, távolról):
```bash
ssh orangepi@100.68.70.151 'bash /home/orangepi/esp32/flash_esp32.sh'
```

Vagy közvetlenül:
```bash
ssh orangepi@100.68.70.151 '~/.local/bin/esptool --chip esp32c6 --port /dev/ttyACM0 --baud 460800 write_flash --flash_mode dio --flash_freq 80m --flash_size 4MB 0x0 esp32firmware/bootloader.bin 0x8000 esp32firmware/partition-table.bin 0x10000 esp32firmware/esp32c6_zigbee_gateway.bin 0x1f5000 esp32firmware/storage.bin'
```

A script automatikusan:
1. Leállítja a serial monitort (screen) – felszabadítja a portot
2. Felírja a firmware-t (bootloader + partíciós tábla + app + storage)
3. Újraindítja a serial monitort

**Soros log élőben** (terminálban):
```bash
tail -f /tmp/esp32_serial.log
```

**Interaktív soros konzol** (be is lehet gépelni az ESP32-nek):
```bash
screen -r esp32serial
# Kilépés konzolból (de nem állítja le): Ctrl+A, majd D
```

**Serial monitor státusz:**
```bash
systemctl status esp32-serial
sudo systemctl restart esp32-serial
```

---

### ESP32 proxy weboldalak frissítése

A proxy a `CLCode01/web/` fájljainak módosított másolatát szolgálja ki (`scripts/esp32-web/`).
Ha az ESP32 firmware webes felülete megváltozik, a proxy fájljait szinkronizálni kell.

**Automatikusan** (Time_lapse projekt gyökeréből futtatva):
```bash
./scripts/sync_proxy_web.sh
```

A script elvégzi:
1. Változatlan fájlok (`ble-service.js`, `style.css`, stb.) egyszerű másolása
2. `script.js`: másolás + `let bleConnected` / `let zigbeeActive` → `var` csere
   *(szükséges, hogy a proxy beállíthassa ezeket a változókat)*
3. `index.html`: másolás + `<script src="proxy-patch.js">` sor visszarakása

Majd deploy az Orange Pi-ra (a script a végén kiírja a pontos parancsokat):
```bash
pscp -pw orangepi scripts/esp32-web/*.js   orangepi@100.68.70.151:/home/orangepi/esp32/web/
pscp -pw orangepi scripts/esp32-web/*.css  orangepi@100.68.70.151:/home/orangepi/esp32/web/
pscp -pw orangepi scripts/esp32-web/*.html orangepi@100.68.70.151:/home/orangepi/esp32/web/
pscp -pw orangepi scripts/esp32-web/*.png  orangepi@100.68.70.151:/home/orangepi/esp32/web/
plink -pw orangepi -batch orangepi@100.68.70.151 "echo orangepi | sudo -S systemctl restart esp32-proxy"
```

**A `proxy-patch.js` fájlt soha nem kell szinkronizálni** – az csak a proxy saját logikáját tartalmazza, nincs megfelelője a CLCode01 projektben.

---

## Azonnali renderelés (meglévő képekből)

Ha nem akarsz várni az éjféli automatikus compile-ra:

```bash
# Renderelés automatikus névvel (archívba kerül)
/home/orangepi/timelapse/scripts/render_now.sh

# Renderelés egyéni kimeneti fájlnévvel
/home/orangepi/timelapse/scripts/render_now.sh --output /mnt/timelapse/archive/sajat_nev.mp4
```

A ramdisken (`/tmp/timelapse_frames/`) lévő összes képet feldolgozza. A képek a renderelés után **megmaradnak** – csak az éjféli `compile_daily.sh` törli őket.

---

## Gyorslista

```bash
# Azonnali renderelés (meglévő képekből)
/home/orangepi/timelapse/scripts/render_now.sh

# Preview indítása / leállítása
/home/orangepi/timelapse/scripts/preview_start.sh
/home/orangepi/timelapse/scripts/preview_stop.sh

# Timelapse indítása / leállítása
/home/orangepi/timelapse/scripts/start_timelapse.sh
/home/orangepi/timelapse/scripts/stop_timelapse.sh

# Státusz
ls /tmp/timelapse_active && echo "RÖGZÍTÉS: FUT" || echo "RÖGZÍTÉS: NEM FUT"

# Élő log
tail -f /home/orangepi/timelapse/logs/capture.log

# Hány kép van a ramdisken?
ls /tmp/timelapse_frames/ | wc -l

# Pendrive szabad hely
df -h /mnt/timelapse
```

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

**Elérés:** http://100.68.70.151:8082/  (NordVPN Meshnet)

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

# Timelapse Rendszer – Orange Pi Zero 3

Logitech C920 webkamera percenként képet készít, ezekből naponta timelapse videó generálódik,
amely hozzáfűződik egy kumulatív master videóhoz.

**Rendszer:** Armbian bookworm, 3.8GB RAM, 56GB SD
**IP:** 192.168.68.177
**HTTP elérés:** http://192.168.68.177:8080/

---

## Architektúra

```
[C920 /dev/video0]
      │ percenként (cron)
      ▼
/tmp/timelapse_frames/        ← tmpfs ramdisk (elvész reboot-nál – elfogadott)

[éjféli compile cron 00:05] ──→ /mnt/timelapse/archive/YYYY-MM-DD.mp4
                                 + master.mp4 újraépítés (concat)

[Python HTTP server :8080]  ←── /mnt/timelapse/  (USB, vagy SD fallback)
```

---

## Fájlstruktúra

```
scripts/
├── config.sh            ← közös konfiguráció (időablak, útvonalak, ffmpeg params)
├── capture.sh           ← fswebcam, percenként, csak ha aktív + időablakban
├── compile_daily.sh     ← napi videó + master concat, éjfél 00:05-kor
├── render_now.sh        ← azonnali renderelés a ramdisken lévő képekből
├── start_timelapse.sh   ← rögzítés kézi indítása (paraméterekkel)
├── stop_timelapse.sh    ← rögzítés kézi leállítása
├── storage_select.sh    ← tárhely kiválasztása / átváltása (interaktív menü)
├── preview_start.sh     ← élő kamera preview indítása (:8081)
└── preview_stop.sh      ← élő kamera preview leállítása

cron.d/
└── timelapse          ← /etc/cron.d/timelapse-be másolandó

systemd/
└── timelapse-http.service
```

### Orange Pi telepítési helyek

| Fájl | Hely |
|---|---|
| `scripts/*` | `/home/orangepi/timelapse/scripts/` |
| `cron.d/timelapse` | `/etc/cron.d/timelapse` |
| `systemd/timelapse-http.service` | `/etc/systemd/system/timelapse-http.service` |

---

## Tárhely stratégia

| Adat | Hely | Típus |
|---|---|---|
| Nyers képek | `/tmp/timelapse_frames/` | tmpfs (RAM) |
| Napi videók + master | `/mnt/timelapse/` | USB pendrive/SSD (elsődleges) |
| Fallback | `/home/orangepi/timelapse/videos/` | SD kártya |

A scriptek automatikusan detektálják, hogy `/mnt/timelapse/` elérhető-e.

---

## Telepítési sorrend

```bash
# 1. USB meghajtó UUID lekérése
blkid /dev/sda1

# 2. /etc/fstab bejegyzés (UUID helyettesítendő)
echo 'UUID=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX /mnt/timelapse ext4 defaults,nofail 0 2' \
  | sudo tee -a /etc/fstab
sudo mkdir -p /mnt/timelapse
sudo mount -a

# 3. Könyvtárak létrehozása
mkdir -p /home/orangepi/timelapse/{scripts,logs,videos}
mkdir -p /mnt/timelapse/archive
mkdir -p /tmp/timelapse_frames

# 4. Scriptek másolása
cp scripts/* /home/orangepi/timelapse/scripts/
chmod 755 /home/orangepi/timelapse/scripts/*.sh

# 5. Teszt: fswebcam
fswebcam --device /dev/video0 --resolution 1920x1080 --skip 5 /tmp/test.jpg

# 6. Systemd HTTP service
sudo cp systemd/timelapse-http.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now timelapse-http

# 7. Cron
sudo cp cron.d/timelapse /etc/cron.d/
sudo chmod 644 /etc/cron.d/timelapse

# 8. Ellenőrzés
systemctl status timelapse-http
curl http://localhost:8080/
```

---

## Használat – rögzítés indítása/leállítása

A timelapse **nem indul el automatikusan** bootoláskor. Manuálisan kell indítani:

```bash
# Rögzítés indítása (alapértelmezett beállításokkal)
/home/orangepi/timelapse/scripts/start_timelapse.sh

# Rögzítés indítása egyéni paraméterekkel
/home/orangepi/timelapse/scripts/start_timelapse.sh --start 08:00 --end 16:00
/home/orangepi/timelapse/scripts/start_timelapse.sh --storage /home/orangepi/timelapse/videos
/home/orangepi/timelapse/scripts/start_timelapse.sh --start 07:30 --end 19:00 --storage /mnt/timelapse

# Súgó
/home/orangepi/timelapse/scripts/start_timelapse.sh --help

# Rögzítés leállítása
/home/orangepi/timelapse/scripts/stop_timelapse.sh

# Státusz ellenőrzés
ls /tmp/timelapse_active && echo "FUT" || echo "NEM FUT"

# Élő log követés
tail -f /home/orangepi/timelapse/logs/capture.log
```

**Paraméterek:**

| Paraméter | Leírás | Alapértelmezett |
|---|---|---|
| `--start HH:MM` | Rögzítés kezdete | `06:00` |
| `--end HH:MM` | Rögzítés vége | `20:00` |
| `--interval SEC` | Képek közötti szünet (mp.) | `60` |
| `--storage PATH` | Videók mentési helye | auto (USB vagy SD fallback) |

**Hogyan működik:**
- `start_timelapse.sh` létrehoz egy `/tmp/timelapse_active` flag fájlt és egy `/tmp/timelapse_session.conf` fájlt a session paramétereivel
- A cron percenként futtatja a `capture.sh`-t, de az csak akkor rögzít, ha a flag fájl létezik **és** az aktuális idő az időablakon belül van
- `stop_timelapse.sh` törli a flag fájlt → a következő cron futás már nem rögzít
- Reboot után a `/tmp` tmpfs ürül, tehát a rögzítés automatikusan **nem** indul újra

---

## Élő kamera preview

Beállításhoz vagy ellenőrzéshez – nem fut folyamatosan, csak igény szerint:

```bash
# Preview indítása
/home/orangepi/timelapse/scripts/preview_start.sh

# Preview leállítása
/home/orangepi/timelapse/scripts/preview_stop.sh
```

**Elérés böngészőből:** `http://192.168.68.177:8081/stream`

- Felbontás: 640×480 @ 5fps (butított – SD kártya és CPU kímélése)
- A timelapse rögzítéssel párhuzamosan is futhat, de feleslegesen ne hagyd bekapcsolva
- Eszköz: `ustreamer` (telepítve: `sudo apt install ustreamer`)

---

## Kapacitásszámítás

| Felbontás | ~KB/kép | Nap végén (~840 kép, 06–20h) |
|---|---|---|
| 1920×1080 | ~350 KB | ~290 MB |
| 1280×720 | ~150 KB | ~125 MB |

- /tmp: 2GB (RAM 50%) → 1080p is elfér egy napig
- Napi videó: ~50–100 MB/nap (H.264, ~35 mp @ 24fps)
- 1 év archívum: ~18–36 GB (56 GB SD-ből elfér)

---

## Verifikáció

```bash
# Kézi capture teszt
/home/orangepi/timelapse/scripts/capture.sh
ls -lh /tmp/timelapse_frames/

# Kézi compile (néhány kép után)
/home/orangepi/timelapse/scripts/compile_daily.sh
ls -lh /mnt/timelapse/archive/

# HTTP szerver
curl http://192.168.68.177:8080/

# Logok
tail -f /home/orangepi/timelapse/logs/capture.log
tail -f /home/orangepi/timelapse/logs/compile.log
```

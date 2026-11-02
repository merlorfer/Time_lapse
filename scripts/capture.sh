#!/bin/bash
# =============================================================================
# capture.sh – Egyetlen képkocka rögzítése kameránként
# Cron: * * * * * orangepi /home/orangepi/timelapse/scripts/capture.sh
#
# Paraméter nélkül minden kamerára külön, párhuzamosan elindítja magát (egy lassú IP kamera
# így nem tartja fel az USB kamerát). Használat egy kamerára: capture.sh <kamera>
# =============================================================================

source "$(dirname "$0")/config.sh"
LOG="${LOG_DIR}/capture.log"
mkdir -p "$LOG_DIR"

if [ -z "$1" ]; then
    for cam in $(python3 "${SCRIPT_DIR}/cameras.py" names); do
        bash "$0" "$cam" &
    done
    wait
    exit 0
fi

load_camera "$1" || exit 1
mkdir -p "$FRAME_DIR"
START_TS=$(date +%s)   # a ciklus (cron) kezdete: ez számít a képek közti intervallumnak

# --- Manuális indítás ellenőrzés (flag fájl) ---
if [ ! -f "$ACTIVE_FLAG" ]; then
    exit 0
fi

# --- Időablak ellenőrzés ---
# Az éjfélen átnyúló ablakot is kezeli (pl. 22:00–06:00), és a 00:00 végpontot is
# (08:00–00:00 = reggel 8-tól éjfélig). A HH:MM sztringek összehasonlíthatók.
in_capture_window() {
    local now="$1" start="$2" end="$3"
    if [[ "$start" < "$end" || "$start" == "$end" ]]; then
        [[ ! "$now" < "$start" && ! "$now" > "$end" ]]
    else
        [[ ! "$now" < "$start" || ! "$now" > "$end" ]]
    fi
}

NOW=$(date +%H:%M)
if ! in_capture_window "$NOW" "$CAPTURE_START" "$CAPTURE_END"; then
    exit 0
fi

# --- Átfedés elleni zár (kameránként) ---
# Az előzetes parancs + várakozás miatt egy futás hosszabb lehet egy percnél; a következő
# percben induló futás addig vár (max. 20 mp), nehogy két rögzítés keveredjen.
exec 9>"$CAPTURE_LOCK"
flock -w 20 9 || exit 0

# --- Intervallum ellenőrzés ---
if [ -f "$LAST_CAPTURE_FILE" ]; then
    LAST_TS=$(cat "$LAST_CAPTURE_FILE")
    NOW_TS=$(date +%s)
    ELAPSED=$(( NOW_TS - LAST_TS ))
    if [ "$ELAPSED" -lt $(( CAPTURE_INTERVAL - 5 )) ]; then
        exit 0
    fi
fi

# --- Ramdisk szabad hely ellenőrzés ---
FREE_MB=$(df -m "$FRAME_DIR" | awk 'NR==2 {print $4}')
if [ "$FREE_MB" -lt "$RAMDISK_MIN_FREE_MB" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [$CAM] WARNING: Ramdisk low space (${FREE_MB}MB free), skipping capture" >> "$LOG"
    exit 1
fi

# --- Képrögzítés előtti parancs (pl. ventilátor leállítása), lezáró parancs a kép után ---
# A lezáró parancs minden kilépési úton kimegy (EXIT trap); ha mégsem (áramszünet, kill),
# a következő ciklus a tartósan tárolt függő parancsot küldi el először (lásd precapture.py).
PRECAP="${SCRIPT_DIR}/precapture.py"
if [ -f "$PRECAP" ]; then
    trap 'python3 "$PRECAP" end --camera "$CAM" >> "$LOG" 2>&1' EXIT
    python3 "$PRECAP" start --camera "$CAM" --max-lead "$CAPTURE_INTERVAL" >> "$LOG" 2>&1
fi

# --- Képrögzítés ---
TIMESTAMP=$(date '+%Y-%m-%d_%H%M%S')
OUTPUT="${FRAME_DIR}/${TIMESTAMP}.jpg"
ERRFILE=$(mktemp /tmp/timelapse_capture_err_XXXXXX)

# Az időbélyeg szűrője: a betűméret a kép magasságához igazodik (FONT_SIZE = 1080p-s kép mellett)
OVERLAY_FILTER=""
if [ "$OVERLAY" = "1" ]; then
    DISPLAY_TS=$(date '+%Y-%m-%d %H\:%M\:%S')
    OVERLAY_FILTER="drawtext=fontfile=${FONT_FILE}:text='${DISPLAY_TS}':fontsize=h*${FONT_SIZE}/1080:fontcolor=white:shadowcolor=black@0.8:shadowx=2:shadowy=2:x=w*20/1920:y=h*15/1080:box=1:boxcolor=black@0.6:boxborderw=12"
fi

if [ "$CAM_TYPE" = "ip" ]; then
    # IP kamera: egy képkocka az RTSP fő streamből; a méretezés és az időbélyeg egy menetben
    VF=""
    [ "$CAPTURE_RESOLUTION" != "native" ] && VF="scale=${CAPTURE_RESOLUTION}"
    if [ -n "$OVERLAY_FILTER" ]; then
        VF="${VF:+${VF},}${OVERLAY_FILTER}"
    fi
    QV=$(( 31 - JPEG_QUALITY * 29 / 100 ))     # 1..100 minőség → ffmpeg -q:v (2 = legjobb, 31 = leggyengébb)
    [ "$QV" -lt 2 ] && QV=2
    FFARGS=(-v error -y -rtsp_transport tcp -i "$RTSP_URL_MAIN" -frames:v 1 -q:v "$QV")
    [ -n "$VF" ] && FFARGS+=(-vf "$VF")
    timeout 40 ffmpeg "${FFARGS[@]}" "$OUTPUT" 2> "$ERRFILE"
    CAPTURE_OK=$?
    METHOD="rtsp"
else
    # USB kamera: ha a preview fut, ustreamer /snapshot endpointból vesszük a képet (kamera
    # megosztva), egyébként fswebcam nyúl a kamerához közvetlenül.
    PREVIEW_PIDFILE="/tmp/timelapse_preview.pid"
    PREVIEW_RUNNING=false
    if [ -f "$PREVIEW_PIDFILE" ] && kill -0 "$(cat "$PREVIEW_PIDFILE")" 2>/dev/null; then
        PREVIEW_RUNNING=true
    fi

    WEBCAM_RES="$CAPTURE_RESOLUTION"
    [ "$WEBCAM_RES" = "native" ] && WEBCAM_RES="1920x1080"

    if $PREVIEW_RUNNING; then
        curl -sf "http://localhost:8081/snapshot" -o "$OUTPUT" 2>> "$ERRFILE"
        CAPTURE_OK=$?
        METHOD="ustreamer snapshot"
    else
        fswebcam \
            --device "$CAMERA_DEVICE" \
            --resolution "$WEBCAM_RES" \
            --skip "$CAMERA_SKIP" \
            --jpeg "$JPEG_QUALITY" \
            --no-banner \
            "$OUTPUT" 2>> "$ERRFILE"
        CAPTURE_OK=$?
        METHOD="fswebcam"
    fi

    # Időbélyeg (ffmpeg drawtext) – külön lépés, mert a webkamera képét már kész JPEG-ként kapjuk
    if [ $CAPTURE_OK -eq 0 ] && [ -n "$OVERLAY_FILTER" ]; then
        TMP_OUTPUT="/tmp/timelapse_overlay_$$.jpg"
        ffmpeg -v error -y -i "$OUTPUT" -vf "$OVERLAY_FILTER" -f mjpeg "$TMP_OUTPUT" >> "$ERRFILE" 2>&1 \
            && mv "$TMP_OUTPUT" "$OUTPUT" || rm -f "$TMP_OUTPUT"
    fi
fi

if [ $CAPTURE_OK -ne 0 ] || [ ! -s "$OUTPUT" ]; then
    rm -f "$OUTPUT"
    REASON=$(grep -v '^[[:space:]]*$' "$ERRFILE" | tail -1 | cut -c1-200)
    cat "$ERRFILE" >> "$LOG"
    rm -f "$ERRFILE"
    echo "$(date '+%Y-%m-%d %H:%M:%S') [$CAM] ERROR: capture failed [${METHOD}] ${REASON}" >> "$LOG"
    python3 "${SCRIPT_DIR}/cameras.py" capture-result "$CAM" fail "${REASON:-${METHOD} sikertelen}" >> "$LOG" 2>&1
    exit 1
fi
cat "$ERRFILE" >> "$LOG"
rm -f "$ERRFILE"

echo "$START_TS" > "$LAST_CAPTURE_FILE"
echo "$(date '+%Y-%m-%d %H:%M:%S') [$CAM] Captured: $OUTPUT [${METHOD}] (${FREE_MB}MB free)" >> "$LOG"
python3 "${SCRIPT_DIR}/cameras.py" capture-result "$CAM" ok >> "$LOG" 2>&1

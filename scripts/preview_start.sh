#!/bin/bash
# =============================================================================
# preview_start.sh – Élő kamera preview indítása (ustreamer, :8081)
# Böngészőből: http://<IP>:8081/stream
# Leállítás:   preview_stop.sh
# =============================================================================

source "$(dirname "$0")/config.sh"

PREVIEW_PORT=8081
PREVIEW_RES="1920x1080"
PREVIEW_FPS=15
PIDFILE="/tmp/timelapse_preview.pid"

if [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
    echo "Preview már fut (PID: $(cat $PIDFILE))"
    echo "Elérés: http://$(hostname -I | awk '{print $1}'):${PREVIEW_PORT}/stream"
    exit 0
fi

nohup ustreamer \
    --device "$CAMERA_DEVICE" \
    --resolution "$PREVIEW_RES" \
    --desired-fps "$PREVIEW_FPS" \
    --format MJPEG \
    --port "$PREVIEW_PORT" \
    --host 0.0.0.0 \
    > /tmp/timelapse_preview.log 2>&1 &

echo $! > "$PIDFILE"
sleep 2

if kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
    IP=$(hostname -I | awk '{print $1}')
    echo "Preview elindítva."
    echo "  Elérés: http://${IP}:${PREVIEW_PORT}/stream"
    echo "  Felbontás: ${PREVIEW_RES} @ ${PREVIEW_FPS}fps"
    echo "Leállítás: preview_stop.sh"
else
    echo "ERROR: ustreamer nem indult el."
    exit 1
fi

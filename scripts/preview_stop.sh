#!/bin/bash
# =============================================================================
# preview_stop.sh – Élő kamera preview leállítása
# =============================================================================

PIDFILE="/tmp/timelapse_preview.pid"

if [ ! -f "$PIDFILE" ]; then
    echo "Preview nem fut."
    exit 0
fi

PID=$(cat "$PIDFILE")

if kill "$PID" 2>/dev/null; then
    rm -f "$PIDFILE"
    echo "Preview leállítva (PID: ${PID})."
else
    echo "Preview már nem fut, PID fájl törölve."
    rm -f "$PIDFILE"
fi

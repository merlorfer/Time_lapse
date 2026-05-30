#!/bin/bash
# =============================================================================
# webui_stop.sh – WebUI szolgáltatások leállítása
# A timelapse felvétel (cron) fut tovább.
# =============================================================================

SERVICES="esp32-proxy esp32-serial timelapse-web timelapse-http"

echo "WebUI szolgáltatások leállítása..."
for svc in $SERVICES; do
    if systemctl is-active --quiet "$svc"; then
        sudo systemctl stop "$svc"
        echo "  Leállítva: $svc"
    else
        echo "  Már leállt: $svc"
    fi
done
echo "Kész."

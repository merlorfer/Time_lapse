#!/bin/bash
# =============================================================================
# webui_start.sh – WebUI szolgáltatások elindítása
# =============================================================================

SERVICES="esp32-serial esp32-proxy timelapse-web timelapse-http"

echo "WebUI szolgáltatások indítása..."
for svc in $SERVICES; do
    if systemctl is-active --quiet "$svc"; then
        echo "  Már fut: $svc"
    else
        sudo systemctl start "$svc"
        echo "  Elindítva: $svc"
    fi
done
echo "Kész."

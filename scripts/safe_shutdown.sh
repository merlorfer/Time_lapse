#!/bin/bash
# =============================================================================
# safe_shutdown.sh – Adatok mentése, majd leállítás
# Ugyanaz a mentés, mint a safe_reboot.sh-ban (képkockák, szenzor adatok,
# session), a végén azonban leállítja az Orange Pi-t. Újraindítás csak
# áramtalanítással lehetséges.
# =============================================================================
export SAFE_ACTION=poweroff
exec bash "$(dirname "$(realpath "$0")")/safe_reboot.sh"

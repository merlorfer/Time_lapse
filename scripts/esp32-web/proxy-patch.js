/**
 * proxy-patch.js  –  Orange Pi BLE Proxy mode
 *
 * Loaded AFTER script.js. Overrides BLE-specific functions so that:
 *  - The browser never tries to use Web Bluetooth
 *  - All /api/* calls go to the Orange Pi proxy via plain HTTP (relative URLs)
 *  - The "BLE connect" button is replaced with a proxy status indicator
 */

(function () {
    'use strict';

    // ── 1. Force HTTP-only mode ───────────────────────────────────────────────
    //
    // The original apiRequest() routes to bleRequest() when bleConnected===true.
    // In proxy mode we always use httpRequest() (plain fetch to the same origin).

    // Ensure bleConnected is never set to true from the outside
    Object.defineProperty(window, 'bleConnected', {
        get: () => false,
        set: () => {},   // silently ignore any attempt to set it
        configurable: false
    });

    // ── 2. Replace connectBLE / disconnectBLE with no-ops ────────────────────

    window.connectBLE = function () {
        showToast('Proxy mód: az Orange Pi kezeli a BLE kapcsolatot', false);
    };

    window.disconnectBLE = function () {
        // nothing to do
    };

    // ── 3. Poll /api/ble-status and show it in the connect button area ────────

    async function updateProxyStatus() {
        let bleOk = false;
        try {
            const r = await fetch('/api/ble-status');
            if (r.ok) {
                const d = await r.json();
                bleOk = d.connected === true;
            }
        } catch (_) {}

        const label = bleOk ? 'ESP32 csatlakozva (BLE)' : 'ESP32 nincs csatlakozva';
        const cls   = bleOk ? 'connected' : 'disconnected';

        // Update every BLE indicator the original UI has
        ['ble-connect-btn', 'modal-ble-connect-btn', 'sticky-ble-connect-btn'].forEach(id => {
            const el = document.getElementById(id);
            if (!el) return;
            el.textContent = label;
            el.disabled = true;
            el.style.cursor = 'default';
        });

        ['ble-status', 'modal-ble-status', 'sticky-ble-status'].forEach(id => {
            const el = document.getElementById(id);
            if (!el) return;
            const dot = el.querySelector('.status-dot');
            if (dot) {
                dot.className = 'status-dot ' + cls;
            }
            const txt = el.querySelector('.status-text');
            if (txt) {
                txt.textContent = label;
            }
        });

        // Also update via the original updateBLEStatus if it exists
        if (typeof updateBLEStatus === 'function') {
            updateBLEStatus(bleOk, label);
        }
    }

    // ── 4. Proxy mode banner ──────────────────────────────────────────────────

    function injectProxyBanner() {
        const banner = document.createElement('div');
        banner.id = 'proxy-mode-banner';
        banner.style.cssText =
            'position:fixed;bottom:0;left:0;right:0;z-index:9999;' +
            'background:#1a3a5c;color:#7ec8f0;font-size:12px;' +
            'text-align:center;padding:4px 8px;pointer-events:none;';
        banner.textContent = '🔌 Orange Pi Proxy mód – BLE az Orange Pi kezeli';
        document.body.appendChild(banner);
    }

    // ── 5. Boot ───────────────────────────────────────────────────────────────

    document.addEventListener('DOMContentLoaded', function () {
        injectProxyBanner();
        updateProxyStatus();
        setInterval(updateProxyStatus, 5000);

        // Hide BLE-only UI elements that don't make sense in proxy mode
        const bleDisconnectBtn = document.getElementById('ble-disconnect-btn');
        if (bleDisconnectBtn) bleDisconnectBtn.style.display = 'none';
        const modalDisconnectBtn = document.getElementById('modal-ble-disconnect-btn');
        if (modalDisconnectBtn) modalDisconnectBtn.style.display = 'none';
        const stickyDisconnectBtn = document.getElementById('sticky-ble-disconnect-btn');
        if (stickyDisconnectBtn) stickyDisconnectBtn.style.display = 'none';
    });

})();

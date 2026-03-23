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
        banner.textContent = '\uD83D\uDD0C Orange Pi Proxy mód – BLE az Orange Pi kezeli';
        document.body.appendChild(banner);
    }

    // ── 5. Serial log panel ───────────────────────────────────────────────────

    let _serialSince = 0;
    let _serialPaused = false;

    function injectSerialPanel() {
        const panel = document.createElement('div');
        panel.id = 'serial-log-panel';
        panel.innerHTML = `
            <div id="serial-log-header" style="
                display:flex;align-items:center;gap:8px;
                background:#1e1e2e;border-bottom:1px solid #333;
                padding:6px 12px;cursor:pointer;user-select:none;">
              <span id="serial-log-dot" style="width:8px;height:8px;border-radius:50%;background:#555;display:inline-block;flex-shrink:0;"></span>
              <span style="font-size:13px;font-weight:600;color:#cdd6f4;flex:1;">Soros port log (/dev/ttyACM0)</span>
              <button id="serial-log-pause" style="font-size:11px;padding:2px 8px;background:#313244;color:#cdd6f4;border:1px solid #555;border-radius:4px;cursor:pointer;">Szünet</button>
              <button id="serial-log-clear" style="font-size:11px;padding:2px 8px;background:#313244;color:#cdd6f4;border:1px solid #555;border-radius:4px;cursor:pointer;">Törlés</button>
              <span id="serial-log-toggle" style="color:#888;font-size:16px;">▲</span>
            </div>
            <div id="serial-log-body" style="
                height:200px;overflow-y:auto;background:#11111b;
                font-family:monospace;font-size:12px;color:#a6e3a1;
                padding:6px 12px;box-sizing:border-box;">
            </div>`;
        panel.style.cssText =
            'position:fixed;bottom:22px;left:0;right:0;z-index:9998;' +
            'border-top:2px solid #313244;box-shadow:0 -2px 12px #0008;';
        document.body.appendChild(panel);

        // Toggle collapse
        let collapsed = false;
        document.getElementById('serial-log-header').addEventListener('click', function (e) {
            if (e.target.tagName === 'BUTTON') return;
            collapsed = !collapsed;
            const body = document.getElementById('serial-log-body');
            body.style.display = collapsed ? 'none' : 'block';
            document.getElementById('serial-log-toggle').textContent = collapsed ? '▼' : '▲';
            document.getElementById('proxy-mode-banner').style.bottom = collapsed ? '28px' : '222px';
        });

        document.getElementById('serial-log-pause').addEventListener('click', function () {
            _serialPaused = !_serialPaused;
            this.textContent = _serialPaused ? 'Folytatás' : 'Szünet';
            this.style.color = _serialPaused ? '#f38ba8' : '#cdd6f4';
        });

        document.getElementById('serial-log-clear').addEventListener('click', function () {
            document.getElementById('serial-log-body').innerHTML = '';
        });

        // Shift bottom banner up
        document.getElementById('proxy-mode-banner').style.bottom = '222px';
    }

    function _colorize(msg) {
        if (/\bE\b|\bERROR\b|error|Error/i.test(msg))   return '#f38ba8';
        if (/\bW\b|\bWARN\b|warn/i.test(msg))            return '#fab387';
        if (/\bI\b|\bINFO\b|\[BLE\]|\[HTTP\]/i.test(msg)) return '#89dceb';
        if (/\bD\b|\bDEBUG\b/i.test(msg))                return '#6c7086';
        return '#a6e3a1';
    }

    async function pollSerialLogs() {
        if (_serialPaused) return;
        try {
            const r = await fetch('/api/serial-logs?since=' + _serialSince);
            if (!r.ok) return;
            const d = await r.json();

            const dot = document.getElementById('serial-log-dot');
            if (dot) dot.style.background = d.available ? '#a6e3a1' : '#f38ba8';

            if (d.lines && d.lines.length > 0) {
                _serialSince = d.total;
                const body = document.getElementById('serial-log-body');
                if (!body) return;
                const atBottom = body.scrollHeight - body.scrollTop <= body.clientHeight + 40;
                d.lines.forEach(function (entry) {
                    const line = document.createElement('div');
                    line.style.cssText = 'padding:1px 0;border-bottom:1px solid #1e1e2e;white-space:pre-wrap;word-break:break-all;';
                    line.innerHTML =
                        '<span style="color:#6c7086;">' + entry.t + '</span> ' +
                        '<span style="color:' + _colorize(entry.msg) + ';">' +
                        entry.msg.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') +
                        '</span>';
                    body.appendChild(line);
                    // Keep max 300 lines in DOM
                    while (body.children.length > 300) body.removeChild(body.firstChild);
                });
                if (atBottom) body.scrollTop = body.scrollHeight;
            }
        } catch (_) {}
    }

    // ── 6. Boot ───────────────────────────────────────────────────────────────

    document.addEventListener('DOMContentLoaded', function () {
        injectProxyBanner();
        injectSerialPanel();
        updateProxyStatus();
        setInterval(updateProxyStatus, 5000);
        setInterval(pollSerialLogs, 2000);

        // Hide BLE-only UI elements that don't make sense in proxy mode
        const bleDisconnectBtn = document.getElementById('ble-disconnect-btn');
        if (bleDisconnectBtn) bleDisconnectBtn.style.display = 'none';
        const modalDisconnectBtn = document.getElementById('modal-ble-disconnect-btn');
        if (modalDisconnectBtn) modalDisconnectBtn.style.display = 'none';
        const stickyDisconnectBtn = document.getElementById('sticky-ble-disconnect-btn');
        if (stickyDisconnectBtn) stickyDisconnectBtn.style.display = 'none';
    });

})();

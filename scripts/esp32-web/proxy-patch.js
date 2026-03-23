/**
 * proxy-patch.js  –  Orange Pi BLE Proxy mode
 *
 * Loaded AFTER script.js (which declares bleConnected/zigbeeActive as `var`
 * in the proxy copy, making them window properties settable from here).
 *
 *  - All /api/* calls go via HTTP to the Orange Pi proxy (never Web Bluetooth)
 *  - BLE connect/disconnect buttons in sticky header control proxy BLE state
 *  - bleConnected / zigbeeActive are synced from /api/ble-status + /api/status
 *  - Serial log drawer accessible via "Soros log" button in sticky header
 */

(function () {
    'use strict';

    // ── 1. Always route via HTTP ──────────────────────────────────────────────

    window.apiRequest = async function (endpoint, method = 'GET', body = null) {
        return await httpRequest(endpoint, method, body);
    };

    window.connectBLE    = function () {};
    window.disconnectBLE = function () {};

    // ── 2. BLE connect / disconnect buttons ──────────────────────────────────

    let _bleConnecting = false;

    async function proxyBleConnect() {
        if (_bleConnecting) return;
        _bleConnecting = true;
        _setBleButtonState('connecting');
        try {
            const r = await fetch('/api/ble-connect', { method: 'POST' });
            const d = await r.json();
            if (!d.ok) throw new Error(d.msg || 'Hiba');
        } catch (e) {
            if (typeof showToast === 'function') showToast('BLE csatlakozás sikertelen: ' + e.message, true);
        } finally {
            _bleConnecting = false;
            await updateProxyStatus();
        }
    }

    async function proxyBleDisconnect() {
        try { await fetch('/api/ble-disconnect', { method: 'POST' }); } catch (_) {}
        await updateProxyStatus();
    }

    function _setBleButtonState(state) {
        const connectBtn    = document.getElementById('proxy-ble-connect-btn');
        const disconnectBtn = document.getElementById('proxy-ble-disconnect-btn');
        const dot           = document.getElementById('proxy-ble-dot');
        const label         = document.getElementById('proxy-ble-label');
        if (!connectBtn) return;
        if (state === 'connected') {
            connectBtn.style.display    = 'none';
            disconnectBtn.style.display = '';
            if (dot)   dot.style.background = '#a6e3a1';
            if (label) label.textContent    = 'ESP32 csatlakozva';
        } else if (state === 'connecting') {
            connectBtn.disabled    = true;
            connectBtn.textContent = 'Csatlakozás…';
            if (dot)   dot.style.background = '#fab387';
            if (label) label.textContent    = 'Csatlakozás…';
        } else {
            connectBtn.style.display    = '';
            connectBtn.disabled         = false;
            connectBtn.textContent      = 'BLE csatlakozás';
            disconnectBtn.style.display = 'none';
            if (dot)   dot.style.background = '#f38ba8';
            if (label) label.textContent    = 'ESP32 nincs csatlakozva';
        }
    }

    function injectBleButtons() {
        const actions = document.querySelector('.ble-actions');
        if (!actions) return;

        ['ble-connect-btn', 'modal-ble-connect-btn', 'sticky-ble-connect-btn',
         'ble-disconnect-btn', 'modal-ble-disconnect-btn', 'sticky-ble-disconnect-btn'
        ].forEach(id => { const el = document.getElementById(id); if (el) el.style.display = 'none'; });

        const status = document.createElement('span');
        status.style.cssText = 'display:flex;align-items:center;gap:5px;font-size:12px;color:#cdd6f4;';
        status.innerHTML =
            '<span id="proxy-ble-dot" style="width:7px;height:7px;border-radius:50%;background:#f38ba8;display:inline-block;flex-shrink:0;"></span>' +
            '<span id="proxy-ble-label">ESP32 nincs csatlakozva</span>';

        const connectBtn = document.createElement('button');
        connectBtn.id        = 'proxy-ble-connect-btn';
        connectBtn.className = 'btn btn-primary btn-small';
        connectBtn.textContent = 'BLE csatlakozás';
        connectBtn.addEventListener('click', proxyBleConnect);

        const disconnectBtn = document.createElement('button');
        disconnectBtn.id           = 'proxy-ble-disconnect-btn';
        disconnectBtn.className    = 'btn btn-secondary btn-small';
        disconnectBtn.textContent  = 'BLE leválasztás';
        disconnectBtn.style.display = 'none';
        disconnectBtn.addEventListener('click', proxyBleDisconnect);

        actions.appendChild(status);
        actions.appendChild(connectBtn);
        actions.appendChild(disconnectBtn);
    }

    // ── 3. Poll /api/ble-status – sync window.bleConnected ───────────────────

    let _lastBleOk      = null;
    let _serialAvailable = false;

    async function updateProxyStatus() {
        let bleOk = false;
        try {
            const r = await fetch('/api/ble-status');
            if (r.ok) bleOk = (await r.json()).connected === true;
        } catch (_) {}

        // Set the global var — script.js reads this directly in every check
        window.bleConnected = bleOk;

        _setBleButtonState(bleOk ? 'connected' : 'disconnected');

        if (_lastBleOk !== bleOk) {
            _lastBleOk = bleOk;
            // Reload UI so all conditional renders (canControl, canPair, status text) update
            if (typeof loadStatus  === 'function') loadStatus();
            if (typeof loadDevices === 'function') loadDevices();
        }

        const dot = document.getElementById('serial-btn-dot');
        if (dot) dot.style.background = _serialAvailable ? '#a6e3a1' : '#f38ba8';
    }

    // ── 4. Serial log drawer ──────────────────────────────────────────────────

    let _serialSince = 0;
    let _serialPaused = false;
    let _drawerOpen   = false;

    function injectSerialButton() {
        const actions = document.querySelector('.ble-actions');
        if (!actions) return;
        const btn = document.createElement('button');
        btn.id = 'serial-log-btn';
        btn.className = 'btn btn-secondary btn-small';
        btn.style.cssText = 'display:flex;align-items:center;gap:5px;';
        btn.innerHTML =
            '<span id="serial-btn-dot" style="width:7px;height:7px;border-radius:50%;background:#555;display:inline-block;flex-shrink:0;"></span>' +
            'Soros log';
        btn.addEventListener('click', toggleDrawer);
        actions.appendChild(btn);
    }

    function injectSerialDrawer() {
        const drawer = document.createElement('div');
        drawer.id = 'serial-drawer';
        drawer.style.cssText =
            'position:fixed;top:48px;right:0;width:520px;max-width:100vw;' +
            'height:calc(100vh - 48px);z-index:9990;display:none;flex-direction:column;' +
            'background:#11111b;border-left:2px solid #313244;box-shadow:-4px 0 20px #0009;';
        drawer.innerHTML = `
            <div style="display:flex;align-items:center;gap:8px;padding:8px 12px;
                        background:#1e1e2e;border-bottom:1px solid #313244;flex-shrink:0;">
              <span id="serial-drawer-dot" style="width:8px;height:8px;border-radius:50%;
                    background:#555;display:inline-block;flex-shrink:0;"></span>
              <span style="font-size:13px;font-weight:600;color:#cdd6f4;flex:1;">Soros port log &mdash; /dev/ttyACM0</span>
              <button id="serial-pause-btn" style="font-size:11px;padding:2px 8px;background:#313244;color:#cdd6f4;border:1px solid #555;border-radius:4px;cursor:pointer;">Szünet</button>
              <button id="serial-clear-btn" style="font-size:11px;padding:2px 8px;background:#313244;color:#cdd6f4;border:1px solid #555;border-radius:4px;cursor:pointer;">Törlés</button>
              <button id="serial-close-btn" style="font-size:13px;padding:2px 8px;background:transparent;color:#888;border:none;cursor:pointer;">✕</button>
            </div>
            <div id="serial-log-body" style="flex:1;overflow-y:auto;font-family:monospace;font-size:12px;color:#a6e3a1;padding:6px 12px;box-sizing:border-box;"></div>`;
        document.body.appendChild(drawer);

        document.getElementById('serial-close-btn').addEventListener('click', toggleDrawer);
        document.getElementById('serial-pause-btn').addEventListener('click', function () {
            _serialPaused = !_serialPaused;
            this.textContent = _serialPaused ? 'Folytatás' : 'Szünet';
            this.style.color = _serialPaused ? '#f38ba8' : '#cdd6f4';
        });
        document.getElementById('serial-clear-btn').addEventListener('click', function () {
            document.getElementById('serial-log-body').innerHTML = '';
        });
    }

    function toggleDrawer() {
        _drawerOpen = !_drawerOpen;
        const drawer = document.getElementById('serial-drawer');
        if (drawer) drawer.style.display = _drawerOpen ? 'flex' : 'none';
        const btn = document.getElementById('serial-log-btn');
        if (btn) btn.style.outline = _drawerOpen ? '2px solid #89dceb' : 'none';
    }

    function _colorize(msg) {
        if (/\bE\b|\bERROR\b|error|Error/.test(msg))              return '#f38ba8';
        if (/\bW\b|\bWARN\b|warn/.test(msg))                      return '#fab387';
        if (/\bI\b|\bINFO\b|\[BLE\]|\[HTTP\]|\[SERIAL\]/.test(msg)) return '#89dceb';
        if (/\bD\b|\bDEBUG\b/.test(msg))                          return '#6c7086';
        return '#a6e3a1';
    }

    async function pollSerialLogs() {
        if (_serialPaused) return;
        try {
            const r = await fetch('/api/serial-logs?since=' + _serialSince);
            if (!r.ok) return;
            const d = await r.json();
            _serialAvailable = d.available;
            const color = d.available ? '#a6e3a1' : '#f38ba8';
            ['serial-btn-dot', 'serial-drawer-dot'].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.style.background = color;
            });
            if (!d.lines || d.lines.length === 0) return;
            _serialSince = d.total;
            if (!_drawerOpen) return;
            const body = document.getElementById('serial-log-body');
            if (!body) return;
            const atBottom = body.scrollHeight - body.scrollTop <= body.clientHeight + 40;
            d.lines.forEach(function (entry) {
                const line = document.createElement('div');
                line.style.cssText = 'padding:1px 0;border-bottom:1px solid #1a1a2a;white-space:pre-wrap;word-break:break-all;';
                line.innerHTML =
                    '<span style="color:#6c7086;">' + entry.t + '</span> ' +
                    '<span style="color:' + _colorize(entry.msg) + ';">' +
                    entry.msg.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') +
                    '</span>';
                body.appendChild(line);
                while (body.children.length > 300) body.removeChild(body.firstChild);
            });
            if (atBottom) body.scrollTop = body.scrollHeight;
        } catch (_) {}
    }

    // ── 5. Boot ───────────────────────────────────────────────────────────────

    document.addEventListener('DOMContentLoaded', function () {
        injectBleButtons();
        injectSerialButton();
        injectSerialDrawer();
        updateProxyStatus();
        setInterval(updateProxyStatus, 5000);
        setInterval(pollSerialLogs, 2000);
    });

})();

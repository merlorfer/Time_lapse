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

    let _lastBleOk = null;

    async function updateProxyStatus() {
        let bleOk = false;
        try {
            const r = await fetch('/api/ble-status');
            if (r.ok) bleOk = (await r.json()).connected === true;
        } catch (_) {}

        // Set the global var — script.js reads this directly in every check
        window.bleConnected = bleOk;

        _setBleButtonState(bleOk ? 'connected' : 'disconnected');

        // Update the sticky header + "Bluetooth Kapcsolat" section immediately
        if (typeof updateBLEStatus === 'function') {
            updateBLEStatus(bleOk, bleOk ? 'Csatlakozva' : 'Nincs csatlakozva');
        }

        if (_lastBleOk !== bleOk) {
            _lastBleOk = bleOk;
            // Reload UI so all conditional renders (canControl, canPair, status text) update
            if (typeof loadStatus  === 'function') loadStatus();
            if (typeof loadDevices === 'function') loadDevices();
        }

    }

    // ── 4. Sensor config section ──────────────────────────────────────────────

    let _sensorConfig = {};

    async function loadSensorConfig() {
        try {
            const r = await fetch('/api/sensor-config');
            if (!r.ok) return;
            const d = await r.json();
            _sensorConfig = d.config || {};
            renderSensorConfig(d.config || {}, d.runtime || {});
        } catch (_) {}
    }

    async function saveSensorConfig() {
        try {
            await fetch('/api/sensor-config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(_sensorConfig)
            });
        } catch (_) {}
    }

    function renderSensorConfig(config, runtime) {
        const container = document.getElementById('proxy-sensor-config');
        if (!container) return;

        // Get devices from the page
        const deviceData = window._proxyDeviceList || [];
        if (deviceData.length === 0) {
            container.innerHTML = '<p style="color:#6c7086;font-size:12px;">Nincs ismert eszköz. Csatlakozz BLE-n az eszközök betöltéséhez.</p>';
            return;
        }

        container.innerHTML = deviceData.map(dev => {
            const ieee = dev.ieee_addr;
            const name = dev.custom_name || ieee;
            const cfg  = config[ieee] || {};
            const rt   = runtime[ieee] || {};
            const statusColor = rt.suspended ? '#f38ba8' : (rt.last_ok ? '#a6e3a1' : '#6c7086');
            const statusText  = rt.suspended
                ? 'Felfüggesztve (3 sikertelen)'
                : rt.last_ok ? 'Utolsó mentés: ' + rt.last_ok : 'Még nem mentett';

            return `<div style="display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid #313244;flex-wrap:wrap;">
              <span style="flex:1;min-width:120px;font-size:13px;font-weight:600;color:#cdd6f4;">${name}</span>
              <span style="font-size:11px;color:#6c7086;flex:2;min-width:140px;">${dev.device_type || ''}</span>
              <label style="display:flex;align-items:center;gap:6px;font-size:12px;white-space:nowrap;">
                <input type="checkbox" data-ieee="${ieee}" class="sensor-enable-chk"
                  ${cfg.enabled ? 'checked' : ''}>
                Mentés
              </label>
              <label style="display:flex;align-items:center;gap:6px;font-size:12px;white-space:nowrap;">
                Minden
                <input type="number" data-ieee="${ieee}" class="sensor-interval-inp"
                  min="1" max="1440" value="${cfg.interval_min || 60}"
                  style="width:55px;background:#313244;color:#cdd6f4;border:1px solid #555;border-radius:4px;padding:2px 6px;font-size:12px;">
                percben
              </label>
              <span style="font-size:11px;padding:2px 8px;border-radius:10px;background:#1e1e2e;white-space:nowrap;">
                <span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${statusColor};margin-right:4px;"></span>${statusText}
              </span>
              ${rt.suspended ? `<button data-ieee="${ieee}" class="sensor-resume-btn"
                style="font-size:11px;padding:2px 8px;background:#313244;color:#fab387;
                       border:1px solid #fab387;border-radius:4px;cursor:pointer;">Folytatás</button>` : ''}
            </div>`;
        }).join('');

        // Events
        container.querySelectorAll('.sensor-enable-chk').forEach(el => {
            el.addEventListener('change', function() {
                const ieee = this.dataset.ieee;
                if (!_sensorConfig[ieee]) _sensorConfig[ieee] = {};
                _sensorConfig[ieee].enabled = this.checked;
                _sensorConfig[ieee].interval_min = parseInt(
                    container.querySelector(`.sensor-interval-inp[data-ieee="${ieee}"]`).value) || 60;
                saveSensorConfig();
            });
        });
        container.querySelectorAll('.sensor-interval-inp').forEach(el => {
            el.addEventListener('change', function() {
                const ieee = this.dataset.ieee;
                if (!_sensorConfig[ieee]) _sensorConfig[ieee] = {};
                _sensorConfig[ieee].interval_min = Math.max(1, parseInt(this.value) || 60);
                saveSensorConfig();
            });
        });
        container.querySelectorAll('.sensor-resume-btn').forEach(el => {
            el.addEventListener('click', async function() {
                const ieee = this.dataset.ieee;
                if (!_sensorConfig[ieee]) _sensorConfig[ieee] = {};
                _sensorConfig[ieee].enabled = true;
                await saveSensorConfig();
                loadSensorConfig();
            });
        });
    }

    function injectSensorSection() {
        const main = document.querySelector('main') || document.body;
        if (document.getElementById('proxy-sensor-section')) return;
        const section = document.createElement('div');
        section.id = 'proxy-sensor-section';
        section.style.cssText = 'margin-top:20px;';
        section.innerHTML = `
            <div style="background:#1e1e2e;border:1px solid #313244;border-radius:10px;padding:16px;">
              <div style="font-size:11px;text-transform:uppercase;letter-spacing:.08em;
                          color:#6c7086;margin-bottom:12px;">Szenzor adatmentés</div>
              <div id="proxy-sensor-config">
                <p style="color:#6c7086;font-size:12px;">Csatlakozz BLE-n az eszközök betöltéséhez.</p>
              </div>
            </div>`;
        main.appendChild(section);
    }

    // ── 6. Boot ───────────────────────────────────────────────────────────────

    // Hook into loadDevices to capture device list
    const _origLoadDevices = window.loadDevices;
    window.loadDevices = async function () {
        await _origLoadDevices();
        // Capture device list from the last /api/devices response
        try {
            const r = await fetch('/api/devices');
            if (r.ok) {
                const d = await r.json();
                window._proxyDeviceList = d.devices || [];
                loadSensorConfig();
            }
        } catch (_) {}
    };

    document.addEventListener('DOMContentLoaded', function () {
        injectBleButtons();
        injectSensorSection();
        updateProxyStatus();
        setInterval(updateProxyStatus, 5000);
        setInterval(loadSensorConfig, 30000);
    });

})();

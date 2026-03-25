// ESP32-C6 Zigbee Gateway - Frontend JavaScript

// ============================================================================
// Global State
// ============================================================================

let devices = [];
let clockInterval = null;
let currentEditDevice = null;
let modalDirty = false;
let espTimeOffset = 0;
let espTimeInitialized = false;
var zigbeeActive = false;   // var = window property (proxy-patch.js sets this)

// BLE State
let bleGateway = null;
var bleConnected = false;   // var = window property (proxy-patch.js sets this)

// Polling intervals
let statusInterval = null;
let devicesInterval = null;
let logsInterval = null;
let timersInterval = null;

// Rules state
let rulesData = null;

// ============================================================================
// Sensor Type Registry (mirrors sensor_types.c on firmware)
// ============================================================================

const SENSOR_TYPES = {
    'temperature_sensor':  { unit: '°C',  displayName: 'Homerseklet' },
    'humidity_sensor':     { unit: '%',   displayName: 'Paratartalom' },
    'water_level_sensor':  { unit: '',    displayName: 'Vizszint' },
    'leak_sensor':         { unit: '',    displayName: 'Szivargaserzekelo' },
};

function isSensorDevice(type) {
    return type in SENSOR_TYPES;
}

function getSensorUnit(type) {
    return SENSOR_TYPES[type]?.unit || '?';
}

function getWaterLevelText(value) {
    if (value === 0) return 'Ures';
    if (value === 1) return 'Alacsony';
    if (value === 2) return 'Tele';
    return '--';
}

function getLeakText(value) {
    if (value === 0) return 'Szaraz';
    if (value === 1) return 'Vizszivargast erzekelt!';
    return '--';
}

// ============================================================================
// Utility Functions
// ============================================================================

function escapeHtml(text) {
    if (!text) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function showToast(message, isError = false) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = 'toast' + (isError ? ' error' : '');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => toast.classList.add('hidden'), 3000);
}

function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    return (bytes / 1024).toFixed(1) + ' KB';
}

// ============================================================================
// Initialization
// ============================================================================

document.addEventListener('DOMContentLoaded', function () {
    updateControlButtons();

    loadStatus();
    loadDevices();
    loadGlobalConfig();
    loadRules();
    startClock();

    statusInterval = setInterval(() => {
        if (!bleConnected) loadStatus();
    }, 5000);

    devicesInterval = setInterval(() => {
        if (!bleConnected) loadDevices();
    }, 10000);

    // Live log polling — apiRequest routes to BLE or WiFi automatically
    pollLiveLogs();
    logsInterval = setInterval(pollLiveLogs, 3000);

    // Timer state polling
    timersInterval = setInterval(pollTimers, 5000);
});

// ============================================================================
// Clock Functions
// ============================================================================

function startClock() {
    updateClock();
    clockInterval = setInterval(updateClock, 1000);
}

function updateClock() {
    const now = new Date();

    document.getElementById('current-time').textContent =
        now.toLocaleTimeString('hu-HU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    document.getElementById('current-date').textContent =
        now.toLocaleDateString('hu-HU', { year: 'numeric', month: '2-digit', day: '2-digit' });

    if (espTimeInitialized) {
        const espTime = new Date(now.getTime() + espTimeOffset);
        document.getElementById('esp-time').textContent =
            espTime.toLocaleTimeString('hu-HU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        document.getElementById('esp-date').textContent =
            espTime.toLocaleDateString('hu-HU', { year: 'numeric', month: '2-digit', day: '2-digit' });
    }
}

// ============================================================================
// BLE Connection Management
// ============================================================================

function updateBLEStatus(connected, statusMessage) {
    const ids = [
        { ind: 'ble-indicator',         txt: 'ble-text',         con: 'ble-connect-btn',         dis: 'ble-disconnect-btn' },
        { ind: 'sticky-ble-indicator',  txt: 'sticky-ble-text',  con: 'sticky-ble-connect-btn',  dis: 'sticky-ble-disconnect-btn' },
    ];

    ids.forEach(({ ind, txt, con, dis }) => {
        const indicator = document.getElementById(ind);
        const textEl    = document.getElementById(txt);
        const conBtn    = document.getElementById(con);
        const disBtn    = document.getElementById(dis);

        if (indicator) indicator.className = connected ? 'status-dot connected' : 'status-dot disconnected';
        if (textEl)    textEl.textContent  = (ind === 'sticky-ble-indicator' ? 'BLE: ' : '') + statusMessage;
        if (conBtn)    conBtn.classList.toggle('hidden', connected);
        if (disBtn)    disBtn.classList.toggle('hidden', !connected);
    });

    updateControlButtons();
}

function updateControlButtons() {
    const pairingBtn = document.getElementById('pairing-btn');
    if (pairingBtn) {
        const canPair = bleConnected && zigbeeActive;
        pairingBtn.disabled = !canPair;
        pairingBtn.title = canPair ? '' : 'Csak Zigbee uzemmodban elerheto (BLE kapcsolat szukseges)';
    }
}

async function connectBLE() {
    const btnIds = ['ble-connect-btn', 'sticky-ble-connect-btn'];
    btnIds.forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.disabled = true;
    });

    updateBLEStatus(false, 'Csatlakozas...');

    try {
        if (!navigator.bluetooth) {
            showToast('Web Bluetooth nem tamogatott ebben a bongeszoben. Hasznaljon Chrome vagy Edge bongeszo!', true);
            updateBLEStatus(false, 'Nincs csatlakozva');
            btnIds.forEach(id => { const b = document.getElementById(id); if (b) b.disabled = false; });
            return;
        }

        if (!bleGateway) {
            bleGateway = new BLEGateway();
        }

        await bleGateway.connect();
        bleConnected = true;

        // Stop WiFi polling — BLE takes over
        clearInterval(statusInterval);  statusInterval = null;
        clearInterval(devicesInterval); devicesInterval = null;

        updateBLEStatus(true, 'Csatlakozva');
        showToast('Bluetooth kapcsolat letrejott');

        // Load initial data sequentially with delay to avoid GATT conflicts
        await new Promise(r => setTimeout(r, 500));
        await loadStatus();
        await new Promise(r => setTimeout(r, 300));
        await loadDevices();
        await new Promise(r => setTimeout(r, 300));
        await loadGlobalConfig();
        await new Promise(r => setTimeout(r, 300));
        await loadRules();

    } catch (error) {
        console.error('BLE connection error:', error);
        showToast('Bluetooth kapcsolat sikertelen: ' + error.message, true);
        updateBLEStatus(false, 'Kapcsolat sikertelen');
        bleConnected = false;
        btnIds.forEach(id => { const b = document.getElementById(id); if (b) b.disabled = false; });
    }
}

function disconnectBLE() {
    if (bleGateway) {
        bleGateway.disconnect();
    }

    bleConnected = false;
    updateBLEStatus(false, 'Nincs csatlakozva');

    const btnIds = ['ble-connect-btn', 'sticky-ble-connect-btn'];
    btnIds.forEach(id => { const b = document.getElementById(id); if (b) b.disabled = false; });

    // Re-render devices (disable control buttons)
    renderDevices();

    // Restart WiFi polling
    clearInterval(statusInterval);
    statusInterval = setInterval(() => { if (!bleConnected) loadStatus(); }, 5000);

    clearInterval(devicesInterval);
    devicesInterval = setInterval(() => { if (!bleConnected) loadDevices(); }, 10000);

    showToast('Bluetooth kapcsolat bontva');
}

window.addEventListener('ble-disconnected', () => {
    showToast('Bluetooth kapcsolat megszakadt', true);
    disconnectBLE();
});

// ============================================================================
// API Helper Functions
// ============================================================================

async function apiRequest(endpoint, method = 'GET', body = null) {
    if (bleConnected) {
        return await bleRequest(endpoint, body);
    } else {
        return await httpRequest(endpoint, method, body);
    }
}

async function httpRequest(endpoint, method = 'GET', body = null) {
    const options = {
        method,
        headers: { 'Content-Type': 'application/json' }
    };
    if (body) options.body = JSON.stringify(body);
    const response = await fetch(endpoint, options);
    return await response.json();
}

async function bleRequest(endpoint, body = null) {
    const command = endpointToCommand(endpoint, body);
    if (!command) {
        throw new Error('Unknown BLE endpoint: ' + endpoint);
    }
    const response = await bleGateway.sendCommand(command.cmd, command.params);
    if (response.status === 'ok') {
        return { success: true, ...response };
    } else if (response.status === 'error') {
        return { success: false, message: response.message };
    }
    return response;
}

function endpointToCommand(endpoint, body) {
    if (endpoint === '/api/status') {
        return { cmd: 'get_status', params: {} };
    }
    if (endpoint === '/api/devices') {
        return { cmd: 'get_devices', params: {} };
    }
    if (endpoint === '/api/rtc/set') {
        const dt = body.datetime.split(/[- :]/);
        return {
            cmd: 'set_rtc',
            params: {
                year:   parseInt(dt[0]),
                month:  parseInt(dt[1]),
                day:    parseInt(dt[2]),
                hour:   parseInt(dt[3]),
                minute: parseInt(dt[4]),
                second: parseInt(dt[5])
            }
        };
    }
    if (endpoint.startsWith('/api/devices/') && endpoint.endsWith('/config')) {
        return {
            cmd: 'set_device_config',
            params: { ieee_addr: extractIeeeFromEndpoint(endpoint), ...body }
        };
    }
    if (endpoint.startsWith('/api/devices/') && body && body.cmd) {
        return { cmd: 'control_device', params: body };
    }
    if (endpoint.startsWith('/api/devices/') && !endpoint.includes('config')) {
        return {
            cmd: 'delete_device',
            params: { ieee_addr: extractIeeeFromEndpoint(endpoint) }
        };
    }
    if (endpoint === '/api/zigbee/permit-join') {
        return { cmd: 'permit_join', params: body || { duration: 60 } };
    }
    if (endpoint === '/api/config') {
        return body
            ? { cmd: 'set_global_settings', params: body }
            : { cmd: 'get_global_settings', params: {} };
    }
    if (endpoint === '/api/reboot') {
        return { cmd: 'reboot', params: {} };
    }
    if (endpoint === '/api/wifi/shutdown') {
        return { cmd: 'switch_mode', params: {} };
    }
    if (endpoint === '/api/factory-reset') {
        return { cmd: 'factory_reset', params: {} };
    }
    if (endpoint === '/api/rules') {
        return body
            ? { cmd: 'set_rules', params: body }
            : { cmd: 'get_rules', params: {} };
    }
    if (endpoint === '/api/rules/timers') {
        return { cmd: 'get_rules_timers', params: {} };
    }
    if (endpoint === '/api/rules/var') {
        return { cmd: 'set_rules_var', params: body };
    }
    if (endpoint === '/api/rules/varconfig') {
        return { cmd: 'set_rules_varconfig', params: body };
    }
    if (endpoint === '/api/rules/reset') {
        return { cmd: 'reset_rules', params: {} };
    }
    if (endpoint === '/api/logs/live') {
        return { cmd: 'get_logs_live', params: { lines: 50 } };
    }
    return null;
}

function extractIeeeFromEndpoint(endpoint) {
    const match = endpoint.match(/\/api\/devices\/(0x[0-9A-Fa-f]+)/);
    return match ? match[1] : null;
}

// ============================================================================
// Status & Clock API
// ============================================================================

async function loadStatus() {
    try {
        const data = await apiRequest('/api/status');

        document.getElementById('status-indicator').className = 'status-dot connected';
        document.getElementById('status-text').textContent = bleConnected
            ? 'Csatlakozva (BLE)'
            : 'Csatlakozva (WiFi)';

        if (data.current_time) {
            const espDate = new Date(data.current_time.replace(' ', 'T'));
            espTimeOffset = espDate.getTime() - Date.now();
            espTimeInitialized = true;
        }

        zigbeeActive = !!data.zigbee_active;
        updateControlButtons();

        const rtcStatus = document.getElementById('rtc-status');
        if (data.rtc_initialized) {
            rtcStatus.className = 'rtc-status ok';
            rtcStatus.textContent = 'RTC beallitva';
        } else {
            rtcStatus.className = 'rtc-status warning';
            rtcStatus.textContent = 'RTC nincs beallitva - kerem allitsa be az idot!';
        }
    } catch (error) {
        console.error('Status load error:', error);
        document.getElementById('status-indicator').className = 'status-dot error';
        document.getElementById('status-text').textContent = 'Kapcsolat hiba';
        if (bleConnected) disconnectBLE();
    }
}

async function setRtc() {
    const input = document.getElementById('datetime-input');
    if (!input.value) {
        showToast('Kerem valasszon idopontot!', true);
        return;
    }
    const datetime = input.value.replace('T', ' ') + ':00';
    try {
        const data = await apiRequest('/api/rtc/set', 'POST', { datetime });
        if (data.success || data.status === 'ok') {
            showToast('Ora sikeresen beallitva!');
            espTimeOffset = 0;
            espTimeInitialized = false;
            loadStatus();
        } else {
            showToast(data.message || 'Hiba tortent', true);
        }
    } catch (error) {
        showToast('Kapcsolati hiba', true);
    }
}

async function syncRtc() {
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    const datetime = `${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())} ` +
                     `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
    try {
        const data = await apiRequest('/api/rtc/set', 'POST', { datetime });
        if (data.success || data.status === 'ok') {
            showToast('Ora szinkronizalva!');
            espTimeOffset = 0;
            espTimeInitialized = true;
            loadStatus();
        } else {
            showToast(data.message || 'Hiba tortent', true);
        }
    } catch (error) {
        showToast('Kapcsolati hiba', true);
    }
}

// ============================================================================
// Device Management
// ============================================================================

async function loadDevices() {
    try {
        const data = await apiRequest('/api/devices');
        devices = data.devices || [];
        renderDevices();

        // Refresh open modal if it's not dirty
        if (currentEditDevice && !modalDirty &&
            !document.getElementById('device-modal').classList.contains('hidden')) {
            const updated = devices.find(d =>
                d.ieee_addr === currentEditDevice.ieee_addr &&
                d.endpoint === currentEditDevice.endpoint &&
                d.device_type === currentEditDevice.device_type
            );
            if (updated) editDevice(updated.ieee_addr, updated.endpoint, updated.device_type);
        }
    } catch (error) {
        console.error('Device load error:', error);
    }
}

async function refreshDevices() {
    const btn = document.getElementById('refresh-devices-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Frissites...'; }
    try {
        await loadDevices();
        showToast('Eszkoklista frissitve');
    } catch (error) {
        showToast('Frissites sikertelen', true);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Frissites'; }
    }
}

async function deleteDevice(ieeeAddr) {
    if (!confirm('Biztosan torolni szeretne ezt az eszkozt?')) return;
    try {
        const data = await apiRequest('/api/devices/' + ieeeAddr, 'DELETE');
        if (data.success || data.status === 'ok') {
            showToast('Eszkoz torolve');
            devices = devices.filter(d => d.ieee_addr !== ieeeAddr);
            renderDevices();
            setTimeout(loadDevices, 500);
        } else {
            showToast(data.message || 'Hiba tortent', true);
        }
    } catch (error) {
        showToast('Kapcsolati hiba', true);
    }
}

async function sendDeviceCmd(ieeeAddr, deviceType, cmd) {
    try {
        await apiRequest('/api/devices/' + ieeeAddr + '/config', 'POST', { cmd, device_type: deviceType });
        showToast(cmd.toUpperCase() + ' parancs elkuldve');
    } catch (error) {
        showToast('Kapcsolati hiba', true);
    }
}

// ============================================================================
// Device Rendering
// ============================================================================

function renderDevices() {
    const container = document.getElementById('device-list');
    if (devices.length === 0) {
        container.innerHTML = '<p class="no-devices">Nincs csatlakozott eszkoz</p>';
        return;
    }
    container.innerHTML = devices.map(device =>
        isSensorDevice(device.device_type)
            ? renderSensorCard(device)
            : renderOnOffCard(device)
    ).join('');
}

function getOnOffDevices() {
    return devices.filter(d => d.device_type === 'on_off_light');
}

function getSensorControlInfo(ieeeAddr) {
    return devices.filter(d =>
        isSensorDevice(d.device_type) &&
        d.sensor && (
            d.sensor.lower_linked_device === ieeeAddr ||
            d.sensor.upper_linked_device === ieeeAddr ||
            d.sensor.error_linked_device === ieeeAddr
        )
    );
}

function renderOnOffCard(device) {
    const sensorControllers = getSensorControlInfo(device.ieee_addr);
    const isSensorControlled = sensorControllers.length > 0;
    const canControl = bleConnected && zigbeeActive;
    const ctrlDisabled = canControl ? '' : 'disabled title="Csak BLE+Zigbee uzemmodban elerheto"';

    // Build status badge
    let statusBadge = '';
    if (isSensorControlled) {
        const badges = sensorControllers.map(s => {
            const roles = [];
            if (s.sensor.lower_linked_device === device.ieee_addr) roles.push('also korlat');
            if (s.sensor.upper_linked_device === device.ieee_addr) roles.push('felso korlat');
            if (s.sensor.error_linked_device === device.ieee_addr) roles.push('hiba');
            return `<span class="sensor-control-badge">${escapeHtml(s.custom_name)} (${roles.join(', ')})</span>`;
        }).join('');
        statusBadge = `
            <div class="device-status">
                <span class="state-badge state-sensor">Szenzor vezerelt</span>
                ${device.error ? `<span class="error-badge">&#9888; ${escapeHtml(device.error.message)}</span>` : ''}
            </div>
            <div class="sensor-control-info">Vezerli: ${badges}</div>`;
    } else {
        statusBadge = `
            <div class="device-status">
                <span class="state-badge ${device.enabled ? 'state-on' : 'state-off'}">
                    ${device.enabled ? 'Automatizacio BE' : 'Automatizacio KI'}
                </span>
                ${device.error ? `<span class="error-badge">&#9888; ${escapeHtml(device.error.message)}</span>` : ''}
            </div>`;
    }

    return `
        <div class="device-item${isSensorControlled ? ' sensor-controlled' : ''}">
            <div class="device-header">
                <span class="device-name">${escapeHtml(device.custom_name)}</span>
                <div class="device-edit-actions">
                    <button onclick="editDevice('${device.ieee_addr}', ${device.endpoint}, '${device.device_type}')" class="btn btn-primary btn-small">Szerkesztes</button>
                    <button onclick="deleteDevice('${device.ieee_addr}')" class="btn btn-danger btn-small">Torles</button>
                </div>
            </div>
            <div class="device-ctrl-buttons">
                <button onclick="sendDeviceCmd('${device.ieee_addr}', '${device.device_type}', 'on')" class="btn btn-success btn-small" ${ctrlDisabled}>BE</button>
                <button onclick="sendDeviceCmd('${device.ieee_addr}', '${device.device_type}', 'off')" class="btn btn-secondary btn-small" ${ctrlDisabled}>KI</button>
                <button onclick="sendDeviceCmd('${device.ieee_addr}', '${device.device_type}', 'toggle')" class="btn btn-primary btn-small" ${ctrlDisabled}>Valtas</button>
            </div>
            ${statusBadge}
            <details class="device-details">
                <summary>Reszletek</summary>
                <div class="device-addr">IEEE: ${device.ieee_addr} &nbsp;|&nbsp; EP: ${device.endpoint}</div>
            </details>
        </div>`;
}

function renderSensorCard(device) {
    const sensor = device.sensor || {};
    const unit = getSensorUnit(device.device_type);
    const isWaterLevel = device.device_type === 'water_level_sensor';
    const isLeak = device.device_type === 'leak_sensor';
    const valueStr = (sensor.valid && typeof sensor.current_value === 'number')
        ? (isWaterLevel ? getWaterLevelText(sensor.current_value)
           : isLeak ? getLeakText(sensor.current_value)
           : sensor.current_value.toFixed(1))
        : '--';

    const batteryHtml = renderBatteryInfo(sensor);
    const errorHtml = (!sensor.valid && sensor.last_update > 0)
        ? '<span class="error-badge">&#9888; Sensor timeout</span>' : '';

    return `
        <div class="device-item sensor-device">
            <div class="device-header">
                <span class="device-name">${escapeHtml(device.custom_name)}</span>
                <div class="device-edit-actions">
                    <button onclick="editDevice('${device.ieee_addr}', ${device.endpoint}, '${device.device_type}')" class="btn btn-primary btn-small">Szerkesztes</button>
                    <button onclick="deleteDevice('${device.ieee_addr}')" class="btn btn-danger btn-small">Torles</button>
                </div>
            </div>
            <div class="sensor-value-display">
                <span class="sensor-value-number">${valueStr}</span>
                <span class="sensor-value-unit">${unit}</span>
            </div>
            ${batteryHtml}
            ${errorHtml ? `<div class="device-status">${errorHtml}</div>` : ''}
            <details class="device-details">
                <summary>Reszletek</summary>
                <div class="device-addr">IEEE: ${device.ieee_addr} &nbsp;|&nbsp; EP: ${device.endpoint}</div>
            </details>
        </div>`;
}

function renderBatteryInfo(sensor) {
    if (sensor.battery_percent === 255 && sensor.battery_voltage_100mv === 255) return '';

    const hasPercent = sensor.battery_percent !== 255;
    const hasVoltage = sensor.battery_voltage_100mv !== 255;
    const percentStr = hasPercent ? sensor.battery_percent + '%' : '?';
    const voltageStr = hasVoltage ? (sensor.battery_voltage_100mv / 10).toFixed(1) + 'V' : '';

    let barClass = 'battery-high';
    if (hasPercent) {
        if (sensor.battery_percent < 20) barClass = 'battery-low';
        else if (sensor.battery_percent < 50) barClass = 'battery-mid';
    }
    const barWidth = hasPercent ? Math.min(sensor.battery_percent, 100) : 50;

    return `
        <div class="sensor-battery">
            <span class="battery-label">Akku:</span>
            <div class="battery-bar-bg">
                <div class="battery-bar ${barClass}" style="width: ${barWidth}%"></div>
            </div>
            <span class="battery-text">${percentStr}${voltageStr ? ' ' + voltageStr : ''}</span>
        </div>`;
}

// ============================================================================
// Modal Functions
// ============================================================================

function editDevice(ieeeAddr, endpoint, deviceType) {
    const device = devices.find(d =>
        d.ieee_addr === ieeeAddr && d.endpoint === endpoint && d.device_type === deviceType
    );
    if (!device) return;

    currentEditDevice = device;
    modalDirty = false;

    document.getElementById('edit-ieee-addr').value = ieeeAddr;
    document.getElementById('edit-name').value = device.custom_name || '';
    document.getElementById('edit-device-type').value = device.device_type || 'on_off_light';

    const isSensor = isSensorDevice(device.device_type);
    document.getElementById('onoff-settings').classList.toggle('hidden', isSensor);
    document.getElementById('sensor-settings').classList.toggle('hidden', !isSensor);

    if (isSensor) {
        const sensor = device.sensor || {};
        const unit = getSensorUnit(device.device_type);

        document.getElementById('edit-current-value').textContent =
            (sensor.valid && typeof sensor.current_value === 'number')
                ? sensor.current_value.toFixed(1) : '--';
        document.getElementById('edit-value-unit').textContent = unit;

        document.getElementById('edit-lower-threshold').value   = sensor.lower_threshold ?? 18.0;
        document.getElementById('edit-lower-hysteresis').value  = sensor.lower_hysteresis ?? 0.5;
        document.getElementById('edit-upper-threshold').value   = sensor.upper_threshold ?? 25.0;
        document.getElementById('edit-upper-hysteresis').value  = sensor.upper_hysteresis ?? 0.5;
        document.getElementById('edit-timeout').value           = sensor.timeout_seconds ?? 60;
        document.getElementById('edit-report-min').value        = sensor.report_min_interval ?? 0;
        document.getElementById('edit-report-max').value        = sensor.report_max_interval ?? 10;
        document.getElementById('edit-report-change').value     = sensor.report_change ?? 50;
        document.getElementById('edit-lower-delay').value       = sensor.lower_delay_seconds ?? 0;
        document.getElementById('edit-upper-delay').value       = sensor.upper_delay_seconds ?? 0;

        const errorActionVal = sensor.error_action_on ? 'on' : 'off';
        document.querySelectorAll('input[name="error-action"]').forEach(r => {
            r.checked = (r.value === errorActionVal);
        });

        renderLinkedDeviceSelect('edit-lower-linked', sensor.lower_linked_device || '');
        renderLinkedDeviceSelect('edit-upper-linked', sensor.upper_linked_device || '');
        renderLinkedDeviceSelect('edit-error-linked', sensor.error_linked_device || '');

        updateErrorActionVisibility();
        updateReportChangeDisplay();
        updateThresholdLogicDisplay();

        const isLeakSensor = device.device_type === 'leak_sensor';
        ['lower-threshold-form', 'lower-hysteresis-form', 'lower-delay-form', 'lower-threshold-logic',
         'upper-threshold-form', 'upper-hysteresis-form', 'upper-delay-form', 'upper-threshold-logic',
         'report-settings-group'].forEach(id => {
            document.getElementById(id).classList.toggle('hidden', isLeakSensor);
        });
        document.getElementById('lower-threshold-title').textContent =
            isLeakSensor ? 'Szaraz allapot (trigger)' : 'Also korlat (futes)';
        document.getElementById('upper-threshold-title').textContent =
            isLeakSensor ? 'Szivargás allapot (trigger)' : 'Felso korlat (hutes)';
    } else {
        document.getElementById('edit-enabled').checked = device.enabled;
        document.getElementById('edit-mode').value = device.mode;

        document.getElementById('edit-delay-off1').value      = device.delay_off1_minutes ?? 0;
        document.getElementById('edit-delay-duration').value  = device.delay_duration_minutes ?? 120;
        document.getElementById('edit-delay-off2').value      = device.delay_off2_minutes ?? 30;

        renderTimePairs(device.time_pairs || [{ on: '06:00', off: '18:00' }]);
        onModeChange();

        // Show sensor control warning if applicable
        const sensorControllers = getSensorControlInfo(ieeeAddr);
        let warningDiv = document.getElementById('sensor-control-warning');
        if (sensorControllers.length > 0) {
            const names = sensorControllers.map(s => escapeHtml(s.custom_name)).join(', ');
            if (!warningDiv) {
                warningDiv = document.createElement('div');
                warningDiv.id = 'sensor-control-warning';
                warningDiv.className = 'sensor-control-warning';
                document.getElementById('onoff-settings').prepend(warningDiv);
            }
            warningDiv.innerHTML =
                `&#9888; Ez az eszkoz szenzor altal vezerelt (${names}). ` +
                `Az idozites beallitasok nem ervenyesulnek, amig szenzor vezerles aktiv.`;
            warningDiv.classList.remove('hidden');
        } else if (warningDiv) {
            warningDiv.classList.add('hidden');
        }
    }

    document.getElementById('device-modal').classList.remove('hidden');
}

function closeModal() {
    document.getElementById('device-modal').classList.add('hidden');
    currentEditDevice = null;
    modalDirty = false;
}

function onModeChange() {
    const mode = document.getElementById('edit-mode').value;
    document.getElementById('fixed-time-settings').classList.toggle('hidden', mode !== 'fixed_time');
    document.getElementById('delay-settings').classList.toggle('hidden', mode === 'fixed_time');
}

function renderLinkedDeviceSelect(selectId, currentValue) {
    const select = document.getElementById(selectId);
    if (!select) return;
    select.innerHTML = '<option value="">-- Nincs --</option>' +
        getOnOffDevices().map(d =>
            `<option value="${d.ieee_addr}" ${d.ieee_addr === currentValue ? 'selected' : ''}>
                ${escapeHtml(d.custom_name)} (${d.ieee_addr})
            </option>`
        ).join('');
}

function updateErrorActionVisibility() {
    const val = document.getElementById('edit-error-linked').value;
    document.getElementById('error-action-group').classList.toggle('hidden', !val);
}

function updateThresholdLogicDisplay() {
    const lowerT  = parseFloat(document.getElementById('edit-lower-threshold').value) || 0;
    const lowerH  = parseFloat(document.getElementById('edit-lower-hysteresis').value) || 0;
    const upperT  = parseFloat(document.getElementById('edit-upper-threshold').value) || 0;
    const upperH  = parseFloat(document.getElementById('edit-upper-hysteresis').value) || 0;
    const lowerD  = parseInt(document.getElementById('edit-lower-delay').value) || 0;
    const upperD  = parseInt(document.getElementById('edit-upper-delay').value) || 0;
    const unit    = getSensorUnit(document.getElementById('edit-device-type').value);

    const lowerDiv = document.getElementById('lower-threshold-logic');
    if (lowerDiv) {
        const delayStr = lowerD > 0
            ? ` → ${lowerD}mp varakozas → BE (ha meg &lt; ${lowerT.toFixed(1)}${unit})` : ' → BE';
        lowerDiv.innerHTML =
            `<span class="logic-on">ertek &lt; ${lowerT.toFixed(1)}${unit}${delayStr}</span>` +
            `<span class="logic-off">ertek &gt; ${(lowerT + lowerH).toFixed(1)}${unit} → KI</span>`;
    }

    const upperDiv = document.getElementById('upper-threshold-logic');
    if (upperDiv) {
        const delayStr = upperD > 0
            ? ` → ${upperD}mp varakozas → BE (ha meg &gt; ${upperT.toFixed(1)}${unit})` : ' → BE';
        upperDiv.innerHTML =
            `<span class="logic-on">ertek &gt; ${upperT.toFixed(1)}${unit}${delayStr}</span>` +
            `<span class="logic-off">ertek &lt; ${(upperT - upperH).toFixed(1)}${unit} → KI</span>`;
    }
}

function updateReportChangeDisplay() {
    const rawValue = parseInt(document.getElementById('edit-report-change').value) || 50;
    const unit = getSensorUnit(document.getElementById('edit-device-type').value);
    document.getElementById('report-change-display').textContent = (rawValue / 100).toFixed(2);
    document.getElementById('report-change-unit').textContent = unit;
}

async function saveDeviceConfig() {
    const ieeeAddr   = document.getElementById('edit-ieee-addr').value;
    const deviceType = document.getElementById('edit-device-type').value;
    const isSensor   = isSensorDevice(deviceType);

    const config = {
        custom_name: document.getElementById('edit-name').value,
        device_type: deviceType
    };

    if (isSensor) {
        const errorLinkedVal  = document.getElementById('edit-error-linked').value;
        const errorActionRadio = document.querySelector('input[name="error-action"]:checked');
        config.sensor = {
            lower_threshold:      parseFloat(document.getElementById('edit-lower-threshold').value),
            lower_hysteresis:     parseFloat(document.getElementById('edit-lower-hysteresis').value),
            upper_threshold:      parseFloat(document.getElementById('edit-upper-threshold').value),
            upper_hysteresis:     parseFloat(document.getElementById('edit-upper-hysteresis').value),
            lower_linked_device:  document.getElementById('edit-lower-linked').value || null,
            upper_linked_device:  document.getElementById('edit-upper-linked').value || null,
            lower_delay_seconds:  parseInt(document.getElementById('edit-lower-delay').value) || 0,
            upper_delay_seconds:  parseInt(document.getElementById('edit-upper-delay').value) || 0,
            timeout_seconds:      parseInt(document.getElementById('edit-timeout').value),
            error_linked_device:  errorLinkedVal || null,
            error_action_on:      errorActionRadio ? errorActionRadio.value === 'on' : false,
            report_min_interval:  parseInt(document.getElementById('edit-report-min').value),
            report_max_interval:  parseInt(document.getElementById('edit-report-max').value),
            report_change:        parseInt(document.getElementById('edit-report-change').value)
        };
    } else {
        const mode = document.getElementById('edit-mode').value;
        config.enabled = document.getElementById('edit-enabled').checked;
        config.mode    = mode;
        if (mode === 'fixed_time') {
            config.time_pairs = getTimePairs();
        } else {
            config.delay_off1_minutes     = parseInt(document.getElementById('edit-delay-off1').value);
            config.delay_duration_minutes = parseInt(document.getElementById('edit-delay-duration').value);
            config.delay_off2_minutes     = parseInt(document.getElementById('edit-delay-off2').value);
        }
    }

    try {
        const data = await apiRequest('/api/devices/' + ieeeAddr + '/config', 'POST', config);
        if (data.success || data.status === 'ok') {
            showToast('Beallitasok mentve');
            closeModal();
            await new Promise(r => setTimeout(r, 500));
            await loadDevices();
        } else {
            showToast(data.message || 'Hiba tortent', true);
        }
    } catch (error) {
        showToast('Kapcsolati hiba', true);
    }
}

// ============================================================================
// Time Pair Functions
// ============================================================================

function renderTimePairs(pairs) {
    const container = document.getElementById('time-pairs-container');
    container.innerHTML = pairs.map((pair, index) => `
        <div class="time-pair" data-index="${index}">
            <label>ON:</label>
            <input type="time" class="time-on" value="${pair.on}">
            <label>OFF:</label>
            <input type="time" class="time-off" value="${pair.off}">
            ${index > 0 ? `<button type="button" class="remove-btn" onclick="removeTimePair(${index})">&times;</button>` : ''}
        </div>
    `).join('');
}

function addTimePair() {
    const container = document.getElementById('time-pairs-container');
    if (container.children.length >= 5) {
        showToast('Maximum 5 idopont par adhato meg', true);
        return;
    }
    const count = container.children.length;
    const div = document.createElement('div');
    div.className = 'time-pair';
    div.dataset.index = count;
    div.innerHTML = `
        <label>ON:</label>
        <input type="time" class="time-on" value="08:00">
        <label>OFF:</label>
        <input type="time" class="time-off" value="20:00">
        <button type="button" class="remove-btn" onclick="removeTimePair(${count})">&times;</button>
    `;
    container.appendChild(div);
}

function removeTimePair(index) {
    const container = document.getElementById('time-pairs-container');
    const pairs = Array.from(container.children);
    if (pairs.length <= 1) return;
    pairs[index].remove();
    Array.from(container.children).forEach((pair, i) => {
        pair.dataset.index = i;
        const btn = pair.querySelector('.remove-btn');
        if (btn) btn.onclick = () => removeTimePair(i);
    });
}

function getTimePairs() {
    const pairs = [];
    document.querySelectorAll('#time-pairs-container .time-pair').forEach(pair => {
        const on  = pair.querySelector('.time-on').value;
        const off = pair.querySelector('.time-off').value;
        if (on && off) pairs.push({ on, off });
    });
    return pairs;
}

// ============================================================================
// Global Config
// ============================================================================

async function loadGlobalConfig() {
    try {
        const data = await apiRequest('/api/config');

        // Only update checkboxes when the server explicitly returns the value.
        // This prevents BLE responses that omit a field from forcing wrong state.
        if (data.rules_enabled !== undefined) {
            document.getElementById('rules-enabled').checked = !!data.rules_enabled;
        }

        if (data.log_zigbee_only !== undefined) {
            document.getElementById('log-zigbee-only').checked = !!data.log_zigbee_only;
        }

        if (data.local_xkc_enabled !== undefined) {
            const xkcEnabled = !!data.local_xkc_enabled;
            document.getElementById('xkc-enabled').checked = xkcEnabled;
            document.getElementById('xkc-gpio-settings').classList.toggle('hidden', !xkcEnabled);
        }

        // Populate GPIO dropdowns (only when XKC data is present)
        if (data.valid_xkc_gpios || data.local_xkc_gpio_lower !== undefined) {
            const validGpios = data.valid_xkc_gpios || [3, 4, 5, 6, 7, 10, 11, 14, 18, 19, 20, 21, 22, 23];
            populateGpioSelect('xkc-gpio-lower', validGpios, data.local_xkc_gpio_lower || 4);
            populateGpioSelect('xkc-gpio-upper', validGpios, data.local_xkc_gpio_upper || 5);
        }
    } catch (error) {
        console.error('Config load error:', error);
    }
}

function populateGpioSelect(selectId, validPins, selectedPin) {
    const select = document.getElementById(selectId);
    select.innerHTML = validPins.map(pin =>
        `<option value="${pin}"${pin === selectedPin ? ' selected' : ''}>GPIO ${pin}</option>`
    ).join('');
}

function onXkcToggleChange() {
    const enabled = document.getElementById('xkc-enabled').checked;
    document.getElementById('xkc-gpio-settings').classList.toggle('hidden', !enabled);
    if (enabled) {
        if (!confirm('Figyelmeztetes: A helyi XKC szenzor engedelyezese GPIO hardware csatlakozast igenyel. ' +
                     'Gyozodjon meg rola, hogy az XKC szenzorok csatlakoztatva vannak a megfelelo GPIO labakra.\n\nFolytatja?')) {
            document.getElementById('xkc-enabled').checked = false;
            document.getElementById('xkc-gpio-settings').classList.add('hidden');
        }
    }
}

async function onLogFilterChange() {
    const enabled = document.getElementById('log-zigbee-only').checked;
    try {
        await apiRequest('/api/config', 'POST', { log_zigbee_only: enabled });
    } catch (error) {
        console.error('Log filter change error:', error);
    }
}

async function onRulesEnabledChange() {
    const enabled = document.getElementById('rules-enabled').checked;
    try {
        await apiRequest('/api/config', 'POST', { rules_enabled: enabled });
    } catch (error) {
        console.error('Rules enabled change error:', error);
    }
}

async function saveGlobalConfig() {
    const xkcEnabled = document.getElementById('xkc-enabled').checked;
    const gpioLower  = parseInt(document.getElementById('xkc-gpio-lower').value);
    const gpioUpper  = parseInt(document.getElementById('xkc-gpio-upper').value);
    const zigbeeOnly = document.getElementById('log-zigbee-only').checked;

    if (xkcEnabled && gpioLower === gpioUpper) {
        showToast('Az also es felso szenzor nem lehet ugyanaz a GPIO lab!', true);
        return;
    }

    try {
        const data = await apiRequest('/api/config', 'POST', {
            local_xkc_enabled:   xkcEnabled,
            local_xkc_gpio_lower: gpioLower,
            local_xkc_gpio_upper: gpioUpper,
            log_zigbee_only:      zigbeeOnly,
        });
        if (data.success || data.status === 'ok') {
            showToast('Beallitasok mentve');
            setTimeout(loadDevices, 1000);
        } else {
            showToast(data.message || 'Hiba tortent', true);
        }
    } catch (error) {
        showToast('Kapcsolati hiba', true);
    }
}

// ============================================================================
// Device Control (Reboot, Mode Switch, Pairing, Factory Reset)
// ============================================================================

async function rebootDevice() {
    if (!confirm('Biztosan ujra szeretne inditani az eszkoezt?')) return;
    try {
        await apiRequest('/api/reboot', 'POST');
        showToast('Ujrainditas folyamatban...');
    } catch (error) {
        showToast('Kapcsolati hiba', true);
    }
}

async function switchMode() {
    if (zigbeeActive) {
        showToast('Mar Zigbee uzemmodban van');
        return;
    }
    try {
        await apiRequest('/api/wifi/shutdown', 'POST');
        showToast('Uzemmod valtas...');
    } catch (error) {
        showToast('Kapcsolati hiba', true);
    }
}

async function startPairing() {
    if (!confirm('Elinditsuk a parositas modot?')) return;
    try {
        const data = await apiRequest('/api/zigbee/permit-join', 'POST', { duration: 60 });
        if (data.success || data.status === 'ok') {
            showToast('Parositas mod aktiv (60mp)');
        } else {
            showToast(data.message || 'Hiba tortent', true);
        }
    } catch (error) {
        showToast('Kapcsolati hiba', true);
    }
}

async function factoryReset() {
    if (!confirm('Biztosan torolni szeretne az osszes eszkozt es beallitast? Ez a muvelet nem vonhato vissza!')) return;
    if (!confirm('Utolso figyelmeztetes: Az osszes adat torlodik. Folytatja?')) return;
    try {
        const data = await apiRequest('/api/factory-reset', 'POST');
        if (data.success || data.status === 'ok') {
            showToast('Gyari alaphelyzetbe allitas sikeres');
            setTimeout(() => location.reload(), 3000);
        } else {
            showToast(data.message || 'Hiba tortent', true);
        }
    } catch (error) {
        showToast('Kapcsolati hiba', true);
    }
}

// ============================================================================
// Rules Engine
// ============================================================================

function toggleRulesEditor() {
    const section = document.getElementById('rules-editor-section');
    const btn     = document.getElementById('rules-toggle-btn');
    const isHidden = section.classList.contains('hidden');
    section.classList.toggle('hidden', !isHidden);
    btn.textContent = isHidden ? 'Bezaras' : 'Szerkesztes';
    if (isHidden) loadRules();
}

async function loadRules() {
    try {
        const data = await apiRequest('/api/rules');
        rulesData = data;

        const editor = document.getElementById('rules-editor');
        if (editor && data.text !== undefined) {
            editor.value = data.text;
        }

        const countEl = document.getElementById('rules-count');
        if (countEl) {
            countEl.textContent = (data.rule_count || 0) + ' szabaly betoltve';
        }

        renderRulesState(data);
    } catch (error) {
        console.error('Rules load error:', error);
    }
}

async function saveRules() {
    const editor   = document.getElementById('rules-editor');
    const statusEl = document.getElementById('rules-save-status');
    try {
        statusEl.textContent = 'Mentes...';
        statusEl.className   = '';

        const data = await apiRequest('/api/rules', 'POST', { text: editor.value });
        // WiFi returns {ok:true}, BLE returns {success:true} — accept both
        if (data.ok || data.success) {
            statusEl.textContent = (data.rule_count || 0) + ' szabaly mentve';
            statusEl.className   = 'rules-status-ok';
            document.getElementById('rules-count').textContent = (data.rule_count || 0) + ' szabaly betoltve';
            showToast('Szabalyok mentve');
            setTimeout(loadRules, 500);
        } else {
            // WiFi uses {error:"..."}, BLE uses {message:"..."}
            const errMsg = data.error || data.message || 'Parse hiba';
            statusEl.textContent = errMsg;
            statusEl.className   = 'rules-status-error';
            showToast(errMsg, true);
        }
    } catch (error) {
        statusEl.textContent = 'Kapcsolati hiba';
        statusEl.className   = 'rules-status-error';
        showToast('Kapcsolati hiba', true);
    }
}

function renderRulesState(data) {
    if (!data) return;

    // Render variables
    const varsContainer = document.getElementById('rules-vars-list');
    if (varsContainer) {
        if (Array.isArray(data.variables) && data.variables.length > 0) {
            varsContainer.innerHTML = data.variables.map((val, i) => {
                let persistIcon = '';
                const cfg = data.var_config && data.var_config[i];
                if (cfg !== undefined) {
                    persistIcon = cfg.persist
                        ? ' <span title="Persistent">&#128190;</span>'
                        : ` <span title="Non-persistent (alapertek: ${cfg.default})">&#128260;(${cfg.default})</span>`;
                }
                return `<div class="rules-card rules-var-card">
                    <span class="rules-card-name">var${i + 1}</span>
                    <span class="rules-card-value">${val}${persistIcon}</span>
                    <button onclick="editRulesVar(${i})" class="btn-tiny" title="Ertek szerkesztese">&#9998;</button>
                    <button onclick="configRulesVar(${i})" class="btn-tiny" title="Konfig">&#9881;</button>
                </div>`;
            }).join('');
        } else {
            varsContainer.innerHTML = '<span class="hint">Nincs valtozo</span>';
        }
    }

    renderTimers(data.timers);
}

function renderTimers(timers) {
    const container = document.getElementById('rules-timers-list');
    if (!container) return;
    if (!timers) {
        container.innerHTML = '<span class="hint">Nincs aktiv timer</span>';
        return;
    }
    const activeTimers = timers.filter(t => t.active);
    if (activeTimers.length > 0) {
        container.innerHTML = activeTimers.map(t =>
            `<div class="rules-card rules-timer-card">
                <span class="rules-card-name">timer ${t.id}</span>
                <span class="rules-card-value">${t.remaining} mp</span>
                <span class="rules-card-status active">Aktiv</span>
            </div>`
        ).join('');
    } else {
        container.innerHTML = '<span class="hint">Nincs aktiv timer</span>';
    }
}

async function pollTimers() {
    try {
        const data = await apiRequest('/api/rules/timers');
        if (data && data.timers) renderTimers(data.timers);
    } catch (e) { /* silent */ }
}

async function editRulesVar(index) {
    const cfg = rulesData?.var_config?.[index] ?? { persist: true, default: 0 };
    const isPersist = cfg.persist;

    const label = isPersist
        ? `var${index + 1} uj erteke:`
        : `var${index + 1} alaperteke (boot utan erre all):`;
    const current = isPersist
        ? (rulesData?.variables?.[index] ?? 0)
        : cfg.default;

    const newVal = prompt(label, current);
    if (newVal === null) return;

    const val = parseFloat(newVal);
    if (isNaN(val)) { showToast('Ervenytelen szam', true); return; }

    try {
        if (isPersist) {
            await apiRequest('/api/rules/var', 'POST', { index, value: val });
            showToast(`var${index + 1} = ${val}`);
        } else {
            await apiRequest('/api/rules/varconfig', 'POST', { index, persist: false, default_value: val });
            showToast(`var${index + 1} alapertek = ${val}`);
        }
        loadRules();
    } catch (error) {
        showToast('Hiba', true);
    }
}

async function configRulesVar(index) {
    const cfg = rulesData?.var_config?.[index] ?? { persist: true, default: 0 };
    const persistChoice = confirm(
        `var${index + 1} konfig\n\n` +
        `Jelenlegi: ${cfg.persist ? 'Persistent (NVS-ben marad)' : `Non-persistent (alapertek: ${cfg.default})`}\n\n` +
        'OK = Persistent (NVS-ben tarolodik)\n' +
        'Megse = Non-persistent (alapertekre all boot-kor)'
    );

    let defaultVal = cfg.default;
    if (!persistChoice) {
        const defInput = prompt(`Alapertek (boot-kor ezt kapja var${index + 1}):`, cfg.default);
        if (defInput === null) return;
        defaultVal = parseFloat(defInput);
        if (isNaN(defaultVal)) { showToast('Ervenytelen szam', true); return; }
    }

    try {
        await apiRequest('/api/rules/varconfig', 'POST', {
            index,
            persist: persistChoice,
            default_value: defaultVal
        });
        showToast(`var${index + 1} konfig mentve`);
        loadRules();
    } catch (error) {
        showToast('Hiba', true);
    }
}

async function resetRulesEngine() {
    if (!confirm('Torol minden szabalyt, valtozot es konfigot az NVS-bol?\n\nEz visszaallitja a gyari alapallapotot.')) return;
    try {
        await apiRequest('/api/rules/reset', 'POST', {});
        showToast('Szabalyok NVS torolve');
        loadRules();
    } catch (error) {
        showToast('Hiba', true);
    }
}

// ============================================================================
// Log Viewer
// ============================================================================

function showLogTab(tab) {
    const live       = document.getElementById('log-tab-live');
    const history    = document.getElementById('log-tab-history');
    const btnLive    = document.getElementById('log-tab-btn-live');
    const btnHist    = document.getElementById('log-tab-btn-history');

    if (tab === 'live') {
        live.classList.remove('hidden');
        history.classList.add('hidden');
        btnLive.classList.add('active');
        btnHist.classList.remove('active');
    } else {
        live.classList.add('hidden');
        history.classList.remove('hidden');
        btnLive.classList.remove('active');
        btnHist.classList.add('active');
        loadHistorySessions();
    }
}

function renderLiveLog(lines) {
    const box = document.getElementById('live-log-box');
    if (!box || !lines) return;
    box.innerHTML = lines.map(line => {
        const level = line[0] || 'I';
        const cls = ['E', 'W', 'I', 'D'].includes(level) ? 'log-line-' + level : 'log-line-I';
        return `<div class="${cls}">${escapeHtml(line)}</div>`;
    }).join('');
    if (document.getElementById('log-autoscroll').checked) {
        box.scrollTop = box.scrollHeight;
    }
}

function clearLiveDisplay() {
    const box = document.getElementById('live-log-box');
    if (box) box.innerHTML = '';
}

async function pollLiveLogs() {
    try {
        const data = await apiRequest('/api/logs/live');
        renderLiveLog(data.lines);
    } catch (e) {
        // silent — live log polling failure is non-critical
    }
}

async function loadHistorySessions() {
    // History logs live on FAT filesystem, only accessible via WiFi HTTP
    const bleMsg  = document.getElementById('log-history-ble-msg');
    const wifiDiv = document.getElementById('log-history-wifi-content');

    if (bleConnected) {
        if (bleMsg)  bleMsg.classList.remove('hidden');
        if (wifiDiv) wifiDiv.classList.add('hidden');
        return;
    }

    if (bleMsg)  bleMsg.classList.add('hidden');
    if (wifiDiv) wifiDiv.classList.remove('hidden');

    try {
        const data = await httpRequest('/api/logs/history');
        const sel = document.getElementById('history-session-select');
        sel.innerHTML = '<option value="">&#8212; Session valasztasa &#8212;</option>' +
            (data.sessions || []).map(s =>
                `<option value="${s.name}">${s.name} (${formatBytes(s.size)})${s.current ? ' \u2190 jelenlegi' : ''}</option>`
            ).join('');
    } catch (e) {
        console.error('loadHistorySessions error:', e);
    }
}

async function loadHistoryContent() {
    const sel = document.getElementById('history-session-select');
    const box = document.getElementById('history-log-box');
    if (!sel || !box || !sel.value) return;

    box.innerHTML = '<div style="color:#888">Betoltes...</div>';
    try {
        // Always use direct HTTP fetch for history content (WiFi only, large file)
        const resp = await fetch('/api/logs/history?file=' + encodeURIComponent(sel.value));
        const text = await resp.text();
        box.innerHTML = text.split('\n').map(line => {
            const level = line[0] || 'I';
            const cls = ['E', 'W', 'I', 'D'].includes(level) ? 'log-line-' + level : 'log-line-I';
            return `<div class="${cls}">${escapeHtml(line)}</div>`;
        }).join('');
        box.scrollTop = box.scrollHeight;
    } catch (e) {
        box.innerHTML = '<div class="log-line-E">Betoltes sikertelen</div>';
    }
}

async function clearLogHistory() {
    if (!confirm('Biztosan torlod az osszes eloezmenyt?')) return;
    try {
        await fetch('/api/logs', { method: 'DELETE' });
        document.getElementById('history-log-box').innerHTML = '';
        document.getElementById('history-session-select').innerHTML =
            '<option value="">&#8212; Session valasztasa &#8212;</option>';
        showToast('Naplo elozmények torolve');
    } catch (e) {
        showToast('Torles sikertelen', true);
    }
}

// ============================================================================
// Modal Event Listeners
// ============================================================================

document.getElementById('device-form').addEventListener('input',  () => { modalDirty = true; });
document.getElementById('device-form').addEventListener('change', () => { modalDirty = true; });

document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeModal();
});

document.getElementById('device-modal').addEventListener('click', function (e) {
    if (e.target === this) closeModal();
});

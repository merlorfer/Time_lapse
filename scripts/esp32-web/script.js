// ESP32-C6 Zigbee Gateway - Frontend JavaScript

// ============================================================================
// Global State
// ============================================================================

let devices = [];
// permitJoinTimer removed - pairing is done via physical button
let clockInterval = null;

// Sensor type registry (mirrors sensor_types.c on firmware)
const SENSOR_TYPES = {
    'temperature_sensor': { unit: '°C', displayName: 'Homerseklet' },
    'humidity_sensor': { unit: '%', displayName: 'Paratartalom' },
    'water_level_sensor': { unit: '', displayName: 'Vizszint' },
    'leak_sensor': { unit: '', displayName: 'Szivargaserzekelo' },
};

function isSensorDevice(type) { return type in SENSOR_TYPES; }

// Water level display helper
function getWaterLevelText(value) {
    if (value === 0) return 'Ures';
    if (value === 1) return 'Alacsony';
    if (value === 2) return 'Tele';
    return '--';
}
// Leak sensor display helper
function getLeakText(value) {
    if (value === 0) return 'Szaraz';
    if (value === 1) return 'Vizszivargast erzekelt!';
    return '--';
}
function getSensorUnit(type) { return SENSOR_TYPES[type]?.unit || '?'; }
let currentEditDevice = null;
let modalDirty = false;
let espTimeOffset = 0;  // Offset between ESP RTC and local time
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

// Helper function to check if we should poll via WiFi
function shouldPollViaWiFi() {
    const shouldPoll = !bleConnected;
    if (!shouldPoll) {
        console.log('Skipping WiFi poll: BLE mode active');
    }
    return shouldPoll;
}

// ============================================================================
// Initialization
// ============================================================================

document.addEventListener('DOMContentLoaded', function() {
    // Set initial button states
    updateControlButtons();

    // Initial load - these will use WiFi by default
    loadStatus();
    loadDevices();
    loadGlobalConfig();
    loadRules();
    startClock();

    // Refresh data periodically (only for WiFi mode)
    statusInterval = setInterval(() => {
        if (shouldPollViaWiFi()) {
            loadStatus();
        }
    }, 5000);

    devicesInterval = setInterval(() => {
        if (shouldPollViaWiFi()) {
            loadDevices();
        }
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

    // Host time
    document.getElementById('current-time').textContent =
        now.toLocaleTimeString('hu-HU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    document.getElementById('current-date').textContent =
        now.toLocaleDateString('hu-HU', { year: 'numeric', month: '2-digit', day: '2-digit' });

    // ESP time (calculated from offset)
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
    // Update main BLE status
    const indicator = document.getElementById('ble-indicator');
    const statusText = document.getElementById('ble-text');
    const connectBtn = document.getElementById('ble-connect-btn');
    const disconnectBtn = document.getElementById('ble-disconnect-btn');

    if (indicator) {
        indicator.className = connected ? 'status-dot connected' : 'status-dot disconnected';
    }
    if (statusText) {
        statusText.textContent = statusMessage;
    }
    if (connectBtn) {
        if (connected) {
            connectBtn.classList.add('hidden');
        } else {
            connectBtn.classList.remove('hidden');
        }
    }
    if (disconnectBtn) {
        if (connected) {
            disconnectBtn.classList.remove('hidden');
        } else {
            disconnectBtn.classList.add('hidden');
        }
    }

    // Update modal BLE status
    const modalIndicator = document.getElementById('modal-ble-indicator');
    const modalStatusText = document.getElementById('modal-ble-text');
    const modalConnectBtn = document.getElementById('modal-ble-connect-btn');
    const modalDisconnectBtn = document.getElementById('modal-ble-disconnect-btn');

    if (modalIndicator) {
        modalIndicator.className = connected ? 'connected' : 'disconnected';
    }
    if (modalStatusText) {
        modalStatusText.textContent = 'BLE: ' + statusMessage;
    }
    if (modalConnectBtn) {
        if (connected) {
            modalConnectBtn.classList.add('hidden');
        } else {
            modalConnectBtn.classList.remove('hidden');
        }
    }
    if (modalDisconnectBtn) {
        if (connected) {
            modalDisconnectBtn.classList.remove('hidden');
        } else {
            modalDisconnectBtn.classList.add('hidden');
        }
    }

    // Update sticky BLE status bar
    const stickyIndicator = document.getElementById('sticky-ble-indicator');
    const stickyStatusText = document.getElementById('sticky-ble-text');
    const stickyConnectBtn = document.getElementById('sticky-ble-connect-btn');
    const stickyDisconnectBtn = document.getElementById('sticky-ble-disconnect-btn');

    if (stickyIndicator) {
        stickyIndicator.className = connected ? 'status-dot connected' : 'status-dot disconnected';
    }
    if (stickyStatusText) {
        stickyStatusText.textContent = 'BLE: ' + statusMessage;
    }
    if (stickyConnectBtn) {
        if (connected) {
            stickyConnectBtn.classList.add('hidden');
        } else {
            stickyConnectBtn.classList.remove('hidden');
        }
    }
    if (stickyDisconnectBtn) {
        if (connected) {
            stickyDisconnectBtn.classList.remove('hidden');
        } else {
            stickyDisconnectBtn.classList.add('hidden');
        }
    }

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
    const connectBtn = document.getElementById('ble-connect-btn');
    const modalConnectBtn = document.getElementById('modal-ble-connect-btn');
    const stickyConnectBtn = document.getElementById('sticky-ble-connect-btn');

    try {
        // Disable connect buttons
        if (connectBtn) connectBtn.disabled = true;
        if (modalConnectBtn) modalConnectBtn.disabled = true;
        if (stickyConnectBtn) stickyConnectBtn.disabled = true;

        updateBLEStatus(false, 'Csatlakozas...');

        // Check browser support
        if (!navigator.bluetooth) {
            showToast('Web Bluetooth nem tamogatott ebben a bongeszoben. Hasznaljon Chrome vagy Edge bongeszo!', true);
            if (connectBtn) connectBtn.disabled = false;
            if (modalConnectBtn) modalConnectBtn.disabled = false;
            updateBLEStatus(false, 'Nincs csatlakozva');
            return;
        }

        // Create BLE gateway if not exists
        if (!bleGateway) {
            bleGateway = new BLEGateway();
        }

        // Connect
        await bleGateway.connect();

        // IMPORTANT: Set flags IMMEDIATELY after successful connection
        bleConnected = true;

        // STOP WiFi polling intervals when switching to BLE mode
        if (statusInterval) {
            clearInterval(statusInterval);
            statusInterval = null;
            console.log('WiFi status polling stopped');
        }
        if (devicesInterval) {
            clearInterval(devicesInterval);
            devicesInterval = null;
            console.log('WiFi devices polling stopped');
        }
        // logsInterval keeps running — apiRequest routes to BLE automatically

        console.log('BLE flags set: bleConnected=', bleConnected);

        updateBLEStatus(true, 'Csatlakozva');
        showToast('Bluetooth kapcsolat letrejott');

        // Load initial data sequentially with delay to avoid GATT conflicts
        // Don't use setTimeout to avoid closure/scope issues
        try {
            await new Promise(resolve => setTimeout(resolve, 500));
            await loadStatus();
            await new Promise(resolve => setTimeout(resolve, 300));
            await loadDevices();
        } catch (err) {
            console.error('Error loading initial data:', err);
            // Don't show error toast - connection is still established
        }

    } catch (error) {
        console.error('BLE connection error:', error);
        showToast('Bluetooth kapcsolat sikertelen: ' + error.message, true);

        // Re-enable connect buttons
        if (connectBtn) connectBtn.disabled = false;
        if (modalConnectBtn) modalConnectBtn.disabled = false;
        if (stickyConnectBtn) stickyConnectBtn.disabled = false;

        updateBLEStatus(false, 'Kapcsolat sikertelen');
        bleConnected = false;
    }
}

function disconnectBLE() {
    if (bleGateway) {
        bleGateway.disconnect();
    }

    bleConnected = false;

    updateBLEStatus(false, 'Nincs csatlakozva');
    loadDevices(); // re-render cards to disable control buttons

    // Re-enable connect buttons
    const connectBtn = document.getElementById('ble-connect-btn');
    const modalConnectBtn = document.getElementById('modal-ble-connect-btn');
    const stickyConnectBtn = document.getElementById('sticky-ble-connect-btn');
    if (connectBtn) connectBtn.disabled = false;
    if (modalConnectBtn) modalConnectBtn.disabled = false;
    if (stickyConnectBtn) stickyConnectBtn.disabled = false;

    // RESTART WiFi polling intervals ONLY if they don't exist
    // Clear first to prevent duplicates
    if (statusInterval) {
        clearInterval(statusInterval);
        statusInterval = null;
    }
    statusInterval = setInterval(() => {
        if (shouldPollViaWiFi()) {
            loadStatus();
        }
    }, 5000);
    console.log('WiFi status polling restarted');

    if (devicesInterval) {
        clearInterval(devicesInterval);
        devicesInterval = null;
    }
    devicesInterval = setInterval(() => {
        if (shouldPollViaWiFi()) {
            loadDevices();
        }
    }, 10000);
    console.log('WiFi devices polling restarted');

    // logsInterval already running, no restart needed

    showToast('Bluetooth kapcsolat bontva');
}

// Handle BLE disconnect event
window.addEventListener('ble-disconnected', () => {
    showToast('Bluetooth kapcsolat megszakadt', true);
    disconnectBLE();
});

// ============================================================================
// API Helper Functions
// ============================================================================

async function apiRequest(endpoint, method = 'GET', body = null) {
    if (bleConnected) {
        // Use BLE
        return await bleRequest(endpoint, body);
    } else {
        // Use WiFi/HTTP
        return await httpRequest(endpoint, method, body);
    }
}

async function httpRequest(endpoint, method = 'GET', body = null) {

    const options = {
        method: method,
        headers: {
            'Content-Type': 'application/json'
        }
    };

    if (body) {
        options.body = JSON.stringify(body);
    }

    const response = await fetch(endpoint, options);
    return await response.json();
}

async function bleRequest(endpoint, body = null) {
    // Map HTTP endpoints to BLE commands
    const command = endpointToCommand(endpoint, body);

    if (!command) {
        throw new Error('Unknown endpoint: ' + endpoint);
    }

    const response = await bleGateway.sendCommand(command.cmd, command.params);

    // Convert BLE response to HTTP-like format
    if (response.status === 'ok') {
        return { success: true, ...response };
    } else if (response.status === 'error') {
        return { success: false, message: response.message };
    }

    return response;
}

function endpointToCommand(endpoint, body) {
    // Map HTTP REST endpoints to BLE JSON commands
    if (endpoint === '/api/status') {
        return { cmd: 'get_status', params: {} };
    }

    if (endpoint === '/api/devices') {
        return { cmd: 'get_devices', params: {} };
    }

    if (endpoint === '/api/rtc/set') {
        // body contains {datetime: "YYYY-MM-DD HH:MM:SS"}
        const dt = body.datetime.split(/[- :]/);
        return {
            cmd: 'set_rtc',
            params: {
                year: parseInt(dt[0]),
                month: parseInt(dt[1]),
                day: parseInt(dt[2]),
                hour: parseInt(dt[3]),
                minute: parseInt(dt[4]),
                second: parseInt(dt[5])
            }
        };
    }

    if (endpoint.startsWith('/api/devices/') && endpoint.endsWith('/config')) {
        // Device config update
        const ieeeAddr = extractIeeeFromEndpoint(endpoint);
        return {
            cmd: 'set_device_config',
            params: {
                ieee_addr: ieeeAddr,
                ...body
            }
        };
    }

    if (endpoint.startsWith('/api/devices/') && body && body.cmd) {
        // Device control
        return {
            cmd: 'control_device',
            params: body
        };
    }

    if (endpoint.startsWith('/api/devices/') && !endpoint.includes('config')) {
        // Device delete
        return {
            cmd: 'delete_device',
            params: {
                ieee_addr: extractIeeeFromEndpoint(endpoint)
            }
        };
    }

    if (endpoint === '/api/zigbee/permit-join') {
        return {
            cmd: 'permit_join',
            params: body || { duration: 60 }
        };
    }

    if (endpoint === '/api/config') {
        if (body) {
            return {
                cmd: 'set_global_settings',
                params: body
            };
        } else {
            return {
                cmd: 'get_global_settings',
                params: {}
            };
        }
    }

    if (endpoint === '/api/reboot') {
        return { cmd: 'reboot', params: {} };
    }

    if (endpoint === '/api/wifi/shutdown') {
        return { cmd: 'switch_mode', params: {} };
    }

    if (endpoint === '/api/factory-reset') {
        return {
            cmd: 'factory_reset',
            params: {}
        };
    }

    if (endpoint === '/api/rules') {
        if (body) {
            return { cmd: 'set_rules', params: body };
        } else {
            return { cmd: 'get_rules', params: {} };
        }
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
// API Functions
// ============================================================================

async function loadStatus() {
    try {
        const data = await apiRequest('/api/status');

        const statusDot = document.getElementById('status-indicator');
        const statusText = document.getElementById('status-text');
        const rtcStatus = document.getElementById('rtc-status');

        statusDot.className = 'status-dot connected';
        statusText.textContent = bleConnected ? 'Csatlakozva (BLE)' : 'Csatlakozva (WiFi)';

        // Update ESP time offset
        if (data.current_time) {
            const espDate = new Date(data.current_time.replace(' ', 'T'));
            const now = new Date();
            espTimeOffset = espDate.getTime() - now.getTime();
            espTimeInitialized = true;
        }

        // Update Zigbee status
        zigbeeActive = data.zigbee_active || false;
        updateControlButtons();

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

        // If BLE fails, try to reconnect
        if (bleConnected) {
            disconnectBLE();
        }
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
        const data = await apiRequest('/api/rtc/set', 'POST', { datetime: datetime });

        if (data.success || data.status === 'ok') {
            showToast('Ora sikeresen beallitva!');
            // Reset ESP time to show immediately
            espTimeOffset = 0;
            espTimeInitialized = false;
            loadStatus();
        } else {
            showToast(data.message || 'Hiba tortent', true);
        }
    } catch (error) {
        console.error('RTC set error:', error);
        showToast('Kapcsolati hiba', true);
    }
}

async function syncRtc() {
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    const hour = String(now.getHours()).padStart(2, '0');
    const minute = String(now.getMinutes()).padStart(2, '0');
    const second = String(now.getSeconds()).padStart(2, '0');

    const datetime = `${year}-${month}-${day} ${hour}:${minute}:${second}`;

    try {
        const data = await apiRequest('/api/rtc/set', 'POST', { datetime: datetime });

        if (data.success || data.status === 'ok') {
            showToast('Ora szinkronizalva!');
            espTimeOffset = 0;  // After sync, offset should be ~0
            espTimeInitialized = true;
            loadStatus();
        } else {
            showToast(data.message || 'Hiba tortent', true);
        }
    } catch (error) {
        console.error('RTC sync error:', error);
        showToast('Kapcsolati hiba', true);
    }
}

async function loadDevices() {
    try {
        console.log('loadDevices: Requesting device list...');
        const data = await apiRequest('/api/devices');
        console.log('loadDevices: Received data:', data);
        console.log('loadDevices: Device count:', data.devices ? data.devices.length : 0);
        devices = data.devices || [];
        renderDevices();

        // Refresh open modal if a device is being edited and user hasn't modified fields
        if (currentEditDevice && !modalDirty && !document.getElementById('device-modal').classList.contains('hidden')) {
            const updated = devices.find(d =>
                d.ieee_addr === currentEditDevice.ieee_addr &&
                d.endpoint === currentEditDevice.endpoint &&
                d.device_type === currentEditDevice.device_type
            );
            if (updated) {
                editDevice(updated.ieee_addr, updated.endpoint, updated.device_type);
            }
        }
    } catch (error) {
        console.error('Device load error:', error);
    }
}

async function refreshDevices() {
    const refreshBtn = document.getElementById('refresh-devices-btn');

    // Disable button during refresh
    if (refreshBtn) {
        refreshBtn.disabled = true;
        refreshBtn.textContent = 'Frissites...';
    }

    try {
        await loadDevices();
        showToast('Eszkoklista frissitve');
    } catch (error) {
        console.error('Refresh error:', error);
        showToast('Frissites sikertelen', true);
    } finally {
        // Re-enable button
        if (refreshBtn) {
            refreshBtn.disabled = false;
            refreshBtn.textContent = 'Frissites';
        }
    }
}

// permitJoin() and startPermitJoinTimer() removed - pairing is done via physical button on ESP

async function deleteDevice(ieeeAddr, endpoint) {
    if (!confirm('Biztosan torolni szeretne ezt az eszkozt?')) {
        return;
    }

    try {
        const data = await apiRequest('/api/devices/' + ieeeAddr, 'DELETE');

        if (data.success || data.status === 'ok') {
            showToast('Eszkoz torolve');

            // Remove ALL entries with this IEEE address (multi-endpoint devices share one IEEE)
            devices = devices.filter(d => d.ieee_addr !== ieeeAddr);
            renderDevices();

            // Also reload from server after a short delay (especially for BLE)
            setTimeout(() => {
                loadDevices();
            }, 500);
        } else {
            showToast(data.message || 'Hiba tortent', true);
        }
    } catch (error) {
        console.error('Delete error:', error);
        showToast('Kapcsolati hiba', true);
    }
}

async function saveDeviceConfig() {
    const ieeeAddr = document.getElementById('edit-ieee-addr').value;
    const deviceType = document.getElementById('edit-device-type').value;
    const isSensor = isSensorDevice(deviceType);

    const config = {
        custom_name: document.getElementById('edit-name').value,
        device_type: deviceType
    };

    if (isSensor) {
        const errorLinkedVal = document.getElementById('edit-error-linked').value;
        const errorActionRadio = document.querySelector('input[name="error-action"]:checked');

        config.sensor = {
            lower_threshold: parseFloat(document.getElementById('edit-lower-threshold').value),
            lower_hysteresis: parseFloat(document.getElementById('edit-lower-hysteresis').value),
            upper_threshold: parseFloat(document.getElementById('edit-upper-threshold').value),
            upper_hysteresis: parseFloat(document.getElementById('edit-upper-hysteresis').value),
            lower_linked_device: document.getElementById('edit-lower-linked').value || null,
            upper_linked_device: document.getElementById('edit-upper-linked').value || null,
            lower_delay_seconds: parseInt(document.getElementById('edit-lower-delay').value) || 0,
            upper_delay_seconds: parseInt(document.getElementById('edit-upper-delay').value) || 0,
            timeout_seconds: parseInt(document.getElementById('edit-timeout').value),
            error_linked_device: errorLinkedVal || null,
            error_action_on: errorActionRadio ? errorActionRadio.value === 'on' : false,
            report_min_interval: parseInt(document.getElementById('edit-report-min').value),
            report_max_interval: parseInt(document.getElementById('edit-report-max').value),
            report_change: parseInt(document.getElementById('edit-report-change').value)
        };
    } else {
        const mode = document.getElementById('edit-mode').value;
        config.enabled = document.getElementById('edit-enabled').checked;
        config.mode = mode;

        if (mode === 'fixed_time') {
            config.time_pairs = getTimePairs();
        } else {
            config.delay_off1_minutes = parseInt(document.getElementById('edit-delay-off1').value);
            config.delay_duration_minutes = parseInt(document.getElementById('edit-delay-duration').value);
            config.delay_off2_minutes = parseInt(document.getElementById('edit-delay-off2').value);
        }
    }

    try {
        const data = await apiRequest('/api/devices/' + ieeeAddr + '/config', 'POST', config);

        if (data.success || data.status === 'ok') {
            showToast('Beallitasok mentve');
            closeModal();

            // Wait a bit for NVS write to complete before reloading
            await new Promise(resolve => setTimeout(resolve, 500));
            await loadDevices();
        } else {
            showToast(data.message || 'Hiba tortent', true);
        }
    } catch (error) {
        console.error('Save error:', error);
        showToast('Kapcsolati hiba', true);
    }
}

async function loadGlobalConfig() {
    try {
        const data = await apiRequest('/api/config');

        // WiFi behavior
        if (data.wifi_on_behavior) {
            document.getElementById('wifi-maintain').checked = true;
        } else {
            document.getElementById('wifi-poweroff').checked = true;
        }

        // Log filter
        document.getElementById('log-zigbee-only').checked = data.log_zigbee_only || false;

        // Rules enabled (default true if not set)
        document.getElementById('rules-enabled').checked = (data.rules_enabled !== false);

        // Local XKC sensor
        const xkcEnabled = data.local_xkc_enabled || false;
        document.getElementById('xkc-enabled').checked = xkcEnabled;

        // Populate GPIO dropdowns
        const validGpios = data.valid_xkc_gpios || [3, 4, 5, 6, 7, 10, 11, 14, 18, 19, 20, 21, 22, 23];
        populateGpioSelect('xkc-gpio-lower', validGpios, data.local_xkc_gpio_lower || 4);
        populateGpioSelect('xkc-gpio-upper', validGpios, data.local_xkc_gpio_upper || 5);

        // Show/hide GPIO settings
        document.getElementById('xkc-gpio-settings').classList.toggle('hidden', !xkcEnabled);
    } catch (error) {
        console.error('Config load error:', error);
    }
}

function populateGpioSelect(selectId, validPins, selectedPin) {
    const select = document.getElementById(selectId);
    select.innerHTML = validPins.map(pin =>
        '<option value="' + pin + '"' + (pin === selectedPin ? ' selected' : '') + '>GPIO ' + pin + '</option>'
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
    await apiRequest('/api/config', 'POST', { log_zigbee_only: enabled });
}

async function onRulesEnabledChange() {
    const enabled = document.getElementById('rules-enabled').checked;
    await apiRequest('/api/config', 'POST', { rules_enabled: enabled });
}

async function saveGlobalConfig() {
    const maintain = document.getElementById('wifi-maintain').checked;
    const xkcEnabled = document.getElementById('xkc-enabled').checked;
    const zigbeeOnly = document.getElementById('log-zigbee-only').checked;
    const gpioLower = parseInt(document.getElementById('xkc-gpio-lower').value);
    const gpioUpper = parseInt(document.getElementById('xkc-gpio-upper').value);

    // Validate GPIO pins are different
    if (xkcEnabled && gpioLower === gpioUpper) {
        showToast('Az also es felso szenzor nem lehet ugyanaz a GPIO lab!', true);
        return;
    }

    try {
        const data = await apiRequest('/api/config', 'POST', {
            wifi_on_behavior: maintain,
            local_xkc_enabled: xkcEnabled,
            local_xkc_gpio_lower: gpioLower,
            local_xkc_gpio_upper: gpioUpper,
            log_zigbee_only: zigbeeOnly,
        });

        if (data.success || data.status === 'ok') {
            showToast('Beallitasok mentve');
            // Reload devices to see the new/removed virtual device
            setTimeout(function() { loadDevices(); }, 1000);
        } else {
            showToast(data.message || 'Hiba tortent', true);
        }
    } catch (error) {
        console.error('Save config error:', error);
        showToast('Kapcsolati hiba', true);
    }
}

async function rebootDevice() {
    if (!confirm('Biztosan ujra szeretne inditani az eszkoezt?')) return;
    try {
        await apiRequest('/api/reboot', 'POST');
        showToast('Ujrainditas folyamatban...');
    } catch (error) {
        console.error('Reboot error:', error);
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
        console.error('Mode switch error:', error);
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
        console.error('Pairing error:', error);
        showToast('Kapcsolati hiba', true);
    }
}

async function sendDeviceCmd(ieeeAddr, deviceType, cmd) {
    try {
        await apiRequest('/api/devices/' + ieeeAddr + '/config', 'POST', { cmd: cmd, device_type: deviceType });
        showToast(cmd.toUpperCase() + ' parancs elkuldve');
    } catch (error) {
        console.error('Device cmd error:', error);
        showToast('Kapcsolati hiba', true);
    }
}

async function factoryReset() {
    if (!confirm('Biztosan torolni szeretne az osszes eszkozt es beallitast? Ez a muvelet nem vonhato vissza!')) {
        return;
    }

    if (!confirm('Utolso figyelmeztetes: Az osszes adat torlodik. Folytatja?')) {
        return;
    }

    try {
        const data = await apiRequest('/api/factory-reset', 'POST');

        if (data.success || data.status === 'ok') {
            showToast('Gyari alaphelyzetbe allitas sikeres');
            setTimeout(() => {
                if (bleConnected) {
                    // If using BLE, just reload the page
                    location.reload();
                } else {
                    // If using WiFi, device might restart
                    location.reload();
                }
            }, 3000);
        } else {
            showToast(data.message || 'Hiba tortent', true);
        }
    } catch (error) {
        console.error('Factory reset error:', error);
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

    container.innerHTML = devices.map(device => {
        if (isSensorDevice(device.device_type)) {
            return renderSensorCard(device);
        }
        return renderOnOffCard(device);
    }).join('');
}

function renderOnOffCard(device) {
    const sensorControllers = getSensorControlInfo(device.ieee_addr);
    const isSensorControlled = sensorControllers.length > 0;
    const canControl = bleConnected && zigbeeActive;
    const ctrlDisabled = canControl ? '' : 'disabled title="Csak BLE+Zigbee uzemmodban elerheto"';

    let sensorBadgesHtml = '';
    if (isSensorControlled) {
        const badges = sensorControllers.map(s => {
            const roles = [];
            if (s.sensor.lower_linked_device === device.ieee_addr) roles.push('also korlat');
            if (s.sensor.upper_linked_device === device.ieee_addr) roles.push('felso korlat');
            if (s.sensor.error_linked_device === device.ieee_addr) roles.push('hiba');
            return `<span class="sensor-control-badge">${escapeHtml(s.custom_name)} (${roles.join(', ')})</span>`;
        }).join('');
        sensorBadgesHtml = `<div class="sensor-control-info">Szenzor vezerles: ${badges}</div>`;
    }

    return `
        <div class="device-item${isSensorControlled ? ' sensor-controlled' : ''}">
            <div class="device-info">
                <div class="device-name">${escapeHtml(device.custom_name)}</div>
                <div class="device-ctrl-buttons">
                    <button onclick="sendDeviceCmd('${device.ieee_addr}', '${device.device_type}', 'on')" class="btn btn-success btn-small" ${ctrlDisabled}>BE</button>
                    <button onclick="sendDeviceCmd('${device.ieee_addr}', '${device.device_type}', 'off')" class="btn btn-secondary btn-small" ${ctrlDisabled}>KI</button>
                    <button onclick="sendDeviceCmd('${device.ieee_addr}', '${device.device_type}', 'toggle')" class="btn btn-primary btn-small" ${ctrlDisabled}>Valtas</button>
                </div>
                <div class="device-manufacturer">${escapeHtml(device.manufacturer || 'Ismeretlen gyarto')}</div>
                <div class="device-model">${escapeHtml(device.model || 'Ismeretlen model')}</div>
                <div class="device-addr">${device.ieee_addr} | EP: ${device.endpoint}</div>
                ${sensorBadgesHtml}
                <div class="device-status">
                    ${isSensorControlled
                        ? '<span class="state-badge state-sensor">Szenzor vezerelt</span>'
                        : `<span class="state-badge ${device.enabled ? 'state-on' : 'state-off'}">
                            ${device.enabled ? 'Automatizacio BE' : 'Automatizacio KI'}
                          </span>`
                    }
                    ${device.error ? `<span class="error-badge">&#9888; ${escapeHtml(device.error.message)} (${device.error.timestamp})</span>` : ''}
                </div>
            </div>
            <div class="device-actions">
                <button onclick="editDevice('${device.ieee_addr}', ${device.endpoint}, '${device.device_type}')" class="btn btn-primary btn-small">Szerkesztes</button>
                <button onclick="deleteDevice('${device.ieee_addr}', ${device.endpoint})" class="btn btn-danger btn-small">Torles</button>
            </div>
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
            <div class="device-info">
                <div class="device-name">${escapeHtml(device.custom_name)}</div>
                <div class="device-manufacturer">${escapeHtml(device.manufacturer || 'Ismeretlen gyarto')}</div>
                <div class="device-model">${escapeHtml(device.model || 'Ismeretlen model')}</div>
                <div class="device-addr">${device.ieee_addr} | EP: ${device.endpoint}</div>
                <div class="sensor-value-display">
                    <span class="sensor-value-number">${valueStr}</span>
                    <span class="sensor-value-unit">${unit}</span>
                </div>
                ${batteryHtml}
                <div class="device-status">${errorHtml}</div>
            </div>
            <div class="device-actions">
                <button onclick="editDevice('${device.ieee_addr}', ${device.endpoint}, '${device.device_type}')" class="btn btn-primary btn-small">Szerkesztes</button>
                <button onclick="deleteDevice('${device.ieee_addr}', ${device.endpoint})" class="btn btn-danger btn-small">Torles</button>
            </div>
        </div>`;
}

function renderBatteryInfo(sensor) {
    if (sensor.battery_percent === 255 && sensor.battery_voltage_100mv === 255) {
        return '';
    }

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

function renderLinkedDeviceSelect(selectId, currentValue) {
    const select = document.getElementById(selectId);
    if (!select) return;
    const onOffDevs = getOnOffDevices();
    select.innerHTML = '<option value="">-- Nincs --</option>' +
        onOffDevs.map(d => `<option value="${d.ieee_addr}" ${d.ieee_addr === currentValue ? 'selected' : ''}>
            ${escapeHtml(d.custom_name)} (${d.ieee_addr})
        </option>`).join('');
}

function updateErrorActionVisibility() {
    const errorLinked = document.getElementById('edit-error-linked').value;
    const errorActionGroup = document.getElementById('error-action-group');
    if (errorLinked) {
        errorActionGroup.classList.remove('hidden');
    } else {
        errorActionGroup.classList.add('hidden');
    }
}

function updateThresholdLogicDisplay() {
    const lowerT = parseFloat(document.getElementById('edit-lower-threshold').value) || 0;
    const lowerH = parseFloat(document.getElementById('edit-lower-hysteresis').value) || 0;
    const upperT = parseFloat(document.getElementById('edit-upper-threshold').value) || 0;
    const upperH = parseFloat(document.getElementById('edit-upper-hysteresis').value) || 0;
    const lowerDelay = parseInt(document.getElementById('edit-lower-delay').value) || 0;
    const upperDelay = parseInt(document.getElementById('edit-upper-delay').value) || 0;
    const deviceType = document.getElementById('edit-device-type').value;
    const unit = getSensorUnit(deviceType);

    const lowerDiv = document.getElementById('lower-threshold-logic');
    if (lowerDiv) {
        const delayStr = lowerDelay > 0
            ? ` → ${lowerDelay}mp varakozas → BE (ha meg &lt; ${lowerT.toFixed(1)}${unit})`
            : ' → BE';
        lowerDiv.innerHTML =
            `<span class="logic-on">ertek &lt; ${lowerT.toFixed(1)}${unit}${delayStr}</span>` +
            `<span class="logic-off">ertek &gt; ${(lowerT + lowerH).toFixed(1)}${unit} → KI</span>`;
    }

    const upperDiv = document.getElementById('upper-threshold-logic');
    if (upperDiv) {
        const delayStr = upperDelay > 0
            ? ` → ${upperDelay}mp varakozas → BE (ha meg &gt; ${upperT.toFixed(1)}${unit})`
            : ' → BE';
        upperDiv.innerHTML =
            `<span class="logic-on">ertek &gt; ${upperT.toFixed(1)}${unit}${delayStr}</span>` +
            `<span class="logic-off">ertek &lt; ${(upperT - upperH).toFixed(1)}${unit} → KI</span>`;
    }
}

function updateReportChangeDisplay() {
    const rawValue = parseInt(document.getElementById('edit-report-change').value) || 50;
    const deviceType = document.getElementById('edit-device-type').value;
    const converted = (rawValue / 100).toFixed(2);
    const unit = getSensorUnit(deviceType);
    document.getElementById('report-change-display').textContent = converted;
    document.getElementById('report-change-unit').textContent = unit;
}

// ============================================================================
// Modal Functions
// ============================================================================

function editDevice(ieeeAddr, endpoint, deviceType) {
    const device = devices.find(d => d.ieee_addr === ieeeAddr && d.endpoint === endpoint && d.device_type === deviceType);
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
            (sensor.valid && typeof sensor.current_value === 'number') ? sensor.current_value.toFixed(1) : '--';
        document.getElementById('edit-value-unit').textContent = unit;

        document.getElementById('edit-lower-threshold').value = sensor.lower_threshold ?? 18.0;
        document.getElementById('edit-lower-hysteresis').value = sensor.lower_hysteresis ?? 0.5;
        document.getElementById('edit-upper-threshold').value = sensor.upper_threshold ?? 25.0;
        document.getElementById('edit-upper-hysteresis').value = sensor.upper_hysteresis ?? 0.5;
        document.getElementById('edit-timeout').value = sensor.timeout_seconds ?? 60;

        document.getElementById('edit-report-min').value = sensor.report_min_interval ?? 0;
        document.getElementById('edit-report-max').value = sensor.report_max_interval ?? 10;
        document.getElementById('edit-report-change').value = sensor.report_change ?? 50;

        document.getElementById('edit-lower-delay').value = sensor.lower_delay_seconds ?? 0;
        document.getElementById('edit-upper-delay').value = sensor.upper_delay_seconds ?? 0;

        // Set error action radio
        const errorActionVal = sensor.error_action_on ? 'on' : 'off';
        document.querySelectorAll('input[name="error-action"]').forEach(r => {
            r.checked = (r.value === errorActionVal);
        });

        // Populate linked device dropdowns
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

        document.getElementById('edit-delay-off1').value = (device.delay_off1_minutes !== undefined) ? device.delay_off1_minutes : 0;
        document.getElementById('edit-delay-duration').value = (device.delay_duration_minutes !== undefined) ? device.delay_duration_minutes : 120;
        document.getElementById('edit-delay-off2').value = (device.delay_off2_minutes !== undefined) ? device.delay_off2_minutes : 30;

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
            warningDiv.innerHTML = `&#9888; Ez az eszkoz szenzor altal vezerelt (${names}). Az idozites beallitasok nem ervenyesulnek, amig szenzor vezerles aktiv.`;
            warningDiv.classList.remove('hidden');
        } else if (warningDiv) {
            warningDiv.classList.add('hidden');
        }
    }

    document.getElementById('device-modal').classList.remove('hidden');
    updateBLEStatus(bleConnected, bleConnected ? 'Csatlakozva' : 'Nincs csatlakozva');
}

function closeModal() {
    document.getElementById('device-modal').classList.add('hidden');
    currentEditDevice = null;
    modalDirty = false;
}

function onModeChange() {
    const mode = document.getElementById('edit-mode').value;
    const fixedSettings = document.getElementById('fixed-time-settings');
    const delaySettings = document.getElementById('delay-settings');

    if (mode === 'fixed_time') {
        fixedSettings.classList.remove('hidden');
        delaySettings.classList.add('hidden');
    } else {
        fixedSettings.classList.add('hidden');
        delaySettings.classList.remove('hidden');
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
    const currentCount = container.children.length;

    if (currentCount >= 5) {
        showToast('Maximum 5 idopont par adhato meg', true);
        return;
    }

    const div = document.createElement('div');
    div.className = 'time-pair';
    div.dataset.index = currentCount;
    div.innerHTML = `
        <label>ON:</label>
        <input type="time" class="time-on" value="08:00">
        <label>OFF:</label>
        <input type="time" class="time-off" value="20:00">
        <button type="button" class="remove-btn" onclick="removeTimePair(${currentCount})">&times;</button>
    `;
    container.appendChild(div);
}

function removeTimePair(index) {
    const container = document.getElementById('time-pairs-container');
    const pairs = Array.from(container.children);

    if (pairs.length <= 1) return;

    pairs[index].remove();

    // Re-index remaining pairs
    Array.from(container.children).forEach((pair, i) => {
        pair.dataset.index = i;
        const removeBtn = pair.querySelector('.remove-btn');
        if (removeBtn) {
            removeBtn.onclick = () => removeTimePair(i);
        }
    });
}

function getTimePairs() {
    const container = document.getElementById('time-pairs-container');
    const pairs = [];

    container.querySelectorAll('.time-pair').forEach(pair => {
        const on = pair.querySelector('.time-on').value;
        const off = pair.querySelector('.time-off').value;
        if (on && off) {
            pairs.push({ on, off });
        }
    });

    return pairs;
}

// ============================================================================
// Rules Engine Functions
// ============================================================================

let rulesData = null;

function toggleRulesEditor() {
    const section = document.getElementById('rules-editor-section');
    const btn = document.getElementById('rules-toggle-btn');
    if (section.classList.contains('hidden')) {
        section.classList.remove('hidden');
        btn.textContent = 'Bezaras';
        loadRules();
    } else {
        section.classList.add('hidden');
        btn.textContent = 'Szerkesztes';
    }
}

async function loadRules() {
    try {
        const data = await apiRequest('/api/rules');
        rulesData = data;

        // Update editor
        const editor = document.getElementById('rules-editor');
        if (editor && data.text !== undefined) {
            editor.value = data.text;
        }

        // Update status
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
    const editor = document.getElementById('rules-editor');
    const statusEl = document.getElementById('rules-save-status');

    try {
        statusEl.textContent = 'Mentes...';
        statusEl.className = '';

        const data = await apiRequest('/api/rules', 'POST', { text: editor.value });

        if (data.ok) {
            statusEl.textContent = (data.rule_count || 0) + ' szabaly mentve';
            statusEl.className = 'rules-status-ok';
            document.getElementById('rules-count').textContent = (data.rule_count || 0) + ' szabaly betoltve';
            showToast('Szabalyok mentve');
            // Reload to get updated state
            setTimeout(loadRules, 500);
        } else {
            statusEl.textContent = data.error || 'Parse hiba';
            statusEl.className = 'rules-status-error';
            showToast(data.error || 'Szabaly hiba', true);
        }
    } catch (error) {
        console.error('Rules save error:', error);
        statusEl.textContent = 'Kapcsolati hiba';
        statusEl.className = 'rules-status-error';
        showToast('Kapcsolati hiba', true);
    }
}

function renderRulesState(data) {
    if (!data) return;

    // Render variables
    const varsContainer = document.getElementById('rules-vars-list');
    if (varsContainer && data.variables) {
        varsContainer.innerHTML = data.variables.map((val, i) => {
            let persistIcon = '';
            if (data.var_config && data.var_config[i] !== undefined) {
                const cfg = data.var_config[i];
                if (cfg.persist) {
                    persistIcon = ' <span title="Persistent">&#128190;</span>';
                } else {
                    persistIcon = ` <span title="Non-persistent (default: ${cfg.default})">&#128260;(${cfg.default})</span>`;
                }
            }
            return `<div class="rules-card rules-var-card">
                    <span class="rules-card-name">var${i+1}</span>
                    <span class="rules-card-value">${val}${persistIcon}</span>
                    <button onclick="editRulesVar(${i})" class="btn btn-secondary btn-tiny">&#9998;</button>
                    <button onclick="configRulesVar(${i})" class="btn btn-secondary btn-tiny">&#9881;</button>
                </div>`;
        }).join('');
    }

    renderTimers(data.timers);
}

function renderTimers(timers) {
    const timersContainer = document.getElementById('rules-timers-list');
    if (!timersContainer || !timers) return;
    const activeTimers = timers.filter(t => t.active);
    if (activeTimers.length > 0) {
        timersContainer.innerHTML = activeTimers.map(t =>
            `<div class="rules-card rules-timer-card">
                <span class="rules-card-name">timer ${t.id}</span>
                <span class="rules-card-value">${t.remaining} mp</span>
                <span class="rules-card-status active">Aktiv</span>
            </div>`
        ).join('');
    } else {
        timersContainer.innerHTML = '<span class="hint">Nincs aktiv timer</span>';
    }
}

async function pollTimers() {
    try {
        const data = await apiRequest('/api/rules/timers');
        if (data && data.timers) renderTimers(data.timers);
    } catch (e) { /* silent */ }
}

async function editRulesVar(index) {
    const cfg = rulesData && rulesData.var_config ? rulesData.var_config[index] : { persist: true, default: 0 };
    const isPersist = cfg.persist;

    const label = isPersist
        ? 'var' + (index+1) + ' uj erteke:'
        : 'var' + (index+1) + ' alaperteke (boot utan erre all):';
    const current = isPersist
        ? (rulesData ? rulesData.variables[index] : 0)
        : cfg.default;

    const newVal = prompt(label, current);
    if (newVal === null) return;

    const val = parseFloat(newVal);
    if (isNaN(val)) {
        showToast('Ervenytelen szam', true);
        return;
    }

    try {
        if (isPersist) {
            await apiRequest('/api/rules/var', 'POST', { index: index, value: val });
            showToast('var' + (index+1) + ' = ' + val);
        } else {
            await apiRequest('/api/rules/varconfig', 'POST', { index: index, persist: false, default_value: val });
            showToast('var' + (index+1) + ' alapertek = ' + val);
        }
        loadRules();
    } catch (error) {
        console.error('Set var error:', error);
        showToast('Hiba', true);
    }
}

async function configRulesVar(index) {
    const cfg = rulesData && rulesData.var_config ? rulesData.var_config[index] : { persist: true, default: 0 };
    const persistChoice = confirm(
        'var' + (index+1) + ' konfig\n\n' +
        'Jelenlegi: ' + (cfg.persist ? 'Persistent (NVS-ben marad)' : 'Non-persistent (alapertek: ' + cfg.default + ')') + '\n\n' +
        'OK = Persistent (NVS-ben tarolodik)\n' +
        'Megse = Non-persistent (alapertekre all boot-kor)'
    );

    let defaultVal = cfg.default;
    if (!persistChoice) {
        const defInput = prompt('Alapertek (boot-kor ezt kapja var' + (index+1) + '):', cfg.default);
        if (defInput === null) return;
        defaultVal = parseFloat(defInput);
        if (isNaN(defaultVal)) {
            showToast('Ervenytelen szam', true);
            return;
        }
    }

    try {
        await apiRequest('/api/rules/varconfig', 'POST', {
            index: index,
            persist: persistChoice,
            default_value: defaultVal
        });
        showToast('var' + (index+1) + ' konfig mentve');
        loadRules();
    } catch (error) {
        console.error('Config var error:', error);
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
        console.error('Rules reset error:', error);
        showToast('Hiba', true);
    }
}

// ============================================================================
// Utility Functions
// ============================================================================

function showToast(message, isError = false) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = 'toast' + (isError ? ' error' : '');

    setTimeout(() => {
        toast.classList.add('hidden');
    }, 3000);
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Mark modal dirty when user modifies any field
document.getElementById('device-form').addEventListener('input', function() {
    modalDirty = true;
});
document.getElementById('device-form').addEventListener('change', function() {
    modalDirty = true;
});

// Close modal on escape key
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        closeModal();
    }
});

// Close modal on outside click
document.getElementById('device-modal').addEventListener('click', function(e) {
    if (e.target === this) {
        closeModal();
    }
});

// ============================================================================
// Log Viewer
// ============================================================================

function showLogTab(tab) {
    const live    = document.getElementById('log-tab-live');
    const history = document.getElementById('log-tab-history');
    const btnLive = document.getElementById('log-tab-btn-live');
    const btnHist = document.getElementById('log-tab-btn-history');

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

function escapeHtml(text) {
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

function renderLiveLog(lines) {
    const box = document.getElementById('live-log-box');
    if (!box || !lines) return;
    box.innerHTML = lines.map(line => {
        const level = line[0] || 'I';
        const cls = ['E','W','I','D'].includes(level) ? 'log-line-' + level : 'log-line-I';
        return '<div class="' + cls + '">' + escapeHtml(line) + '</div>';
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
        // silent - live log polling failure is non-critical
    }
}

async function loadHistorySessions() {
    try {
        const data = await apiRequest('/api/logs/history');
        const sel = document.getElementById('history-session-select');
        sel.innerHTML = '<option value="">— Session választása —</option>' +
            (data.sessions || []).map(s =>
                '<option value="' + s.name + '">' + s.name +
                ' (' + formatBytes(s.size) + ')' +
                (s.current ? ' ← jelenlegi' : '') + '</option>'
            ).join('');
    } catch (e) {
        console.error('loadHistorySessions error:', e);
    }
}

function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    return (bytes / 1024).toFixed(1) + ' KB';
}

async function loadHistoryContent() {
    const sel = document.getElementById('history-session-select');
    const box = document.getElementById('history-log-box');
    if (!sel || !box || !sel.value) return;

    box.innerHTML = '<div style="color:#888">Betöltés...</div>';
    try {
        const resp = await fetch('/api/logs/history?file=' + encodeURIComponent(sel.value));
        const text = await resp.text();
        box.innerHTML = '<div class="log-line-I">' + escapeHtml(text).replace(/\n/g, '</div><div class="log-line-I">') + '</div>';
        box.scrollTop = box.scrollHeight;
    } catch (e) {
        box.innerHTML = '<div class="log-line-E">Betöltés sikertelen</div>';
    }
}

async function clearLogHistory() {
    if (!confirm('Biztosan törlöd az összes előzményt?')) return;
    try {
        await fetch('/api/logs', { method: 'DELETE' });
        document.getElementById('history-log-box').innerHTML = '';
        document.getElementById('history-session-select').innerHTML =
            '<option value="">— Session választása —</option>';
        showToast('Napló előzmények törölve');
    } catch (e) {
        showToast('Törlés sikertelen', true);
    }
}


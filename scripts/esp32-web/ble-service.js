/**
 * @file ble-service.js
 * @brief Web Bluetooth API abstraction for ESP32-C6 Gateway
 */

class BLEGateway {
    constructor() {
        this.device = null;
        this.server = null;
        this.service = null;
        this.characteristics = {};
        this.responseCallbacks = [];
        this.commandQueue = [];
        this.commandInProgress = false;

        // Chunked response state
        this.responseChunks = [];
        this.expectedChunks = 0;

        // Store bound handlers to allow proper cleanup
        this._boundHandlers = {
            responseNotification: null,
            deviceListNotification: null,
            statusNotification: null,
            disconnect: null
        };
    }

    /**
     * Connect to ESP32-C6 Gateway via Bluetooth
     */
    async connect() {
        try {
            console.log('Requesting Bluetooth device...');

            this.device = await navigator.bluetooth.requestDevice({
                filters: [{ name: 'ESP32C6_Gateway' }],
                optionalServices: ['0000fff0-0000-1000-8000-00805f9b34fb']
            });

            console.log('Connecting to GATT server...');
            this.server = await this.device.gatt.connect();

            console.log('Getting primary service...');
            this.service = await this.server.getPrimaryService('0000fff0-0000-1000-8000-00805f9b34fb');

            // Get all characteristics
            console.log('Getting characteristics...');
            this.characteristics.cmdReq = await this.service.getCharacteristic('0000fff1-0000-1000-8000-00805f9b34fb');
            this.characteristics.cmdRes = await this.service.getCharacteristic('0000fff2-0000-1000-8000-00805f9b34fb');
            this.characteristics.deviceList = await this.service.getCharacteristic('0000fff3-0000-1000-8000-00805f9b34fb');
            this.characteristics.status = await this.service.getCharacteristic('0000fff4-0000-1000-8000-00805f9b34fb');

            // Start notifications
            console.log('Starting notifications...');
            await this.characteristics.cmdRes.startNotifications();
            await this.characteristics.deviceList.startNotifications();
            await this.characteristics.status.startNotifications();

            // Create bound handlers (only once)
            if (!this._boundHandlers.responseNotification) {
                this._boundHandlers.responseNotification = this._handleResponseNotification.bind(this);
                this._boundHandlers.deviceListNotification = this._handleDeviceListNotification.bind(this);
                this._boundHandlers.statusNotification = this._handleStatusNotification.bind(this);
                this._boundHandlers.disconnect = this._handleDisconnect.bind(this);
            }

            // Setup notification handlers (remove old ones first to prevent duplicates)
            this.characteristics.cmdRes.removeEventListener('characteristicvaluechanged',
                this._boundHandlers.responseNotification);
            this.characteristics.cmdRes.addEventListener('characteristicvaluechanged',
                this._boundHandlers.responseNotification);

            this.characteristics.deviceList.removeEventListener('characteristicvaluechanged',
                this._boundHandlers.deviceListNotification);
            this.characteristics.deviceList.addEventListener('characteristicvaluechanged',
                this._boundHandlers.deviceListNotification);

            this.characteristics.status.removeEventListener('characteristicvaluechanged',
                this._boundHandlers.statusNotification);
            this.characteristics.status.addEventListener('characteristicvaluechanged',
                this._boundHandlers.statusNotification);

            // Setup disconnect handler (remove old one first)
            this.device.removeEventListener('gattserverdisconnected',
                this._boundHandlers.disconnect);
            this.device.addEventListener('gattserverdisconnected',
                this._boundHandlers.disconnect);

            console.log('BLE connection established');
            return true;
        } catch (error) {
            console.error('BLE connection failed:', error);
            throw error;
        }
    }

    /**
     * Disconnect from device
     */
    disconnect() {
        // Clean up event listeners
        if (this.device && this._boundHandlers.disconnect) {
            this.device.removeEventListener('gattserverdisconnected',
                this._boundHandlers.disconnect);
        }

        if (this.characteristics.cmdRes && this._boundHandlers.responseNotification) {
            this.characteristics.cmdRes.removeEventListener('characteristicvaluechanged',
                this._boundHandlers.responseNotification);
        }

        if (this.characteristics.deviceList && this._boundHandlers.deviceListNotification) {
            this.characteristics.deviceList.removeEventListener('characteristicvaluechanged',
                this._boundHandlers.deviceListNotification);
        }

        if (this.characteristics.status && this._boundHandlers.statusNotification) {
            this.characteristics.status.removeEventListener('characteristicvaluechanged',
                this._boundHandlers.statusNotification);
        }

        // Disconnect GATT
        if (this.device && this.device.gatt.connected) {
            this.device.gatt.disconnect();
        }

        // Clear state
        this.responseCallbacks = [];
        this.commandQueue = [];
        this.commandInProgress = false;
    }

    /**
     * Check if connected
     */
    isConnected() {
        return this.device && this.server && this.server.connected;
    }

    /**
     * Send command to ESP32 (with queuing to prevent GATT conflicts)
     */
    async sendCommand(cmd, params = {}) {
        if (!this.isConnected()) {
            throw new Error('Not connected to device');
        }

        // Add command to queue
        return new Promise((resolve, reject) => {
            this.commandQueue.push({ cmd, params, resolve, reject });
            this._processCommandQueue();
        });
    }

    /**
     * Process command queue sequentially
     */
    async _processCommandQueue() {
        // If already processing, just return
        if (this.commandInProgress || this.commandQueue.length === 0) {
            return;
        }

        this.commandInProgress = true;
        console.log('Starting command queue processing, queue length:', this.commandQueue.length);

        while (this.commandQueue.length > 0) {
            const { cmd, params, resolve, reject } = this.commandQueue.shift();

            try {
                const payload = JSON.stringify({ cmd, params });
                console.log('Sending command:', payload, 'callbacks queue before:', this.responseCallbacks.length);

                const encoder = new TextEncoder();
                const data = encoder.encode(payload);

                // Wait for response (setup callback BEFORE sending to avoid race condition)
                const responsePromise = new Promise((resolveResp, rejectResp) => {
                    const timeout = setTimeout(() => {
                        // Remove callback from queue on timeout
                        const index = this.responseCallbacks.findIndex(cb => cb.timeout === timeout);
                        if (index !== -1) {
                            console.warn('Command timeout, removing callback from queue');
                            this.responseCallbacks.splice(index, 1);
                        }
                        rejectResp(new Error('Command timeout'));
                    }, 10000);

                    this.responseCallbacks.push({ resolve: resolveResp, reject: rejectResp, timeout });
                    console.log('Callback registered, queue length now:', this.responseCallbacks.length);
                });

                // Write to characteristic AFTER callback is registered
                await this.characteristics.cmdReq.writeValue(data);

                // Wait for response
                const response = await responsePromise;
                console.log('Response received and processed successfully');

                resolve(response);
            } catch (error) {
                console.error('Command error:', error);
                reject(error);
            }

            // Small delay between commands to prevent GATT conflicts
            await new Promise(r => setTimeout(r, 100));
        }

        this.commandInProgress = false;
        console.log('Command queue processing finished');
    }

    /**
     * Handle response notification
     */
    _handleResponseNotification(event) {
        const decoder = new TextDecoder();
        const value = decoder.decode(event.target.value);
        console.log('Received response:', value.substring(0, 100) + (value.length > 100 ? '...' : ''));

        // Check if this is a chunked response
        const chunkMatch = value.match(/^\[(\d+)\/(\d+)\]/);

        if (chunkMatch) {
            // This is a chunked response
            const chunkNum = parseInt(chunkMatch[1]);
            const totalChunks = parseInt(chunkMatch[2]);
            const chunkData = value.substring(chunkMatch[0].length);

            console.log(`Received chunk ${chunkNum}/${totalChunks}, size=${chunkData.length}`);

            // Initialize chunks array on first chunk
            if (chunkNum === 0) {
                this.responseChunks = [];
                this.expectedChunks = totalChunks;
            }

            // Store chunk
            this.responseChunks[chunkNum] = chunkData;

            // Check if all chunks received
            if (this.responseChunks.filter(c => c !== undefined).length === totalChunks) {
                console.log('All chunks received, reassembling...');
                const fullResponse = this.responseChunks.join('');
                console.log('Reassembled response length:', fullResponse.length);

                // Clear chunks
                this.responseChunks = [];
                this.expectedChunks = 0;

                // Parse and resolve
                try {
                    const response = JSON.parse(fullResponse);

                    if (this.responseCallbacks.length > 0) {
                        console.log('Processing chunked response, callbacks queue length:', this.responseCallbacks.length);
                        const callback = this.responseCallbacks.shift();
                        clearTimeout(callback.timeout);
                        callback.resolve(response);
                        console.log('Chunked response callback resolved successfully');
                    } else {
                        console.warn('Received chunked response but no callback waiting!');
                    }
                } catch (error) {
                    console.error('Failed to parse reassembled response:', error);
                    if (this.responseCallbacks.length > 0) {
                        const callback = this.responseCallbacks.shift();
                        clearTimeout(callback.timeout);
                        callback.reject(error);
                    }
                }
            }
            return;
        }

        // Non-chunked response - process normally
        try {
            const response = JSON.parse(value);

            // Resolve waiting promise (FIFO - first in, first out)
            if (this.responseCallbacks.length > 0) {
                console.log('Processing response, callbacks queue length:', this.responseCallbacks.length);
                const callback = this.responseCallbacks.shift();
                clearTimeout(callback.timeout);
                callback.resolve(response);
                console.log('Response callback resolved successfully');
            } else {
                console.warn('Received response but no callback waiting!');
            }
        } catch (error) {
            console.error('Failed to parse response:', error);
            if (this.responseCallbacks.length > 0) {
                const callback = this.responseCallbacks.shift();
                clearTimeout(callback.timeout);
                callback.reject(error);
            }
        }
    }

    /**
     * Handle device list notification
     */
    _handleDeviceListNotification(event) {
        const decoder = new TextDecoder();
        const value = decoder.decode(event.target.value);
        console.log('Device list update:', value);

        // Trigger custom event for UI update
        window.dispatchEvent(new CustomEvent('ble-devices-update', { detail: value }));
    }

    /**
     * Handle status notification
     */
    _handleStatusNotification(event) {
        const decoder = new TextDecoder();
        const value = decoder.decode(event.target.value);
        console.log('Status update:', value);

        // Trigger custom event for UI update
        window.dispatchEvent(new CustomEvent('ble-status-update', { detail: value }));
    }

    /**
     * Handle disconnection
     */
    _handleDisconnect() {
        console.log('BLE device disconnected');
        window.dispatchEvent(new CustomEvent('ble-disconnected'));
    }
}

// Export for use in other scripts
window.BLEGateway = BLEGateway;

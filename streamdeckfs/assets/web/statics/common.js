/*
#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
*/

// based on https://github.com/websockets/ws/wiki/Websocket-client-implementation-for-auto-reconnect/35b439d43ebedd12394092618b9be431fe4517d3
function WebSocketClient() {
    this.number = 0;
    this.autoReconnectInterval = 5*1000;  // ms
    this._eventListeners = {};
}

WebSocketClient.prototype.open = function(url) {
    this.url = url;
    this.instance = new WebSocket(this.url);

    this._eventListeners['open'] = ()=>{
        if (this.onopen) {
            this.onopen();
        }
    };
    this.instance.addEventListener('open', this._eventListeners['open']);

    this._eventListeners['message'] = (data, flags)=>{
        this.number ++;
        if (this.onmessage) {
            this.onmessage(data, flags, this.number);
        }
    };
    this.instance.addEventListener('message', this._eventListeners['message']);

    this._eventListeners['close'] = (e)=>{
        switch (e.code) {
            case 1000:  // CLOSE_NORMAL
                break;
            default:    // Abnormal closure
                this.reconnect(e);
                break;
        }
        if (this.onclose) {
            this.onclose(e);
        }
    };
    this.instance.addEventListener('close', this._eventListeners['close']);

    this._eventListeners['error'] = (e)=>{
        switch (e.code) {
            case 'ECONNREFUSED':
                this.reconnect(e);
                break;
            default:
                if (this.onerror) {
                    this.onerror(e);
                }
                break;
        }
    };
    this.instance.addEventListener('error', this._eventListeners['error']);
};

WebSocketClient.prototype.send = function(data) {
    this.instance.send(data);
};

WebSocketClient.prototype.removeAllListeners = function() {
    for (const [name, func] of Object.entries(this._eventListeners)) {
        if (func) {
            this.instance.removeEventListener(name, func);
            this._eventListeners[name] = null;
        }
    }
};

WebSocketClient.prototype.reconnect = function(e) {
    this.removeAllListeners();
    var that = this;
    setTimeout(function() {
        that.open(that.url);
    }, this.autoReconnectInterval);
};

WebSocketClient.prototype.sendJson = function(data) {
    this.send(JSON.stringify(data));
};

function connect() {
    var wsUri = (window.location.protocol=='https:'&&'wss://'||'ws://') + window.location.host;
    var connection = new WebSocketClient();
    connection.open(wsUri);
    return connection;
}

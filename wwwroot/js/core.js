let manualMode = false;
let isApplyingBackendState = false;

const RELAY_PRESETS = {
    custom: {
        placeholder: 'relay.your-vps.example:25200',
        hint: 'Point this to your UDP relay host. Hostnames and host:port are supported.'
    },
    template: {
        placeholder: 'relay.example.com:25200',
        hint: 'Template only. Replace it with your VPS host or domain before launching.'
    }
};

let detectedDeviceIP = '';

function send(type, data) {
    window.external.sendMessage(JSON.stringify({ type, ...data }));
}
window.external.receiveMessage(function (msg) {
    try {
    const data = JSON.parse(msg);
    switch (data.type) {
        case 'tosStatus':
            if (data.accepted) {
                send('init', {});
            } else {
                document.getElementById('tosModalBackdrop').style.display = 'flex';
            }
            break;
        case 'status': showStatus(data.text, data.level || 'info'); break;
        case 'gameDir': setGameDir(data.path); break;
        case 'loadUserData': loadUserData(data); break;
        case 'modPacks': populateSelect('modPackSelect', data.packs); break;
        case 'playlists': populateSelect('playlistSelect', data.files); break;
        case 'relayLease': applyRelayLease(data); break;
        case 'relayResolved': onRelayResolved(data); break;
        case 'windowDragStart': if (window.onWindowDragStart) window.onWindowDragStart(data); break;
        case 'mapBg':
            if (data.key && data.data) {
                MAP_BG_CACHE[data.key] = data.data;
                updateChangedSettingsIndicator();
                if (typeof updateInstanceList === 'function') updateInstanceList();
                if (typeof updateSrvOverride === 'function') updateSrvOverride();
                if (typeof updatePickerOptionBgs === 'function') updatePickerOptionBgs(data.key);
            }
            break;
        case 'modeBg':
            if (data.key && data.data) {
                MODE_BG_CACHE[data.key] = data.data;
                if (typeof updateModePickerOptionBgs === 'function') updateModePickerOptionBgs(data.key);
            }
            break;
        case 'aiSetBg':
            if (data.key && data.data) {
                AI_SET_BG_CACHE[data.key] = data.data;
                if (typeof updateAiSetPickerBgs === 'function') updateAiSetPickerBgs(data.key);
            }
            break;
        case 'charIcon':
            if (data.key && data.data) {
                CHAR_ICON_CACHE[data.key] = data.data;
                document.querySelectorAll('img[data-icon-key="' + data.key + '"]').forEach(function(img) {
                    img.src = 'data:image/png;base64,' + data.data;
                    img.classList.remove('char-icon-pending');
                });
            }
            break;
        case 'instanceStarted':
        case 'instanceOutput':
        case 'instanceExited':
        case 'instances':
            handleInstanceMessage(data);
            break;
        case 'serverInfo':
            if (typeof onServerInfoResult === 'function') onServerInfoResult(data);
            break;
        case 'browserList':
            if (typeof onBrowserList === 'function') onBrowserList(data);
            break;
        case 'browserIcon':
            if (typeof onBrowserIcon === 'function') onBrowserIcon(data);
            break;
        case 'detectedInstances':
            if (typeof onDetectedInstances === 'function') onDetectedInstances(data);
            break;
        case 'modLoginResult':
            if (typeof onModLoginResult === 'function') onModLoginResult(data);
            break;
        case 'modRegisterResult':
            if (typeof onModRegisterResult === 'function') onModRegisterResult(data);
            break;
        case 'modLogoutResult':
            if (typeof onModLogoutResult === 'function') onModLogoutResult(data);
            break;
        case 'modGlobalBanResult':
            if (typeof onModGlobalBanResult === 'function') onModGlobalBanResult(data);
            break;
        case 'modGlobalUnbanResult':
            if (typeof onModGlobalUnbanResult === 'function') onModGlobalUnbanResult(data);
            break;
        case 'modGlobalBansList':
            if (typeof onModGlobalBansList === 'function') onModGlobalBansList(data);
            break;
        case 'modBanServerResult':
            if (typeof onModBanServerResult === 'function') onModBanServerResult(data);
            break;
        case 'modUnbanServerResult':
            if (typeof onModUnbanServerResult === 'function') onModUnbanServerResult(data);
            break;
        case 'modBannedServersList':
            if (typeof onModBannedServersList === 'function') onModBannedServersList(data);
            break;
        case 'localBansResult':
            if (typeof onLocalBansResult === 'function') onLocalBansResult(data);
            break;
    }
    } catch (e) {
        console.error('receiveMessage error:', e);
        showStatus('JS Error: ' + e.message, 'error');
    }
});

(function() {
    var dragging = false;
    var startScreenX = 0, startScreenY = 0;
    var startWinX = 0, startWinY = 0;

    var dragEl = document.getElementById('titlebar');
    if (!dragEl) return;

    dragEl.addEventListener('mousedown', function(e) {
        if (e.button !== 0) return;
        if (e.target.closest('.titlebar-controls')) return;
        dragging = true;
        startScreenX = e.screenX;
        startScreenY = e.screenY;
        send('windowDragStart');
        e.preventDefault();
    });

    window.onWindowDragStart = function(data) {
        startWinX = data.windowX;
        startWinY = data.windowY;
    };

    document.addEventListener('mousemove', function(e) {
        if (!dragging) return;
        var dx = e.screenX - startScreenX;
        var dy = e.screenY - startScreenY;
        send('windowDragMove', { x: startWinX + dx, y: startWinY + dy });
    });

    document.addEventListener('mouseup', function() {
        dragging = false;
    });
})();

function populateSelect(id, items) {
    const sel = document.getElementById(id);
    const first = sel.options[0]?.text || '';
    sel.innerHTML = '<option value="">' + first + '</option>';
    (items || []).forEach(p => { const o = document.createElement('option'); o.value = p; o.textContent = p; sel.appendChild(o); });
    if (typeof renderPickerOptions === 'function' && typeof PICKER_REGISTRY !== 'undefined' && PICKER_REGISTRY[id]) {
        renderPickerOptions(id);
        updatePickerTrigger(id);
    }
}

function acceptTos() {
    document.getElementById('tosModalBackdrop').style.display = 'none';
    send('acceptTos', {});
    send('init', {});
}

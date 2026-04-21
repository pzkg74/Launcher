// server browser

var browserServers = [];  // raw list from master server
var browserIconCache = {}; // key -> base64 icon string
var browserIconRequested = {}; // key -> true (pending fetch)
var browserAutoRefreshTimer = null;
var browserPlayerCache = {}; // key -> player count from side-channel
var browserPlayerNames = {}; // key -> array of player names from side-channel
var selectedBrowserKey = null; // key of the last-clicked browser entry

// resolve a raw level id to its friendly name from GAME_DATA
function resolveLevelName(game, levelId) {
    if (!levelId || typeof GAME_DATA === 'undefined') return levelId;
    var gd = GAME_DATA[game];
    if (!gd || !gd.levels) return levelId;
    for (var i = 0; i < gd.levels.length; i++) {
        if (gd.levels[i].id === levelId) return gd.levels[i].name;
    }
    // try partial match (level id might be a substring)
    for (var i = 0; i < gd.levels.length; i++) {
        if (levelId.indexOf(gd.levels[i].id) !== -1 || gd.levels[i].id.indexOf(levelId) !== -1) return gd.levels[i].name;
    }
    return levelId;
}

// resolve a raw mode id to its friendly name from GAME_DATA
function resolveModeName(game, modeId) {
    if (!modeId || typeof GAME_DATA === 'undefined') return modeId;
    var gd = GAME_DATA[game];
    if (!gd || !gd.modes) return modeId;
    for (var i = 0; i < gd.modes.length; i++) {
        if (gd.modes[i].id === modeId) return gd.modes[i].name;
    }
    return modeId;
}

function refreshBrowser() {
    send('fetchBrowser', {});
}

function onBrowserList(data) {
    browserServers = data.servers || [];
    filterBrowserList();
    // lazy-fetch icons and ping for live player counts
    for (var i = 0; i < browserServers.length; i++) {
        var s = browserServers[i];
        var key = s.address + ':' + (s.port || 14638);
        if (s.hasIcon && !browserIconCache[key] && !browserIconRequested[key]) {
            browserIconRequested[key] = true;
            send('fetchBrowserIcon', { key: key });
        }
        // ping side-channel for live player count
        var pingData = { address: key, browserPing: true };
        if (s.relayAddress && s.relayKey) {
            pingData.relayAddress = s.relayAddress;
            pingData.relayKey = s.relayKey;
        }
        send('checkServer', pingData);
    }
}

function onBrowserIcon(data) {
    if (!data.key) return;
    delete browserIconRequested[data.key];
    if (data.icon) {
        if (typeof isValidBase64 === 'function' && isValidBase64(data.icon)) {
            browserIconCache[data.key] = data.icon;
        }
        // re-render the specific icon element if visible
        var el = document.querySelector('.browser-entry-icon[data-key="' + data.key + '"]');
        if (el && browserIconCache[data.key]) {
            el.innerHTML = '<img src="data:image/jpeg;base64,' + browserIconCache[data.key] + '" alt="" draggable="false">';
        }
    }
}

function filterBrowserList() {
    var gameFilter = document.getElementById('browserFilterGame').value;
    var searchTerm = (document.getElementById('browserSearch').value || '').toLowerCase();

    var filtered = browserServers.filter(function (s) {
        if (gameFilter && s.game !== gameFilter) return false;
        if (searchTerm) {
            var haystack = ((s.motd || '') + ' ' + (s.address || '') + ' ' + (s.game || '') + ' ' + (s.level || '') + ' ' + (s.mode || '')).toLowerCase();
            if (haystack.indexOf(searchTerm) === -1) return false;
        }
        return true;
    });

    renderBrowserList(filtered);
}

function renderBrowserList(servers) {
    var container = document.getElementById('browserList');
    var emptyEl = document.getElementById('browserEmpty');

    if (!servers.length) {
        container.querySelectorAll('.browser-entry').forEach(function (e) { e.remove(); });
        if (emptyEl) emptyEl.style.display = 'flex';
        return;
    }

    // detach empty-state element before replacing innerHTML so it survives
    if (emptyEl) emptyEl.remove();
    if (emptyEl) emptyEl.style.display = 'none';

    var html = '';
    for (var i = 0; i < servers.length; i++) {
        var s = servers[i];
        var key = s.address + ':' + (s.port || 14638);
        var motd = s.motd || 'A Cypress Server';
        var liveCount = browserPlayerCache[key];
        var players = liveCount !== undefined ? liveCount : s.players;
        var playerText = (players !== undefined ? players : '?') + '/' + (s.maxPlayers || '?');
        var gameClass = (s.game || 'GW2').toLowerCase();
        var cachedIcon = browserIconCache[key];

        html += '<div class="browser-entry" onclick="onBrowserEntryClick(\'' + escapeAttr(key) + '\')" ondblclick="onBrowserEntryDblClick(\'' + escapeAttr(key) + '\')">';
        html += '<div class="browser-entry-icon" data-key="' + escapeAttr(key) + '">';
        if (cachedIcon) {
            html += '<img src="data:image/jpeg;base64,' + cachedIcon + '" alt="" draggable="false">';
        } else {
            html += '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="1.5" opacity="0.6"><rect x="2" y="2" width="20" height="20" rx="3" ry="3"/><path d="M8 12h8M12 8v8"/></svg>';
        }
        html += '</div>';
        html += '<div class="browser-entry-info">';
        html += '<div class="browser-entry-title">';
        html += '<span class="game-pill game-pill-' + gameClass + '">' + escapeHtml(s.game || 'GW2') + '</span> ';
        html += '<span class="browser-entry-motd motd-rendered">' + (typeof renderMotd === 'function' ? renderMotd(motd) : escapeHtml(motd)) + '</span>';
        if (s.hasPassword) {
            html += ' <svg class="browser-lock-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>';
        }
        html += '</div>';
        html += '<div class="browser-entry-meta">';
        var isRelay = s.relayAddress && s.relayKey;
        html += escapeHtml(isRelay ? 'Relay' : key);
        var tags = [];
        if (s.level) tags.push(resolveLevelName(s.game || 'GW2', s.level));
        if (s.mode) tags.push(resolveModeName(s.game || 'GW2', s.mode));
        if (s.modded) tags.push('Modded');
        if (tags.length) html += ' &middot; ' + escapeHtml(tags.join(' · '));
        html += '</div>';
        if (s.modded && s.modpackUrl) {
            html += '<a class="browser-entry-modpack" href="#" onclick="event.stopPropagation(); openModpackLink(\'' + escapeAttr(s.modpackUrl) + '\'); return false;">';
            html += '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
            html += ' Modpack';
            html += '</a>';
        }
        html += '</div>';
        html += '<div class="browser-entry-players">';
        html += '<span class="browser-player-count">' + playerText + '</span>';
        html += '<span class="browser-player-label">players</span>';
        var names = browserPlayerNames[key];
        html += '<div class="browser-player-tooltip">';
        if (names && names.length) {
            for (var n = 0; n < names.length; n++) {
                html += '<div class="browser-player-tooltip-name">' + escapeHtml(names[n]) + '</div>';
            }
        } else {
            html += '<div class="browser-player-tooltip-name browser-player-tooltip-muted">' + playerText + ' connected</div>';
        }
        html += '</div>';
        html += '</div>';
        html += '</div>';
    }
    container.innerHTML = html;
    // re-append the empty-state element so it's available for future renders
    if (emptyEl) container.appendChild(emptyEl);

    // position fixed tooltips on hover
    container.querySelectorAll('.browser-entry-players').forEach(function (el) {
        el.addEventListener('mouseenter', function () {
            var tip = el.querySelector('.browser-player-tooltip');
            if (!tip) return;
            var rect = el.getBoundingClientRect();
            tip.style.right = (window.innerWidth - rect.right) + 'px';
            tip.style.bottom = (window.innerHeight - rect.top + 4) + 'px';
        });
    });
}

function onBrowserEntryClick(address) {
    selectedBrowserKey = address;
    // find the server entry to check for relay info
    var server = findBrowserServer(address);
    if (server && server.relayAddress && server.relayKey) {
        // relay server - auto-configure relay join
        setRelayMode('join', 'Relay');
        document.getElementById('joinRelayAddress').value = server.relayAddress;
        document.getElementById('joinRelayKey').value = server.relayKey;
        var codeField = document.getElementById('joinRelayCode');
        if (codeField) codeField.value = server.relayCode || '';
        var hintEl = document.getElementById('joinRelayCodeHint');
        if (hintEl) hintEl.textContent = 'Auto-filled from server browser.';
        var infoEl = document.getElementById('joinRelayResolved');
        if (infoEl) {
            infoEl.style.display = '';
            infoEl.innerHTML = '<strong>' + escapeHtml(server.motd || 'Server') + '</strong> <span class="text-muted">(' + escapeHtml(server.game || '?') + ' via relay)</span>';
        }
    } else {
        // direct server
        setRelayMode('join', 'Direct');
        var input = document.getElementById('serverIP');
        if (input) input.value = address.replace(/:.*$/, '');
        // stash the gamePort so joinServer can use it
        var cached = typeof browserPlayerCache !== 'undefined' && typeof serverStatusCache !== 'undefined' ? serverStatusCache[address] : null;
        var server = findBrowserServer(address);
        window._selectedBrowserGamePort = (cached && cached.gamePort) ? cached.gamePort : (server && server.gamePort) ? server.gamePort : 0;
        console.log('[browser] click', address, 'cached:', cached, 'gamePort:', window._selectedBrowserGamePort);
    }
    // if passworded, pop the modal so they don't have to click join first
    if (server && server.hasPassword) {
        showPasswordModal(address);
    }
}

function onBrowserEntryDblClick(address) {
    onBrowserEntryClick(address);
    var server = findBrowserServer(address);
    if (server && server.hasPassword) {
        showPasswordModal(address);
        return;
    }
    if (typeof joinServer === 'function') joinServer();
}

function findBrowserServer(key) {
    for (var i = 0; i < browserServers.length; i++) {
        var s = browserServers[i];
        var k = s.address + ':' + (s.port || 14638);
        if (k === key) return s;
    }
    return null;
}

function startBrowserAutoRefresh() {
    if (browserAutoRefreshTimer) return;
    browserAutoRefreshTimer = setInterval(refreshBrowser, 30000);
}

function stopBrowserAutoRefresh() {
    if (browserAutoRefreshTimer) {
        clearInterval(browserAutoRefreshTimer);
        browserAutoRefreshTimer = null;
    }
}

// password prompt modal for passworded servers
var _pendingPasswordJoinKey = null;

function showPasswordModal(key) {
    _pendingPasswordJoinKey = key;
    var input = document.getElementById('passwordModalInput');
    if (input) input.value = '';
    var backdrop = document.getElementById('passwordModalBackdrop');
    if (backdrop) backdrop.style.display = 'flex';
    if (input) input.focus();
}

function closePasswordModal() {
    _pendingPasswordJoinKey = null;
    var backdrop = document.getElementById('passwordModalBackdrop');
    if (backdrop) backdrop.style.display = 'none';
}

function submitPasswordModal() {
    var pw = document.getElementById('passwordModalInput');
    var field = document.getElementById('serverPassword');
    if (field && pw) field.value = pw.value;
    closePasswordModal();
    if (typeof joinServer === 'function') joinServer();
}

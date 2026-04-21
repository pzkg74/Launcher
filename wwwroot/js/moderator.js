// cypress global moderator panel

var modLoggedIn = false;
var modUsername = '';

function switchModAuthTab(tab) {
    var loginForm = document.getElementById('modLoginForm');
    var regForm = document.getElementById('modRegisterForm');
    var loginBtn = document.getElementById('modLoginTabBtn');
    var regBtn = document.getElementById('modRegTabBtn');

    if (tab === 'login') {
        loginForm.style.display = '';
        regForm.style.display = 'none';
        loginBtn.className = 'btn btn-sm btn-primary';
        regBtn.className = 'btn btn-sm btn-secondary';
    } else {
        loginForm.style.display = 'none';
        regForm.style.display = '';
        loginBtn.className = 'btn btn-sm btn-secondary';
        regBtn.className = 'btn btn-sm btn-primary';
    }
}

function switchModPanel(panel) {
    var bansDiv = document.getElementById('modPanelBans');
    var serversDiv = document.getElementById('modPanelServers');
    var bansBtn = document.getElementById('modPanelBansBtn');
    var serversBtn = document.getElementById('modPanelServersBtn');

    if (panel === 'bans') {
        bansDiv.style.display = '';
        serversDiv.style.display = 'none';
        bansBtn.className = 'btn btn-sm btn-primary';
        serversBtn.className = 'btn btn-sm btn-secondary';
        send('modGetGlobalBans', {});
    } else {
        bansDiv.style.display = 'none';
        serversDiv.style.display = '';
        bansBtn.className = 'btn btn-sm btn-secondary';
        serversBtn.className = 'btn btn-sm btn-primary';
        send('modGetBannedServers', {});
    }
}

function modLogin() {
    var username = document.getElementById('modLoginUsername').value.trim();
    var password = document.getElementById('modLoginPassword').value;
    if (!username || !password) return;
    send('modLogin', { username: username, password: password });
}

function modRegister() {
    var username = document.getElementById('modRegUsername').value.trim();
    var password = document.getElementById('modRegPassword').value;
    var secret = document.getElementById('modRegSecret').value;
    if (!username || !password || !secret) return;
    send('modRegister', { username: username, password: password, secret: secret });
}

function modLogout() {
    send('modLogout', {});
}

function onModLoginResult(data) {
    if (data.ok) {
        modLoggedIn = true;
        modUsername = data.username || '';
        document.getElementById('modAuthSection').style.display = 'none';
        document.getElementById('modPanel').style.display = '';
        document.getElementById('modUsernameDisplay').textContent = modUsername;
        document.getElementById('modAuthStatus').textContent = 'Logged in as ' + modUsername;
        document.getElementById('modLoginError').style.display = 'none';
        // refresh mod tabs on any selected client instance (global mod override)
        if (typeof refreshModTabVisibility === 'function') refreshModTabVisibility();
        // load bans
        send('modGetGlobalBans', {});
    } else {
        var err = document.getElementById('modLoginError');
        err.textContent = data.error || 'Login failed';
        err.style.display = '';
    }
}

function onModRegisterResult(data) {
    if (data.ok) {
        var err = document.getElementById('modRegError');
        err.textContent = 'Registered! You can now login.';
        err.style.display = '';
        err.style.color = 'var(--success, #4caf50)';
        switchModAuthTab('login');
    } else {
        var err = document.getElementById('modRegError');
        err.textContent = data.error || 'Registration failed';
        err.style.display = '';
        err.style.color = 'var(--danger)';
    }
}

function onModLogoutResult() {
    modLoggedIn = false;
    modUsername = '';
    document.getElementById('modAuthSection').style.display = '';
    document.getElementById('modPanel').style.display = 'none';
    document.getElementById('modAuthStatus').textContent = 'Not logged in';
    // refresh mod tabs (might lose access if not local mod)
    if (typeof refreshModTabVisibility === 'function') refreshModTabVisibility();
}

// global bans
function modAddGlobalBan() {
    var hash = document.getElementById('modBanHwid').value.trim();
    var reason = document.getElementById('modBanReason').value.trim();
    if (!hash) return;
    // treat input as a component hash so viral matching applies
    send('modGlobalBan', { hwid: '', reason: reason, components: [hash] });
}

function onModGlobalBanResult(data) {
    if (data.ok) {
        document.getElementById('modBanHwid').value = '';
        document.getElementById('modBanReason').value = '';
        send('modGetGlobalBans', {});
    }
}

function onModGlobalBansList(data) {
    var container = document.getElementById('modGlobalBansList');
    var bans = data.bans || [];
    if (!bans.length) {
        container.innerHTML = '<p class="text-muted">No global bans</p>';
        return;
    }
    var html = '<table class="mod-table"><thead><tr><th>HWID</th><th>Reason</th><th>Banned By</th><th>Date</th><th></th></tr></thead><tbody>';
    for (var i = 0; i < bans.length; i++) {
        var b = bans[i];
        var date = new Date(b.created_at * 1000).toLocaleDateString();
        var shortHwid = b.hwid.length > 16 ? b.hwid.substring(0, 16) + '...' : b.hwid;
        html += '<tr>';
        html += '<td title="' + escapeAttr(b.hwid) + '"><code>' + escapeHtml(shortHwid) + '</code></td>';
        html += '<td>' + escapeHtml(b.reason || '-') + '</td>';
        html += '<td>' + escapeHtml(b.banned_by) + '</td>';
        html += '<td>' + escapeHtml(date) + '</td>';
        html += '<td><button class="btn btn-sm btn-danger" onclick="modRemoveGlobalBan(' + b.id + ')">Unban</button></td>';
        html += '</tr>';
    }
    html += '</tbody></table>';
    container.innerHTML = html;
}

function modRemoveGlobalBan(id) {
    send('modGlobalUnban', { id: id });
}

function onModGlobalUnbanResult(data) {
    if (data.ok) send('modGetGlobalBans', {});
}

// server bans
function modBanServer() {
    var ip = document.getElementById('modBanServerIp').value.trim();
    var reason = document.getElementById('modBanServerReason').value.trim();
    if (!ip) return;
    send('modBanServer', { ip: ip, reason: reason });
}

function onModBanServerResult(data) {
    if (data.ok) {
        document.getElementById('modBanServerIp').value = '';
        document.getElementById('modBanServerReason').value = '';
        send('modGetBannedServers', {});
    }
}

function onModBannedServersList(data) {
    var container = document.getElementById('modBannedServersList');
    var servers = data.servers || [];
    if (!servers.length) {
        container.innerHTML = '<p class="text-muted">No banned servers</p>';
        return;
    }
    var html = '<table class="mod-table"><thead><tr><th>IP</th><th>Reason</th><th>Banned By</th><th>Date</th><th></th></tr></thead><tbody>';
    for (var i = 0; i < servers.length; i++) {
        var s = servers[i];
        var date = new Date(s.created_at * 1000).toLocaleDateString();
        html += '<tr>';
        html += '<td><code>' + escapeHtml(s.ip) + '</code></td>';
        html += '<td>' + escapeHtml(s.reason || '-') + '</td>';
        html += '<td>' + escapeHtml(s.banned_by) + '</td>';
        html += '<td>' + escapeHtml(date) + '</td>';
        html += '<td><button class="btn btn-sm btn-danger" onclick="modUnbanServer(\'' + escapeAttr(s.ip) + '\')">Unban</button></td>';
        html += '</tr>';
    }
    html += '</tbody></table>';
    container.innerHTML = html;
}

function modUnbanServer(ip) {
    send('modUnbanServer', { ip: ip });
}

function onModUnbanServerResult(data) {
    if (data.ok) send('modGetBannedServers', {});
}

// MOTD formatting system
// markup: [b], [i], [u], [c=#hex], [c=name], [g=#a,#b], [g=#a,#b,#c],
//         [pulse], [flash], [wave], [scroll], [center], [right], \n

var MOTD_MAX_RAW = 256;
var MOTD_MAX_LINES = 4;

var MOTD_COLOR_PRESETS = {
    red: '#FF5555', green: '#55FF55', blue: '#5555FF', yellow: '#FFFF55',
    gold: '#FFAA00', aqua: '#55FFFF', pink: '#FF55FF', orange: '#FF8C00',
    white: '#FFFFFF', gray: '#AAAAAA', dark_red: '#AA0000', dark_green: '#00AA00',
    dark_blue: '#0000AA', dark_aqua: '#00AAAA', dark_purple: '#AA00AA', black: '#000000'
};

// parser: raw markup → token tree

function parseMotd(raw) {
    if (!raw) return [];
    var lines = raw.split('\n').slice(0, MOTD_MAX_LINES);
    var result = [];
    for (var li = 0; li < lines.length; li++) {
        result.push(parseMotdLine(lines[li]));
    }
    return result;
}

function parseMotdLine(line) {
    var align = 'left';
    // check for alignment wrapper
    var m;
    if ((m = line.match(/^\[center\](.*?)(?:\[\/center\])?$/s))) {
        align = 'center'; line = m[1];
    } else if ((m = line.match(/^\[right\](.*?)(?:\[\/right\])?$/s))) {
        align = 'right'; line = m[1];
    }
    return { align: align, nodes: parseMotdNodes(line) };
}

function parseMotdNodes(str) {
    var nodes = [];
    var i = 0;
    while (i < str.length) {
        if (str[i] === '[') {
            var tag = parseMotdTag(str, i);
            if (tag) {
                if (i > tag._start && nodes.length === 0) {
                    // shouldn't happen since _start === i
                }
                nodes.push(tag.node);
                i = tag.end;
                continue;
            }
        }
        // plain text
        var next = str.indexOf('[', i);
        if (next === -1) next = str.length;
        var text = str.substring(i, next);
        if (text) nodes.push({ type: 'text', text: text });
        i = next;
    }
    return nodes;
}

function parseMotdTag(str, pos) {
    var rest = str.substring(pos);
    var patterns = [
        // bold
        { re: /^\[b\]([\s\S]*?)\[\/b\]/, make: function (m) { return { type: 'bold', children: parseMotdNodes(m[1]) }; } },
        // italic
        { re: /^\[i\]([\s\S]*?)\[\/i\]/, make: function (m) { return { type: 'italic', children: parseMotdNodes(m[1]) }; } },
        // underline
        { re: /^\[u\]([\s\S]*?)\[\/u\]/, make: function (m) { return { type: 'underline', children: parseMotdNodes(m[1]) }; } },
        // color (hex or named)
        { re: /^\[c=(#[0-9A-Fa-f]{3,8}|[a-z_]+)\]([\s\S]*?)\[\/c\]/, make: function (m) {
            var color = m[1].startsWith('#') ? m[1] : (MOTD_COLOR_PRESETS[m[1]] || '#FFFFFF');
            return { type: 'color', color: color, children: parseMotdNodes(m[2]) };
        }},
        // gradient (2 or 3 stops)
        { re: /^\[g=(#[0-9A-Fa-f]{3,8}),(#[0-9A-Fa-f]{3,8})(?:,(#[0-9A-Fa-f]{3,8}))?\]([\s\S]*?)\[\/g\]/, make: function (m) {
            var stops = [m[1], m[2]];
            if (m[3]) stops.push(m[3]);
            var lastGroup = m[3] ? m[4] : m[4];
            return { type: 'gradient', stops: stops, children: parseMotdNodes(lastGroup) };
        }},
        // pulse
        { re: /^\[pulse\]([\s\S]*?)\[\/pulse\]/, make: function (m) { return { type: 'anim', anim: 'pulse', children: parseMotdNodes(m[1]) }; } },
        // flash
        { re: /^\[flash\]([\s\S]*?)\[\/flash\]/, make: function (m) { return { type: 'anim', anim: 'flash', children: parseMotdNodes(m[1]) }; } },
        // wave
        { re: /^\[wave\]([\s\S]*?)\[\/wave\]/, make: function (m) { return { type: 'anim', anim: 'wave', children: parseMotdNodes(m[1]) }; } },
        // scroll (gradient scroll)
        { re: /^\[scroll=(#[0-9A-Fa-f]{3,8}),(#[0-9A-Fa-f]{3,8})(?:,(#[0-9A-Fa-f]{3,8}))?\]([\s\S]*?)\[\/scroll\]/, make: function (m) {
            var stops = [m[1], m[2]];
            if (m[3]) stops.push(m[3]);
            return { type: 'scroll', stops: stops, children: parseMotdNodes(m[3] ? m[4] : m[4]) };
        }},
    ];

    for (var p = 0; p < patterns.length; p++) {
        var match = rest.match(patterns[p].re);
        if (match) {
            return { _start: pos, node: patterns[p].make(match), end: pos + match[0].length };
        }
    }
    return null;
}

// renderer: token tree → safe HTML

function renderMotd(raw) {
    if (!raw || typeof raw !== 'string') return escapeHtml(raw || '');
    // if no markup tags present, fast path
    if (raw.indexOf('[') === -1 && raw.indexOf('\n') === -1) return escapeHtml(raw);
    var parsed = parseMotd(raw);
    var html = '';
    for (var i = 0; i < parsed.length; i++) {
        var line = parsed[i];
        var cls = 'motd-line';
        if (line.align === 'center') cls += ' motd-center';
        else if (line.align === 'right') cls += ' motd-right';
        html += '<span class="' + cls + '">' + renderMotdNodes(line.nodes) + '</span>';
    }
    return html;
}

function renderMotdNodes(nodes) {
    var html = '';
    for (var i = 0; i < nodes.length; i++) {
        html += renderMotdNode(nodes[i]);
    }
    return html;
}

function renderMotdNode(node) {
    if (node.type === 'text') return escapeHtml(node.text);
    var inner = renderMotdNodes(node.children || []);
    switch (node.type) {
        case 'bold':
            return '<span class="motd-bold">' + inner + '</span>';
        case 'italic':
            return '<span class="motd-italic">' + inner + '</span>';
        case 'underline':
            return '<span class="motd-underline">' + inner + '</span>';
        case 'color':
            return '<span style="color:' + sanitizeCSSColor(node.color) + '">' + inner + '</span>';
        case 'gradient':
            var grad = 'linear-gradient(90deg,' + node.stops.map(sanitizeCSSColor).join(',') + ')';
            return '<span class="motd-gradient" style="background-image:' + grad + '">' + inner + '</span>';
        case 'anim':
            if (node.anim === 'wave') return renderWaveNode(node);
            return '<span class="motd-' + node.anim + '">' + inner + '</span>';
        case 'scroll':
            var sgrad = 'linear-gradient(90deg,' + node.stops.map(sanitizeCSSColor).join(',') + ',' + sanitizeCSSColor(node.stops[0]) + ')';
            return '<span class="motd-scroll" style="background-image:' + sgrad + '">' + inner + '</span>';
        default:
            return inner;
    }
}

function renderWaveNode(node) {
    // each character gets a staggered delay
    var text = getPlainText(node);
    var html = '<span class="motd-wave">';
    for (var i = 0; i < text.length; i++) {
        var delay = (i * 0.08).toFixed(2);
        var ch = text[i] === ' ' ? '&nbsp;' : escapeHtml(text[i]);
        html += '<span class="motd-char" style="animation-delay:' + delay + 's">' + ch + '</span>';
    }
    html += '</span>';
    return html;
}

function getPlainText(node) {
    if (node.type === 'text') return node.text;
    var t = '';
    if (node.children) {
        for (var i = 0; i < node.children.length; i++) t += getPlainText(node.children[i]);
    }
    return t;
}

function sanitizeCSSColor(c) {
    if (!c) return '#FFFFFF';
    // only allow hex colors
    if (/^#[0-9A-Fa-f]{3,8}$/.test(c)) return c;
    return '#FFFFFF';
}

// plain text extraction (for char counting)

function motdPlainLength(raw) {
    if (!raw) return 0;
    // strip all tags
    return raw.replace(/\[\/?\w+(?:=[^\]]+)?\]/g, '').replace(/\n/g, '').length;
}

// MOTD editor

var motdEditorMode = 'markup'; // 'markup'

function initMotdEditor() {
    var wrap = document.getElementById('motdEditorWrap');
    if (!wrap) return;
    var textarea = document.getElementById('motdTextarea');
    if (!textarea) return;

    textarea.addEventListener('input', onMotdEditorInput);
    updateMotdPreview();
    updateMotdCharCount();
}

function onMotdEditorInput() {
    updateMotdPreview();
    updateMotdCharCount();
}

function updateMotdPreview() {
    var textarea = document.getElementById('motdTextarea');
    var preview = document.getElementById('motdPreview');
    if (!textarea || !preview) return;
    var raw = textarea.value;
    preview.innerHTML = '<div class="motd-rendered">' + renderMotd(raw) + '</div>';
}

function updateMotdCharCount() {
    var textarea = document.getElementById('motdTextarea');
    var counter = document.getElementById('motdCharCount');
    if (!textarea || !counter) return;
    var raw = textarea.value;
    var plain = motdPlainLength(raw);
    var lineCount = (raw.match(/\n/g) || []).length + 1;
    counter.textContent = plain + ' chars · ' + Math.min(lineCount, MOTD_MAX_LINES) + '/' + MOTD_MAX_LINES + ' lines';
    counter.classList.toggle('over', raw.length > MOTD_MAX_RAW);
}

function getMotdRaw() {
    var textarea = document.getElementById('motdTextarea');
    return textarea ? textarea.value : '';
}

function setMotdRaw(val) {
    var textarea = document.getElementById('motdTextarea');
    if (textarea) {
        textarea.value = val || '';
        updateMotdPreview();
        updateMotdCharCount();
    }
}

// toolbar actions

function motdWrapSelection(before, after) {
    var textarea = document.getElementById('motdTextarea');
    if (!textarea) return;
    var start = textarea.selectionStart;
    var end = textarea.selectionEnd;
    var text = textarea.value;
    var selected = text.substring(start, end) || 'text';
    textarea.value = text.substring(0, start) + before + selected + after + text.substring(end);
    textarea.selectionStart = start + before.length;
    textarea.selectionEnd = start + before.length + selected.length;
    textarea.focus();
    onMotdEditorInput();
}

function motdInsertBold() { motdWrapSelection('[b]', '[/b]'); }
function motdInsertItalic() { motdWrapSelection('[i]', '[/i]'); }
function motdInsertUnderline() { motdWrapSelection('[u]', '[/u]'); }

function motdInsertColor(color) {
    if (!color) return;
    motdWrapSelection('[c=' + color + ']', '[/c]');
    closeMotdColorPresets();
}

function motdInsertGradient() {
    var c1 = document.getElementById('motdGradColor1');
    var c2 = document.getElementById('motdGradColor2');
    var c3 = document.getElementById('motdGradColor3');
    if (!c1 || !c2) return;
    var stops = c1.value + ',' + c2.value;
    if (c3 && c3.value && c3.value !== '#000000') stops += ',' + c3.value;
    motdWrapSelection('[g=' + stops + ']', '[/g]');
    closeMotdGradientPicker();
}

function motdInsertAnim(type) {
    if (type === 'scroll') {
        motdWrapSelection('[scroll=#FF5555,#5555FF]', '[/scroll]');
    } else {
        motdWrapSelection('[' + type + ']', '[/' + type + ']');
    }
}

function motdInsertNewline() {
    var textarea = document.getElementById('motdTextarea');
    if (!textarea) return;
    var pos = textarea.selectionStart;
    textarea.value = textarea.value.substring(0, pos) + '\n' + textarea.value.substring(pos);
    textarea.selectionStart = textarea.selectionEnd = pos + 1;
    textarea.focus();
    onMotdEditorInput();
}

function motdInsertAlign(align) {
    motdWrapSelection('[' + align + ']', '[/' + align + ']');
}

// color presets panel
function toggleMotdColorPresets() {
    var el = document.getElementById('motdColorPresets');
    if (el) el.classList.toggle('open');
}

function closeMotdColorPresets() {
    var el = document.getElementById('motdColorPresets');
    if (el) el.classList.remove('open');
}

function onMotdColorPickerChange(input) {
    motdInsertColor(input.value);
    closeMotdColorPresets();
}

// gradient picker panel
function toggleMotdGradientPicker() {
    var el = document.getElementById('motdGradientPicker');
    if (el) el.classList.toggle('open');
    updateMotdGradientPreview();
}

function closeMotdGradientPicker() {
    var el = document.getElementById('motdGradientPicker');
    if (el) el.classList.remove('open');
}

function updateMotdGradientPreview() {
    var c1 = document.getElementById('motdGradColor1');
    var c2 = document.getElementById('motdGradColor2');
    var c3 = document.getElementById('motdGradColor3');
    var bar = document.getElementById('motdGradPreviewBar');
    if (!c1 || !c2 || !bar) return;
    var stops = c1.value + ', ' + c2.value;
    if (c3 && c3.value && c3.value !== '#000000') stops += ', ' + c3.value;
    bar.style.background = 'linear-gradient(90deg, ' + stops + ')';
}

// build color preset swatches
function buildMotdColorPresets() {
    var container = document.getElementById('motdColorPresets');
    if (!container || container.querySelector('.motd-color-preset[title="red"]')) return;
    var keys = Object.keys(MOTD_COLOR_PRESETS);
    for (var i = 0; i < keys.length; i++) {
        var swatch = document.createElement('div');
        swatch.className = 'motd-color-preset';
        swatch.style.background = MOTD_COLOR_PRESETS[keys[i]];
        swatch.title = keys[i];
        swatch.setAttribute('onclick', 'motdInsertColor("' + keys[i] + '")');
        container.appendChild(swatch);
    }
}

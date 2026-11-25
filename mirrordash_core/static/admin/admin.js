// The admin page's script (loaded by templates/admin.html)
// Logged in = the server's session cookie (HttpOnly, so this script never sees it or the password).
// Earlier versions kept the password itself in localStorage: remove it.
let loggedIn = false;
try { localStorage.removeItem('mirrordash_api_key'); } catch (_) { /* storage blocked: nothing kept there */ }
let authPromiseResolve;

// A floating message. Errors stay 8 s, others 4 s; stayMs = 0 keeps it until hideGlobal().
let globalTimer = null;
function showGlobal(msg, type = 'info', stayMs = type === 'error' ? 8000 : 4000) {
    const globalStatus = document.getElementById('global-status');
    if (!globalStatus) return;
    globalStatus.textContent = msg;
    globalStatus.className = `alert toast alert--${type}`;
    globalStatus.hidden = false;
    clearTimeout(globalTimer);
    if (stayMs) globalTimer = setTimeout(() => { globalStatus.hidden = true; }, stayMs);
}

function hideGlobal() {
    clearTimeout(globalTimer);
    document.getElementById('global-status').hidden = true;
}

function showAuthError(msg) {
    const authError = document.getElementById('auth-error');
    if (authError) {
        authError.textContent = msg;
        authError.style.display = 'block';
    }
}

function showConfirm(title, desc, confirmText = 'Confirm', isDestructive = false) {
    const overlay = document.getElementById('confirm-overlay');
    const titleEl = document.getElementById('confirm-title');
    const descEl = document.getElementById('confirm-desc');
    const okBtn = document.getElementById('confirm-ok-btn');
    const cancelBtn = document.getElementById('confirm-cancel-btn');
    const icon = document.getElementById('confirm-icon');

    titleEl.textContent = title;
    descEl.textContent = desc;
    okBtn.textContent = confirmText;
    
    if (isDestructive) {
        okBtn.className = 'auth-btn corrupt-btn';
        icon.className = 'auth-icon corrupt';
        icon.innerHTML = '<i data-lucide="shield-alert"></i>';
    } else {
        okBtn.className = 'auth-btn';
        icon.className = 'auth-icon';
        icon.innerHTML = '<i data-lucide="help-circle"></i>';
    }

    if (window.lucide) lucide.createIcons({ root: icon });

    overlay.classList.add('open');

    return new Promise((resolve) => {
        const handleOk = () => {
            overlay.classList.remove('open');
            cleanup();
            resolve(true);
        };
        const handleCancel = () => {
            overlay.classList.remove('open');
            cleanup();
            resolve(false);
        };
        const cleanup = () => {
            okBtn.removeEventListener('click', handleOk);
            cancelBtn.removeEventListener('click', handleCancel);
        };
        okBtn.addEventListener('click', handleOk);
        cancelBtn.addEventListener('click', handleCancel);
    });
}

function setPasswordVisible(btn, visible) {
    document.getElementById(btn.dataset.reveal).type = visible ? 'text' : 'password';
    btn.textContent = visible ? 'Hide' : 'Show';
    btn.setAttribute('aria-pressed', visible);
}

// Delegated, so it also works for password fields HTMX loads later
document.addEventListener('click', (e) => {
    const btn = e.target.closest('.pw-reveal');
    if (!btn) return;
    const input = document.getElementById(btn.dataset.reveal);
    setPasswordVisible(btn, input.type === 'password');
    input.focus();
});

// What each step of the password dialog shows: its text, its parts (the ids auth-<part>-group)
// and its button
const AUTH_MODES = {
    login: {
        title: 'Enter Password',
        desc: 'Enter your MirrorDash admin password to unlock the dashboard.',
        parts: ['password', 'forgot'],
        button: 'Unlock',
    },
    setup: {
        title: 'Welcome to MirrorDash',
        desc: 'Create an admin password to secure your mirror (at least 8 characters).',
        parts: ['password'],
        button: 'Create Password',
    },
    recover: {
        title: 'Reset Password',
        desc: 'Enter the recovery code you saved when you set up the mirror, and choose a new password.',
        parts: ['code', 'new-password', 'lost'],
        button: 'Set New Password',
    },
    saved: {
        title: 'Save Your Recovery Code',
        desc: 'If you forget the password, this code sets a new one. It is shown only now: save it in your password manager or write it down.',
        parts: ['saved-code'],
        button: "I've Saved It",
    },
};
const AUTH_PARTS = ['password', 'code', 'new-password', 'saved-code', 'forgot', 'lost'];

// Switch the open dialog to another step; the caller still waits for the same answer
function setAuthMode(mode, desc) {
    const m = AUTH_MODES[mode];
    document.getElementById('auth-overlay').setAttribute('data-mode', mode);
    document.getElementById('auth-title').textContent = m.title;
    document.getElementById('auth-desc').textContent = desc || m.desc;
    AUTH_PARTS.forEach(part => {
        document.getElementById(`auth-${part}-group`).style.display = m.parts.includes(part) ? 'block' : 'none';
    });
    const submitBtn = document.getElementById('auth-submit-btn');
    submitBtn.disabled = false;
    submitBtn.textContent = m.button;
    const authError = document.getElementById('auth-error');
    authError.textContent = '';
    authError.style.display = 'none';
    // Every step starts with empty fields and passwords hidden again
    ['auth-password-input', 'auth-code-input', 'auth-new-password-input'].forEach(id => {
        document.getElementById(id).value = '';
    });
    document.querySelectorAll('#auth-overlay .pw-reveal').forEach(btn => setPasswordVisible(btn, false));
    // Lets password managers save a new password at setup and fill it at login
    document.getElementById('auth-password-input').autocomplete = mode === 'setup' ? 'new-password' : 'current-password';
}

function showAuthModal(mode, desc) {
    setAuthMode(mode, desc);
    document.getElementById('auth-overlay').classList.add('open');
    return new Promise((resolve) => {
        authPromiseResolve = resolve;
    });
}

// Logged in with a new password (the answer set the session cookie): show its recovery code once
function acceptNewPassword(recoveryCode) {
    loggedIn = true;
    setAuthMode('saved');
    document.getElementById('auth-saved-code-input').value = recoveryCode;
}

async function postAuth(url, body) {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, data };
}

async function handleAuthSubmit(event) {
    event.preventDefault();
    const overlay = document.getElementById('auth-overlay');
    const mode = overlay.getAttribute('data-mode');
    const submitBtn = document.getElementById('auth-submit-btn');
    const password = document.getElementById('auth-password-input').value;
    const newPassword = document.getElementById('auth-new-password-input').value;
    const code = document.getElementById('auth-code-input').value;

    if (mode === 'saved') {
        overlay.classList.remove('open');
        showGlobal('Password set.', 'success');
        authPromiseResolve(true);
        return;
    }
    if (mode !== 'login' && (mode === 'setup' ? password : newPassword).length < 8) {  // MIN_PASSWORD in features/auth/routes.py
        showAuthError('Password must be at least 8 characters.');
        return;
    }

    submitBtn.disabled = true;
    submitBtn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Processing…';
    try {
        if (mode === 'login') {
            const { ok, data } = await postAuth('/admin/auth/login', { password });
            if (ok) {
                loggedIn = true;
                overlay.classList.remove('open');
                authPromiseResolve(true);
            } else {
                showAuthError(data.detail || 'Invalid password. Please try again.');  // incl. "Too many wrong tries"
            }
        } else if (mode === 'setup') {
            const { ok, data } = await postAuth('/admin/auth/setup', { password });
            if (ok) return acceptNewPassword(data.recovery_code);
            showAuthError('Failed to set password: ' + (data.detail || 'Unknown error'));
        } else if (mode === 'recover') {
            const { ok, data } = await postAuth('/admin/auth/recover', { code, new_password: newPassword });
            if (ok) return acceptNewPassword(data.recovery_code);
            showAuthError(data.detail || 'Unknown error');
        }
    } catch (e) {
        showAuthError('Could not reach the mirror: ' + e.message);
    }
    submitBtn.disabled = false;
    submitBtn.textContent = AUTH_MODES[mode].button;
}

// Settings → Admin Password. The current password is asked again, so an unlocked page alone can't change it
async function changeAdminPassword(event) {
    event.preventDefault();
    const form = event.target;
    const button = form.querySelector('button[type=submit]');
    const current = document.getElementById('admin-current-password');
    const next = document.getElementById('admin-new-password');
    button.disabled = true;
    try {
        const res = await fetch('/admin/auth/change-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ current_password: current.value, new_password: next.value }),
        });
        if (res.ok) {
            form.reset();
            showGlobal('Password changed. Other devices are logged out.', 'success');
        } else {
            const data = await res.json().catch(() => ({}));
            showGlobal(data.detail || 'Could not change the password.', 'error');
        }
    } catch (e) {
        showGlobal('Could not reach the mirror: ' + e.message, 'error');
    }
    button.disabled = false;
}

// Settings → Admin Password → Log Out: ends this browser's session
async function logoutAdmin() {
    try { await fetch('/admin/auth/logout', { method: 'POST' }); } catch (_) { /* reload asks to log in either way */ }
    window.location.reload();
}

async function checkAuthStatus() {
    try {
        const res = await fetch('/admin/auth/status');
        if (!res.ok) throw new Error('Failed to fetch auth status');
        const data = await res.json();

        if (data.auth_corrupt) {
            return await showAuthModal('recover', 'The admin password settings are damaged. Enter your recovery code and choose a new password.');
        }

        if (data.setup_required) {
            return await showAuthModal('setup');
        }
        if (!data.logged_in) {
            return await showAuthModal('login');
        }
        loggedIn = true;
        return true;
    } catch (err) {
        showGlobal('Auth check failed: ' + err.message, 'error');
        return false;
    }
}

const TAB_HEADINGS = {
    dashboard: ['Dashboard', 'How the mirror is doing right now.'],
    config: ['Settings', 'Language, location, updates, Wi-Fi and the admin password.'],
    modules: ['Modules', 'Choose what the mirror shows, and where.'],
    logs: ['Logs', 'What the mirror has been doing. Useful when something goes wrong.'],
    backup: ['Backup', 'Save your setup to a file, or restore it.'],
    system: ['Hardware', 'Screen, sound and things connected to the Pi.'],
    power: ['Power', 'When the screen is on, and restarting or shutting down.'],
};

function setActiveTab(btn) {
    document.querySelectorAll('.nav-item').forEach(b => {
        const active = b === btn;
        b.classList.toggle('active', active);
        b.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    const tabName = btn.id.replace('page-tab-', '');
    
    const [headline, desc] = TAB_HEADINGS[tabName] || TAB_HEADINGS.dashboard;
    document.getElementById('tab-headline').innerText = headline;
    document.getElementById('tab-description').innerText = desc;

    window.location.hash = tabName;
}

function handleHashChange() {
    const hash = window.location.hash;
    const tabBtn = document.getElementById(hash ? 'page-tab-' + hash.replace('#', '') : 'page-tab-dashboard');
    const empty = !document.getElementById('tab-content').children.length;
    if (tabBtn && (empty || !tabBtn.classList.contains('active'))) {
        tabBtn.click();
    }
}
window.addEventListener('hashchange', handleHashChange);

async function triggerRestart() {
    const confirmed = await showConfirm(
        "Restart MirrorDash?",
        "The screen goes blank for a few seconds while MirrorDash starts again.",
        "Restart",
        true
    );
    if (!confirmed) return;
    const btn = document.getElementById('restart-btn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Restarting…';
    let bootId = null;
    try {
        const res = await fetch('/admin/restart', { method: 'POST' });
        if (!res.ok) throw new Error();
        bootId = (await res.json()).boot_id;
    } catch (_) {
        showGlobal('Restart request failed.', 'error');
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-sync-alt" aria-hidden="true"></i> Restart MirrorDash';
        return;
    }

    window.pollRestartAndReload({
        bootId,
        successMsg: 'MirrorDash restarted.',
        title: 'Restarting MirrorDash',
        message: 'Waiting for the backend server to come back online...',
        onFinish: () => {
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-sync-alt" aria-hidden="true"></i> Restart MirrorDash';
        }
    });
}

window.openConfigSheet = function(name, title, iconHtml) {
    document.getElementById('profileTitle').innerText = title;
    document.getElementById('profileDesc').innerText = '';  // the position, set by sheet.js when the form arrives
    // The schema's root "help": an info button after the title (help.js); the module lists show the short text only
    const help = (document.getElementById('module-card-' + name) || {dataset: {}}).dataset.help || '';
    const helpButton = document.getElementById('profileHelp');
    helpButton.hidden = !help;
    helpButton.dataset.help = help;
    helpButton.dataset.helpTitle = title;
    helpButton.setAttribute('aria-label', 'More about ' + title);

    const profileIcon = document.getElementById('profileIcon');
    profileIcon.innerHTML = iconHtml || '<i class="fas fa-puzzle-piece"></i>';

    // No screenshot here: the preview shows the module itself (ARCHITECTURE #43); its README stays a link
    const card = document.getElementById('module-card-' + name);
    const readme = document.getElementById('profileReadme');
    readme.hidden = !(card && card.dataset.repo);
    if (!readme.hidden) readme.href = card.dataset.repo + '#readme';

    document.getElementById('configOverlay').classList.add('open');
};

// Closing asks first when there are edits not saved (sheet_guard.js); force skips the question
window.closeConfigSheet = function(force) {
    if (force !== true && !window.sheetGuard(() => window.closeConfigSheet(true))) return;
    document.getElementById('configOverlay').classList.remove('open');
    const fields = document.getElementById('config-fields-global');
    if (fields) fields.innerHTML = '';
    document.dispatchEvent(new Event('sheet-closed'));
    clearTimeout(previewTimer);
    clearTimeout(previewPoll);
    previewRun++;  // an answer still on its way is dropped
    document.getElementById('modulePreview').srcdoc = '';
};

// Escape closes a module's settings, as any modal (not while another dialog, e.g. a confirm, is on top)
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && document.getElementById('configOverlay').classList.contains('open')
        && !document.querySelector('.modal-overlay[style*="display: flex"]')) window.closeConfigSheet();
});

// The drawer's preview (ARCHITECTURE #43): the unsaved form, posted as Save would post it, rendered by the
// module from its sample answers. Without those, the card the mirror shows now, looked at again every 10 s.
// srcdoc: the page is of no site (the iframe's sandbox), so the module's HTML can't reach the admin page.
let previewTimer = null, previewPoll = null, previewRun = 0;
const PREVIEW_CAPTIONS = { sample: 'Preview, with sample data', saved: 'On the mirror now' };

async function refreshModulePreview() {
    const form = document.querySelector('#config-fields-global .module-config-form');
    const frame = document.getElementById('modulePreview');
    if (!form || !frame) return;
    const url = form.getAttribute('hx-post').replace(/\/config\/([^/?]+)\/save/, '/preview/$1');
    const run = ++previewRun;
    clearTimeout(previewPoll);
    try {
        const response = await fetch(url, { method: 'POST', body: new FormData(form) });
        const page = await response.text();
        if (run !== previewRun) return;  // a newer one is on its way
        const source = response.ok ? response.headers.get('X-Preview') || 'none' : 'none';
        frame.srcdoc = response.ok ? page : '';
        document.getElementById('modulePreviewCaption').textContent = PREVIEW_CAPTIONS[source] || 'Preview';
        if (source !== 'sample') previewPoll = setTimeout(refreshModulePreview, 10000);
    } catch (e) {
        if (run === previewRun) previewPoll = setTimeout(refreshModulePreview, 10000);  // offline: the last one stays
    }
}

function schedulePreview(event) {
    if (!(event.target.closest && event.target.closest('#config-fields-global .module-config-form'))) return;
    clearTimeout(previewTimer);
    previewTimer = setTimeout(refreshModulePreview, 600);
}
document.addEventListener('input', schedulePreview);
document.addEventListener('change', schedulePreview);
// An icon setting (forms.py _icon): the page's Lucide icons as suggestions, and the one typed drawn beside it
// (an unknown name gets a question mark). Lucide's names are PascalCase; the mirror's data-lucide kebab-case.
const pascalIcon = (name) => name.trim().replace(/(^|-)([a-z0-9])/g, (_, __, c) => c.toUpperCase());
function drawIcon(input) {
    const preview = input.parentElement.querySelector('.icon-preview');
    const icon = window.lucide && input.value.trim() && lucide.icons[pascalIcon(input.value)];
    preview.classList.toggle('unknown', Boolean(input.value.trim()) && !icon);
    preview.replaceChildren(...(icon ? [lucide.createElement(icon)] : []));
}
function setUpIconInputs(root) {
    const inputs = root.querySelectorAll('[data-icon-input]');
    if (!inputs.length || !window.lucide) return;
    if (!document.getElementById('lucide-icons')) {
        const list = document.createElement('datalist');
        list.id = 'lucide-icons';
        list.append(...Object.keys(lucide.icons).map((name) => new Option('', name.replace(/([a-z0-9])([A-Z])/g, '$1-$2').toLowerCase())));
        document.body.append(list);
    }
    inputs.forEach(drawIcon);
}
document.addEventListener('input', (event) => { if (event.target.matches?.('[data-icon-input]')) drawIcon(event.target); });
// A setting the browser finds breaks its schema's rules (schema_form.html rules, ARCHITECTURE #49) may sit in a
// folded list item or section, or be hidden by show_if (ARCHITECTURE #50): open and show them, or the browser
// can't point at it and the save just doesn't happen. (The module sheet doesn't use the browser's check:
// sheet_errors.js shows the server's answer, with the same reveal.)
window.revealField = (field) => {
    for (let fold = field.closest('details'); fold; fold = fold.parentElement.closest('details')) fold.open = true;
    for (let hidden = field.closest('.show-if[hidden]'); hidden; hidden = hidden.parentElement.closest('.show-if[hidden]')) hidden.hidden = false;
};
document.addEventListener('invalid', (event) => window.revealField(event.target), true);
// The whole settings form, or a new item in one of its lists
document.body.addEventListener('htmx:afterSwap', (event) => {
    if (!event.target.closest?.('#config-fields-global')) return;
    setUpIconInputs(event.target);
    event.target.querySelectorAll('[data-files-field]').forEach(setUpFilesField);
});

// A module's files setting ("format": "files", features/modules/files.py, ARCHITECTURE #44). An image is
// shrunk here on the phone before it's sent: to the screen's longest side, as a JPEG (the canvas drops its
// EXIF, the GPS position too, and the rotation is applied), under the module route's 4 MB out with room
// to spare. A tenth of the data over the Pi's Wi-Fi, and no 12-megapixel decode on the Pi.
// With it a 240 px copy for the field's thumbnails (~15 kB), so a long list doesn't fetch every photo.
async function shrinkImage(file, maxSide) {
    const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
    const draw = (side) => {
        const scale = Math.min(1, side / Math.max(bitmap.width, bitmap.height));
        const canvas = document.createElement('canvas');
        canvas.width = Math.round(bitmap.width * scale);
        canvas.height = Math.round(bitmap.height * scale);
        canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        return canvas;
    };
    const jpeg = (canvas, quality) => new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality));
    const canvas = draw(maxSide), thumb = await jpeg(draw(240), 0.8);
    bitmap.close();
    for (const quality of [0.85, 0.75, 0.65, 0.5]) {
        const photo = await jpeg(canvas, quality);
        if (photo && (photo.size <= 3.5 * 1024 * 1024 || quality === 0.5)) return { photo, thumb };
    }
    throw new Error('not drawable');
}

// The day the photo was taken goes first in its name (a phone's file date is usually that), so a
// slideshow sorting by name sorts by date. The canvas drops the EXIF date, so it's kept here.
function datedName(file) {
    const day = new Date(file.lastModified || Date.now());
    const pad = (number) => String(number).padStart(2, '0');
    return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}_${file.name}`;
}

async function setUpFilesField(field) {
    const url = field.dataset.filesUrl;
    const isImage = field.dataset.filesAccept === 'image';
    const grid = field.querySelector('.files-grid');
    const status = field.querySelector('.files-status');
    const add = field.querySelector('.files-add');
    const input = add.querySelector('input');
    const detail = async (response) => (await response.json().catch(() => ({}))).detail || `Error ${response.status}`;
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) {
        status.textContent = await detail(response);
        return;
    }
    const info = await response.json();
    const megabytes = (bytes) => `${Math.floor(bytes / (1024 * 1024))} MB`;
    // The newest first, PAGE at a time: 500 photos would be a list a hundred rows long
    const PAGE = 30;
    let shown = PAGE;
    const more = Object.assign(document.createElement('button'), { type: 'button', className: 'btn secondary btn-sm files-more' });
    more.onclick = () => { shown += PAGE; draw(); };
    grid.after(more);
    const draw = () => {
        grid.replaceChildren(...info.files.slice(-shown).reverse().map((file) => {
            const tile = document.createElement('div');
            tile.className = 'files-tile';
            const preview = isImage ? Object.assign(document.createElement('img'), {
                src: `${url}/${encodeURIComponent(file.name)}?thumb=1`, alt: file.name, loading: 'lazy', title: file.name,
            }) : Object.assign(document.createElement('span'), { textContent: file.name });
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'btn danger btn-sm';
            remove.setAttribute('aria-label', `Remove ${file.name}`);
            remove.innerHTML = '<i class="fas fa-trash" aria-hidden="true"></i>';
            remove.onclick = async () => {
                const removed = await fetch(`${url}/${encodeURIComponent(file.name)}`, { method: 'DELETE' });
                if (!removed.ok) return showGlobal(await detail(removed), 'error');
                info.files = info.files.filter((other) => other.name !== file.name);
                draw();
            };
            tile.append(preview, remove);
            return tile;
        }));
        const hidden = info.files.length - shown;
        more.hidden = hidden <= 0;
        more.textContent = `Show more (${hidden} left)`;
        status.textContent = `${info.files.length} of ${info.max_files} · ${megabytes(info.free_bytes)} free on the mirror`;
        add.hidden = info.files.length >= info.max_files;
    };
    input.onchange = async () => {
        const chosen = [...input.files];
        input.value = '';
        add.hidden = true;
        for (const [index, file] of chosen.entries()) {
            status.textContent = `Adding ${index + 1} of ${chosen.length}…`;
            const form = new FormData();
            try {
                if (isImage) {
                    const { photo, thumb } = await shrinkImage(file, info.max_side);
                    form.append('file', photo, datedName(file));
                    if (thumb) form.append('thumb', thumb, 'thumb.jpg');
                } else {
                    form.append('file', file, file.name);
                }
            } catch (_) {
                showGlobal(`${file.name} couldn't be read as a picture.`, 'error');
                continue;
            }
            const sent = await fetch(url, { method: 'POST', body: form });
            if (!sent.ok) {
                showGlobal(`${file.name}: ${await detail(sent)}`, 'error');
                if (sent.status === 409) break;  // full
                continue;
            }
            info.files.push({ name: (await sent.json()).name });
            draw();
        }
        draw();
    };
    draw();
}

document.body.addEventListener('htmx:afterSwap', (event) => {
    if (event.target.id !== 'config-fields-global') return;
    event.target.querySelectorAll('.array-item-card').forEach(arrayItemSummary);
    refreshModulePreview();
});

window.openConfigureModule = function(name) {
    const card = document.getElementById('module-card-' + name);
    if (!card) return;
    const iconContainer = card.querySelector('.module-card-icon-container');
    openSettings = { name };
    window.openConfigSheet(name, card.dataset.title || name, iconContainer ? iconContainer.innerHTML : '');
};
let openSettings = null;  // the module whose settings are open

// Remove from mirror closes the settings; Save keeps them open (configSaved, sheet.js)
document.body.addEventListener('closeConfig', () => window.closeConfigSheet(true));

window.showRestartOverlay = function(title, message) {
    const overlay = document.getElementById('restart-overlay');
    if (overlay) {
        document.getElementById('restart-overlay-title').textContent = title || 'Restarting System';
        document.getElementById('restart-overlay-message').textContent = message || 'Please wait...';
        overlay.style.display = 'flex';
        overlay.classList.add('open');  // .modal-overlay is transparent until it's open
    }
};

window.hideRestartOverlay = function() {
    const overlay = document.getElementById('restart-overlay');
    if (overlay) {
        overlay.classList.remove('open');
        overlay.style.display = 'none';
    }
};

// Follows a background job (options.jobId) and the restart that ends it. The server
// reports a new boot_id once it has restarted, so there is no guessing from timings.
window.pollRestartAndReload = function(options = {}) {
    const startedAt = Date.now();
    const baseMessage = options.message || 'Restarting MirrorDash...';
    const messageEl = document.getElementById('restart-overlay-message');
    window.showRestartOverlay(options.title || 'Restarting MirrorDash', baseMessage);

    const finish = (ok, msg) => {
        window.hideRestartOverlay();
        showGlobal(msg, ok ? 'success' : 'error');
        if (options.onFinish) options.onFinish();
        if (!ok) return;
        if (options.targetPanel) {
            htmx.ajax('GET', '/admin/panels/' + options.targetPanel, {target: '#tab-content'});
        } else {
            window.location.reload();
        }
    };

    const tick = async () => {
        const secs = Math.round((Date.now() - startedAt) / 1000);
        if (secs > (options.maxSeconds || 900)) {
            return finish(false, 'This is taking unusually long. Check the mirror, then reload this page.');
        }
        let status = null;
        try {
            const res = await fetch(options.jobId ? '/admin/jobs/current' : '/health', {
                cache: 'no-store',
                signal: AbortSignal.timeout(4000),
            });
            if (res.ok) status = await res.json();
        } catch (_) {
            // No answer: the server is restarting or Wi-Fi hiccupped. Keep waiting.
        }
        if (status && status.boot_id !== options.bootId) {
            return finish(true, options.successMsg || 'Changes applied successfully.');
        }
        if (status && options.jobId && status.id === options.jobId && status.state === 'failed') {
            return finish(false, status.error || 'The operation failed.');
        }
        const installing = status && status.state === 'running';
        messageEl.textContent = `${installing ? baseMessage : 'Restarting MirrorDash...'} (${secs} s)`;
        setTimeout(tick, 1500);
    };
    setTimeout(tick, 1000);
};

// The bell's list closes on a click outside it, or Escape, like any dropdown
const closeBell = () => document.querySelector('details.notif[open]')?.removeAttribute('open');
document.addEventListener('click', (event) => { if (!event.target.closest('details.notif')) closeBell(); });
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeBell(); });

// Server responses ask for UI feedback through HX-Trigger-After-Swap events (see ui_events()).
document.body.addEventListener('md-notify', (e) => showGlobal(e.detail.message, e.detail.kind));
document.body.addEventListener('md-follow', (e) => window.pollRestartAndReload(e.detail));
document.body.addEventListener('md-overlay', (e) => window.showRestartOverlay(e.detail.title, e.detail.message));
document.body.addEventListener('backup-created', () => {
    const pwd = document.getElementById('backup-password');
    if (pwd) pwd.value = '';
});

// Route every hx-confirm through the app's own dialog instead of the browser's confirm().
document.body.addEventListener('htmx:confirm', async (event) => {
    if (!event.detail.question) return;
    event.preventDefault();
    if (await showConfirm('Are you sure?', event.detail.question)) {
        event.detail.issueRequest(true);
    }
});

document.body.addEventListener('htmx:responseError', (event) => {
    const xhr = event.detail.xhr;
    if (xhr.status === 401) {  // the session ended (logged out elsewhere, a new password): log in again
        if (loggedIn) {
            loggedIn = false;
            window.location.reload();
        }
        return;
    }
    // Never fail silently: show FastAPI's {"detail": ...} message, or a generic one.
    let detail = '';
    try {
        const body = JSON.parse(xhr.responseText);
        if (body.fields) return;  // a field's error: sheet_errors.js shows it at the field
        detail = body.detail;
    } catch (_) {}
    showGlobal(typeof detail === 'string' && detail ? detail : `Something went wrong (error ${xhr.status}).`, 'error');
});

// Loading is always visible: a line at the top while a tab loads. Only the latest tab
// request hides it (an aborted older one must not), and errors are shown just below.
let loadingRequest = null, loadingTimer = null;
document.body.addEventListener('htmx:beforeRequest', (event) => {
    if (event.detail.target?.id !== 'tab-content') return;
    loadingRequest = event.detail.requestConfig;
    clearTimeout(loadingTimer);
    loadingTimer = setTimeout(() => { document.getElementById('page-loading').hidden = false; }, 300);
});
document.body.addEventListener('htmx:afterRequest', (event) => {
    if (event.detail.requestConfig !== loadingRequest) return;
    clearTimeout(loadingTimer);
    document.getElementById('page-loading').hidden = true;
});

// A request got no answer. Often the phone just woke up and its Wi-Fi isn't back yet, so
// say "Reconnecting" and only call the mirror unreachable after 20 s without an answer.
const RECONNECTING = 'Reconnecting to the mirror…';
const UNREACHABLE = 'Could not reach the mirror. Check that it is on and connected to Wi-Fi.';
let reconnecting = false;
document.body.addEventListener('htmx:sendError', async () => {
    if (reconnecting) return;
    reconnecting = true;
    showGlobal(RECONNECTING, 'info', 0);
    const started = Date.now();
    while (true) {
        try {
            const res = await fetch('/health', { cache: 'no-store', signal: AbortSignal.timeout(3000) });
            if (res.ok) break;
        } catch (_) {}
        if (Date.now() - started > 20000) showGlobal(UNREACHABLE, 'error', 0);
        await new Promise(resolve => setTimeout(resolve, 2000));
    }
    reconnecting = false;
    const text = document.getElementById('global-status').textContent;
    if (text === RECONNECTING || text === UNREACHABLE) hideGlobal();
});

document.body.addEventListener('htmx:afterSwap', (event) => {
    triggerLucide(event.detail.target);
});

// Lucide is only needed for the module icon picker (the same icons the mirror draws).
// Scan just the swapped element: a full-document scan on every swap was slow on phones.
function triggerLucide(root) {
    if (typeof lucide !== 'undefined') {
        lucide.createIcons(root ? { root } : undefined);
    }
}

function closeReleaseNotesModal() {
    const modal = document.getElementById('notes-modal');
    modal.classList.remove('open');
    setTimeout(() => { modal.style.display = 'none'; }, 250);
}

function renderNotesMarkdown() {
    const sourceEl = document.getElementById('notes-markdown-source');
    const targetEl = document.getElementById('notes-rendered-content');
    if (sourceEl && targetEl && window.marked && window.DOMPurify) {
        targetEl.innerHTML = DOMPurify.sanitize(marked.parse(sourceEl.value));
    } else if (sourceEl && targetEl) {
        // Without the Markdown libraries (no CDN): the author's text as plain text, never as HTML
        const pre = document.createElement('pre');
        pre.className = 'notes-plain';
        pre.textContent = sourceEl.value;
        targetEl.replaceChildren(pre);
    }
}
// The notes arrive as HTML without a <script> (rule 8c): render them once they are in the modal
document.body.addEventListener('htmx:afterSettle', (e) => {
    if (e.target.id === 'notes-modal-content-container') renderNotesMarkdown();
});

function selectSubFieldColor(btn, inputId, value) {
    const inputEl = document.getElementById(inputId);
    if (inputEl) {
        inputEl.value = value;
    }
    const cardEl = btn.closest('.swatch-row');
    const swatches = cardEl.querySelectorAll('.color-swatch-btn');
    swatches.forEach(sw => {
        const isSelected = sw === btn;
        sw.classList.toggle('active', isSelected);
    });
}

function updateSwatchSelection(input) {
    const cardEl = input.closest('.swatch-row');
    const swatches = cardEl.querySelectorAll('.color-swatch-btn');
    swatches.forEach(sw => {
        const isSelected = sw.title.toLowerCase() === input.value.toLowerCase() || sw.getAttribute('onclick').includes(input.value);
        sw.classList.toggle('active', isSelected);
    });
}

function moveArrayItem(btn, direction) {
    const card = btn.closest('.array-item-card');
    const container = card.closest('.array-items-container');
    if (direction === 'up') {
        const prev = card.previousElementSibling;
        if (prev && prev.classList.contains('array-item-card')) {
            container.insertBefore(card, prev);
        }
    } else if (direction === 'down') {
        const next = card.nextElementSibling;
        if (next && next.classList.contains('array-item-card')) {
            container.insertBefore(next, card);
        }
    }
    reindexArrayContainer(container);
    container.dispatchEvent(new Event('input', { bubbles: true }));  // the preview follows the new order
}

// A folded list item's line (forms.py _row_summary does the same on the server): the text of its first choice
// box (a select), else its first filled text field, " · off" after it while its "enabled" switch is off; the
// next filled text field beside it; "# 2: Calendar" while there is nothing to say. Only the row's own fields
// count, not those of a list or object inside it.
function arrayItemSummary(card) {
    const title = card.querySelector('.array-item-index-title');
    const sub = card.querySelector('.array-item-sub');
    if (!title || !sub) return;
    const own = {};  // the row's fields by their last name part
    const named = [...card.querySelectorAll('.array-item-fields [name]')].map((el) => [el, el.name.match(/^(.*\[\d+\])\[([^\][]+)\]$/)]);
    const prefix = named.filter(([, m]) => m).map(([, m]) => m[1]).sort((x, y) => x.length - y.length)[0];
    named.forEach(([el, m]) => { if (m && m[1] === prefix && !(m[2] in own && el.type === 'hidden')) own[m[2]] = el; });
    const fields = Object.values(own);
    const filled = fields.filter((input) => input.matches('input[type="text"]') && !input.closest('.swatch-row')
        && !input.matches('[data-icon-input]') && input.value.trim());
    const choice = fields.find((el) => el.tagName === 'SELECT' && el.selectedOptions[0]?.text.trim());
    const label = choice ? choice.selectedOptions[0].text.trim() : filled[0]?.value.trim();
    const index = [...card.parentElement.querySelectorAll('.array-item-card')].indexOf(card);
    const off = own.enabled?.type === 'checkbox' && !own.enabled.checked;
    title.textContent = label ? label + (off ? ' \u00b7 off' : '') : `# ${index + 1}: ${title.dataset.itemTitle || 'Item'}`;
    sub.textContent = filled.find((input) => input.value.trim() !== label)?.value.trim() || '';
}

for (const type of ['input', 'change']) document.addEventListener(type, (event) => {
    const card = event.target.closest && event.target.closest('.array-item-card');
    if (card) arrayItemSummary(card);
});

// A row's place is in its fields' names ("modules[c][calendars][2][url]") and in the ids, labels, Show buttons,
// colour swatches and show_if rules made from them (forms.py _html_id): after a move or a removal each row
// takes its new place in all of them
const REINDEXED = ['name', 'id', 'for', 'data-reveal', 'aria-controls', 'onclick', 'data-show-if'];
const htmlId = (text) => text.replace(/[[\]_]/g, '-');
function reindexArrayContainer(container) {
    if (!container) return;
    container.querySelectorAll(':scope > .array-item-card').forEach((card, idx) => {
        arrayItemSummary(card);
        const match = card.querySelector('[name]')?.getAttribute('name').match(/^(.*?\[[A-Za-z0-9_-]+\])\[(\d+)\]/);
        if (!match || match[2] === String(idx)) return;
        const [before, after] = [`${match[1]}[${match[2]}]`, `${match[1]}[${idx}]`];
        const swap = (text) => text.split(before).join(after).split(htmlId(before)).join(htmlId(after));
        card.querySelectorAll('*').forEach((el) => REINDEXED.forEach((attr) => {
            const value = el.getAttribute(attr);
            if (value) el.setAttribute(attr, swap(value));
        }));
    });
}

document.body.addEventListener('htmx:afterSwap', (event) => {
    if (event.target && event.target.classList.contains('array-items-container')) {
        reindexArrayContainer(event.target);
        triggerLucide();
    }
});

window.addEventListener('DOMContentLoaded', async () => {
    const authed = await checkAuthStatus();
    if (authed) {
        triggerLucide();
        handleHashChange();
    }
});

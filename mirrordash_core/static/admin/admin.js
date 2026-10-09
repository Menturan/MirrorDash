// The admin page's script (loaded by templates/admin.html)
let currentApiKey = localStorage.getItem('mirrordash_api_key') || '';
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
        desc: 'Create an admin password to secure your mirror (at least 4 characters).',
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

// Logged in with a new password: remember it, then show its recovery code once
function acceptNewPassword(password, recoveryCode) {
    currentApiKey = password;
    localStorage.setItem('mirrordash_api_key', currentApiKey);
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
    if (mode !== 'login' && (mode === 'setup' ? password : newPassword).length < 4) {
        showAuthError('Password must be at least 4 characters.');
        return;
    }

    submitBtn.disabled = true;
    submitBtn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Processing…';
    try {
        if (mode === 'login') {
            const res = await fetch('/admin/system', { headers: { 'X-API-Key': password } });
            if (res.status === 200 || res.status === 404) {
                currentApiKey = password;
                localStorage.setItem('mirrordash_api_key', currentApiKey);
                overlay.classList.remove('open');
                authPromiseResolve(true);
            } else {
                showAuthError('Invalid password. Please try again.');
            }
        } else if (mode === 'setup') {
            const { ok, data } = await postAuth('/admin/auth/setup', { password });
            if (ok) return acceptNewPassword(password, data.recovery_code);
            showAuthError('Failed to set password: ' + (data.detail || 'Unknown error'));
        } else if (mode === 'recover') {
            const { ok, data } = await postAuth('/admin/auth/recover', { code, new_password: newPassword });
            if (ok) return acceptNewPassword(newPassword, data.recovery_code);
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
            headers: { 'Content-Type': 'application/json', 'X-API-Key': current.value },
            body: JSON.stringify({ new_password: next.value }),
        });
        if (res.ok) {
            currentApiKey = next.value;
            localStorage.setItem('mirrordash_api_key', currentApiKey);
            form.reset();
            showGlobal('Password changed.', 'success');
        } else {
            const data = await res.json().catch(() => ({}));
            showGlobal(res.status === 401 ? 'The current password is wrong.' : (data.detail || 'Could not change the password.'), 'error');
        }
    } catch (e) {
        showGlobal('Could not reach the mirror: ' + e.message, 'error');
    }
    button.disabled = false;
}

async function checkAuthStatus() {
    try {
        const res = await fetch('/admin/auth/status');
        if (!res.ok) throw new Error('Failed to fetch auth status');
        const data = await res.json();

        if (data.auth_corrupt) {
            currentApiKey = '';
            localStorage.removeItem('mirrordash_api_key');
            return await showAuthModal('recover', 'The admin password settings are damaged. Enter your recovery code and choose a new password.');
        }

        if (data.setup_required) {
            currentApiKey = '';
            localStorage.removeItem('mirrordash_api_key');
            return await showAuthModal('setup');
        } else {
            if (!currentApiKey) {
                return await showAuthModal('login');
            }
            return true;
        }
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
        const res = await fetch('/admin/restart', { method: 'POST', headers: { 'X-API-Key': currentApiKey } });
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

window.openConfigSheet = function(name, title, description, iconHtml, color, textColor, packageName, activeInstanceId) {
    document.getElementById('sheetTitle').innerText = `Configure ${title}`;
    document.getElementById('profileTitle').innerText = title;
    document.getElementById('profileDesc').innerText = description || 'Configure instance properties.';
    
    const profileIcon = document.getElementById('profileIcon');
    if (profileIcon) {
        profileIcon.style.background = color || 'rgba(255, 255, 255, 0.08)';
        profileIcon.style.color = textColor || '#ffffff';
        profileIcon.innerHTML = iconHtml || '<i class="fas fa-puzzle-piece"></i>';
    }
    
    const uninstallBtn = document.getElementById('uninstall-btn-global');
    if (uninstallBtn) {
        if (packageName) {
            uninstallBtn.style.display = 'inline-flex';
            uninstallBtn.setAttribute('hx-vals', `{"package_name": "${packageName}"}`);
            uninstallBtn.setAttribute('hx-confirm', `Are you sure you want to uninstall ${title}? This will remove the package and restart the mirror.`);
            htmx.process(uninstallBtn);
        } else {
            uninstallBtn.style.display = 'none';
        }
    }

    // Render instances list
    const card = document.getElementById('module-card-' + name);
    const instancesSection = document.getElementById('instancesSection');
    const instancesList = document.getElementById('instances-list-global');
    const addNewBtn = document.getElementById('add-new-instance-btn');

    if (card && instancesSection && instancesList && addNewBtn) {
        let instancesData = [];
        try {
            instancesData = JSON.parse(card.getAttribute('data-instances') || '[]');
        } catch (e) {
            console.error("Failed to parse instances JSON", e);
        }
        if (!instancesData) instancesData = [];
        
        if (Array.isArray(instancesData) && instancesData.length > 0) {
            instancesSection.style.display = 'block';
            instancesList.innerHTML = '';

            // Configure Add New button to load blank config form and highlight itself
            addNewBtn.onclick = function() {
                // De-select list items
                instancesList.querySelectorAll('.instance-item').forEach(el => {
                    el.style.borderColor = 'rgba(255, 255, 255, 0.05)';
                    el.style.background = 'rgba(255, 255, 255, 0.01)';
                });
                addNewBtn.className = 'btn primary btn-sm';
                htmx.ajax('GET', `/admin/panels/modules/config/${name}`, {target: '#config-fields-global', source: addNewBtn});
            };

            instancesData.forEach(inst => {
                const row = document.createElement('div');
                row.className = 'instance-item';
                row.style.display = 'flex';
                row.style.justifyContent = 'space-between';
                row.style.alignItems = 'center';
                row.style.padding = '10px 14px';
                row.style.borderRadius = '8px';
                row.style.cursor = 'pointer';
                row.style.transition = 'all 0.2s';
                
                const isActive = inst.id === activeInstanceId;
                if (isActive) {
                    row.style.border = '1px solid var(--primary)';
                    row.style.background = 'rgba(255, 255, 255, 0.06)';
                    addNewBtn.className = 'btn secondary btn-sm';
                } else {
                    row.style.border = '1px solid rgba(255, 255, 255, 0.05)';
                    row.style.background = 'rgba(255, 255, 255, 0.01)';
                }

                const posVal = inst.position || 'middle_center';
                const positionLabel = posVal.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase());

                row.innerHTML = `
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <div style="width: 6px; height: 6px; border-radius: 50%; background-color: ${inst.enabled ? 'var(--success)' : 'var(--text-muted)'}; box-shadow: ${inst.enabled ? '0 0 6px var(--success-glow)' : 'none'};"></div>
                        <span style="font-size: 0.85rem; font-weight: 500; color: white;">${inst.title || inst.id}</span>
                    </div>
                    <span style="display: inline-flex; align-items: center; gap: 12px;">
                        <span class="status-badge" style="background: rgba(255, 255, 255, 0.04); color: #a1a1aa; border: 1px solid rgba(255,255,255,0.08); font-size: 0.65rem; padding: 2px 6px; border-radius: 4px;">
                            ${positionLabel}
                        </span>
                        <label class="switch" onclick="event.stopPropagation()" style="transform: scale(0.8); margin: 0; display: inline-block;">
                            <input type="checkbox" ${inst.enabled ? 'checked' : ''} 
                                   hx-post="/admin/panels/modules/config/${name}/toggle?instance_id=${inst.id}"
                                   hx-swap="none">
                            <span class="slider round"></span>
                        </label>
                        <i class="fas fa-pen-to-square" style="color: var(--text-muted); font-size: 0.7rem; opacity: 0.6; transition: opacity 0.2s;"></i>
                    </span>
                `;

                row.onclick = function() {
                    // Update active states
                    instancesList.querySelectorAll('.instance-item').forEach(el => {
                        el.style.borderColor = 'rgba(255, 255, 255, 0.05)';
                        el.style.background = 'rgba(255, 255, 255, 0.01)';
                    });
                    row.style.border = '1px solid var(--primary)';
                    row.style.background = 'rgba(255, 255, 255, 0.06)';
                    addNewBtn.className = 'btn secondary btn-sm';
                    
                    htmx.ajax('GET', `/admin/panels/modules/config/${name}?instance_id=${inst.id}`, {target: '#config-fields-global', source: row});
                };

                instancesList.appendChild(row);
                htmx.process(row);
            });
        } else {
            instancesSection.style.display = 'none';
        }
    }

    document.getElementById('configOverlay').classList.add('open');
};

window.closeConfigSheet = function() {
    document.getElementById('configOverlay').classList.remove('open');
    const fields = document.getElementById('config-fields-global');
    if (fields) fields.innerHTML = '';
};

// Keep backwards compatibility for toggle/open calls
window.toggleConfigureModule = function(name) {
    window.openConfigureModule(name);
};

window.openConfigureModule = function(name, packageName, activeInstanceId) {
    const card = document.getElementById('module-card-' + name);
    if (!card) return;
    
    const titleEl = card.querySelector('h3');
    const title = titleEl ? titleEl.innerText : name;
    
    const descEl = card.querySelector('p');
    const description = descEl ? descEl.innerText : '';
    
    const iconContainer = card.querySelector('.module-card-icon-container');
    let iconHtml = '<i class="fas fa-puzzle-piece"></i>';
    if (iconContainer) {
        iconHtml = iconContainer.innerHTML;
    }

    // Resolve initial default instance ID if none is active
    if (activeInstanceId === undefined || activeInstanceId === null) {
        let instancesData = [];
        try {
            instancesData = JSON.parse(card.getAttribute('data-instances') || '[]');
        } catch (e) {}
        if (!instancesData) instancesData = [];
        activeInstanceId = instancesData.length > 0 ? instancesData[0].id : '';
    }
    
    window.openConfigSheet(name, title, description, iconHtml, null, null, packageName, activeInstanceId);
};

// Close on refreshModules trigger
document.body.addEventListener('refreshModules', function() {
    window.closeConfigSheet();
});

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
        if (secs > 900) {
            return finish(false, 'This is taking unusually long. Check the mirror, then reload this page.');
        }
        let status = null;
        try {
            const res = await fetch(options.jobId ? '/admin/jobs/current' : '/health', {
                headers: { 'X-API-Key': currentApiKey },
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

document.body.addEventListener('htmx:configRequest', (event) => {
    event.detail.headers['X-API-Key'] = currentApiKey;
});

document.body.addEventListener('htmx:responseError', (event) => {
    const xhr = event.detail.xhr;
    if (xhr.status === 401 || xhr.status === 403) {
        if (currentApiKey) {
            localStorage.removeItem('mirrordash_api_key');
            currentApiKey = '';
            window.location.reload();
        }
        return;
    }
    // Never fail silently: show FastAPI's {"detail": ...} message, or a generic one.
    let detail = '';
    try { detail = JSON.parse(xhr.responseText).detail; } catch (_) {}
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
        targetEl.innerHTML = `<pre style="white-space: pre-wrap; font-family: inherit;">${sourceEl.value}</pre>`;
    }
}

function selectSubFieldColor(btn, inputId, value) {
    const inputEl = document.getElementById(inputId);
    if (inputEl) {
        inputEl.value = value;
    }
    const cardEl = btn.closest('.array-item-card');
    const swatches = cardEl.querySelectorAll('.color-swatch-btn');
    swatches.forEach(sw => {
        const isSelected = sw === btn;
        sw.classList.toggle('active', isSelected);
    });
}

function selectSubFieldIcon(btn, inputId, value) {
    const inputEl = document.getElementById(inputId);
    if (inputEl) {
        inputEl.value = value;
    }
    const cardEl = btn.closest('.array-item-card');
    const btns = cardEl.querySelectorAll('.icon-picker-btn');
    btns.forEach(b => {
        const isSelected = b === btn;
        b.classList.toggle('active', isSelected);
    });
}

function updateSwatchSelection(input) {
    const cardEl = input.closest('.array-item-card');
    const swatches = cardEl.querySelectorAll('.color-swatch-btn');
    swatches.forEach(sw => {
        const isSelected = sw.title.toLowerCase() === input.value.toLowerCase() || sw.getAttribute('onclick').includes(input.value);
        sw.classList.toggle('active', isSelected);
    });
}

function updateIconSelection(input) {
    const cardEl = input.closest('.array-item-card');
    const btns = cardEl.querySelectorAll('.icon-picker-btn');
    btns.forEach(b => {
        const isSelected = b.title.toLowerCase() === input.value.toLowerCase();
        b.classList.toggle('active', isSelected);
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
}

function reindexArrayContainer(container) {
    if (!container) return;
    const cards = container.querySelectorAll('.array-item-card');
    cards.forEach((card, idx) => {
        const titleEl = card.querySelector('.array-item-index-title');
        if (titleEl) {
            const itemTitle = titleEl.getAttribute('data-item-title') || 'Item';
            titleEl.textContent = `# ${idx + 1}: ${itemTitle}`;
        }
        
        const inputs = card.querySelectorAll('input, select, textarea');
        inputs.forEach(input => {
            const name = input.getAttribute('name');
            if (name) {
                const newName = name.replace(/(\[[a-zA-Z_-]+\])\[\d+\]/, `$1[${idx}]`);
                input.setAttribute('name', newName);
                
                const id = input.getAttribute('id');
                if (id) {
                    const newId = `field-${newName}`.replace(/\[/g, '-').replace(/\]/g, '-').replace(/_/g, '-').replace(/--/g, '-').replace(/-$/, '');
                    input.setAttribute('id', newId);
                    
                    const colorSwatchBtns = card.querySelectorAll('.color-swatch-btn');
                    colorSwatchBtns.forEach(sw => {
                        const onclick = sw.getAttribute('onclick');
                        if (onclick) {
                            const newOnclick = onclick.replace(/selectSubFieldColor\(this,\s*'[^']+',/g, `selectSubFieldColor(this, '${newId}',`);
                            sw.setAttribute('onclick', newOnclick);
                        }
                    });
                    const iconPickerBtns = card.querySelectorAll('.icon-picker-btn');
                    iconPickerBtns.forEach(pb => {
                        const onclick = pb.getAttribute('onclick');
                        if (onclick) {
                            const newOnclick = onclick.replace(/selectSubFieldIcon\(this,\s*'[^']+',/g, `selectSubFieldIcon(this, '${newId}',`);
                            pb.setAttribute('onclick', newOnclick);
                        }
                    });
                }
            }
        });
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

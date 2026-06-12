let currentApiKey = localStorage.getItem('mirrordash_api_key') || localStorage.getItem('mymm_api_key') || '';
let currentConfig = { modules: {} };
let installedModules = {};
let pypiReleaseCache = {};
let updatesCheckStarted = false;

function getInstalledModuleMeta(moduleName) {
    if (!moduleName) return null;
    if (installedModules[moduleName]) return installedModules[moduleName];
    const norm = moduleName.replace(/-/g, '_');
    for (const [k, v] of Object.entries(installedModules)) {
        if (k.replace(/-/g, '_') === norm) {
            return v;
        }
    }
    return null;
}

function isModuleConfigured(name) {
    if (!name) return false;
    const norm = name.replace(/-/g, '_');
    return Object.keys(currentConfig.modules).some(k => k.replace(/-/g, '_') === norm);
}

function getConfiguredModuleKey(name) {
    if (!name) return null;
    const norm = name.replace(/-/g, '_');
    for (const key of Object.keys(currentConfig.modules)) {
        if (key.replace(/-/g, '_') === norm) {
            return key;
        }
    }
    return null;
}

function authHeaders() {
    return {
        'Content-Type': 'application/json',
        'X-API-Key': currentApiKey
    };
}

const restartBtn        = document.getElementById('restart-btn');
const globalStatus      = document.getElementById('global-status');

// Page Panels
const pageTabConfig      = document.getElementById('page-tab-config');
const pageTabModules     = document.getElementById('page-tab-modules');
const pageTabLogs        = document.getElementById('page-tab-logs');
const pageTabBackup      = document.getElementById('page-tab-backup');
const pagePanelConfig    = document.getElementById('page-panel-config');
const pagePanelModules   = document.getElementById('page-panel-modules');
const pagePanelLogs      = document.getElementById('page-panel-logs');
const pagePanelBackup    = document.getElementById('page-panel-backup');
const pageTabSystem      = document.getElementById('page-tab-system');
const pagePanelSystem    = document.getElementById('page-panel-system');

function showGlobal(msg, type = 'info') {
    globalStatus.textContent = msg;
    globalStatus.className = `alert alert--${type}`;
    globalStatus.hidden = false;
    setTimeout(() => { globalStatus.hidden = true; }, 5000);
}

function setLoading(btn, labelEl, loading, defaultLabel, loadingLabel) {
    btn.disabled = loading;
    labelEl.textContent = loading ? loadingLabel : defaultLabel;
}

async function checkAuthStatus() {
    try {
        const res = await fetch('/admin/auth/status');
        if (!res.ok) throw new Error('Failed to fetch auth status');
        const data = await res.json();
        
        if (data.setup_required) {
            const pwd = prompt('Welcome! Please create an admin password for MirrorDash (at least 4 characters):');
            if (!pwd || pwd.length < 4) {
                alert('Password must be at least 4 characters. Reload to try again.');
                return false;
            }
            const setupRes = await fetch('/admin/auth/setup', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password: pwd })
            });
            
            if (setupRes.ok) {
                currentApiKey = pwd;
                localStorage.setItem('mirrordash_api_key', currentApiKey);
                showGlobal('Password set successfully.', 'success');
                return true;
            } else {
                const errData = await setupRes.json();
                alert('Failed to set password: ' + (errData.detail || 'Unknown error'));
                return false;
            }
        } else {
            if (!currentApiKey) {
                const pwd = prompt('Enter MirrorDash admin password:');
                if (!pwd) return false;
                currentApiKey = pwd;
                localStorage.setItem('mirrordash_api_key', currentApiKey);
            }
            return true;
        }
    } catch (err) {
        showGlobal('Auth check failed: ' + err.message, 'error');
        return false;
    }
}

function initPageTabs() {
    const tabs = [
        { tab: pageTabConfig, panel: pagePanelConfig },
        { tab: pageTabModules, panel: pagePanelModules, callback: loadModules },
        { tab: pageTabLogs, panel: pagePanelLogs, callback: loadLogs },
        { tab: pageTabBackup, panel: pagePanelBackup, callback: loadBackupsList },
        { tab: pageTabSystem, panel: pagePanelSystem, callback: loadSystemSettings }
    ];

    tabs.forEach(({ tab, panel, callback }) => {
        tab.onclick = () => {
            tabs.forEach(t => {
                const active = t.tab === tab;
                t.tab.classList.toggle('active', active);
                t.tab.setAttribute('aria-selected', active ? 'true' : 'false');
                t.panel.style.display = active ? 'block' : 'none';
            });
            if (callback) callback();
        };
    });
}


restartBtn.onclick = async () => {
    if (!confirm('Are you sure you want to restart the system?')) return;
    restartBtn.disabled = true;
    restartBtn.textContent = 'Restarting…';
    try {
        const res = await fetch('/admin/restart', { method: 'POST', headers: authHeaders() });
        if (!res.ok) { showGlobal('Restart request failed.', 'error'); restartBtn.disabled = false; restartBtn.innerHTML = '<i class="fas fa-power-off" aria-hidden="true"></i> Restart'; return; }
    } catch (_) {}
    // Poll until server is back up
    showGlobal('Restarting… waiting for server to come back online.', 'info');
    const pollStart = Date.now();
    const poll = setInterval(async () => {
        if (Date.now() - pollStart > 30000) { clearInterval(poll); showGlobal('Server did not respond after 30s.', 'error'); return; }
        try {
            const r = await fetch('/health');
            if (r.ok) { clearInterval(poll); window.location.reload(); }
        } catch (_) {}
    }, 1500);
};

async function initBootStatusAlert() {
    const banner = document.getElementById('system-alert-banner');
    if (!banner) return;
    try {
        const response = await fetch('/api/active-modules');
        if (!response.ok) return;
        const data = await response.json();
        
        if (data.boot_status === 'rollback' || data.boot_status === 'safe_mode') {
            let title, iconClass, messageText, actionButtonHtml;
            const isSafeMode = data.boot_status === 'safe_mode';
            
            if (isSafeMode) {
                title = 'Safe Mode Active';
                iconClass = 'fas fa-shield-alt';
                messageText = 'MirrorDash has booted from the read-only Golden Copy (safe backup environment) because the primary virtual environment failed to launch. Custom modules and updates are currently disabled.';
                actionButtonHtml = `<button id="rebuild-venv-btn" class="btn danger"><i class="fas fa-tools"></i> Rebuild Active Environment</button>`;
            } else {
                title = 'System Rollback Triggered';
                iconClass = 'fas fa-history';
                messageText = 'MirrorDash recovered from a critical startup crash by automatically rolling back the last update. Some of your recent changes may have been reverted to restore stability.';
                actionButtonHtml = `<button id="rebuild-venv-btn" class="btn secondary"><i class="fas fa-tools"></i> Rebuild Active Environment</button>`;
            }
            
            banner.className = `system-alert system-alert--${isSafeMode ? 'error' : 'warning'}`;
            banner.innerHTML = `
                <div class="system-alert-header">
                    <i class="${iconClass}"></i>
                    <h3 class="system-alert-title">${title}</h3>
                </div>
                <p class="system-alert-body">${messageText}</p>
                <div class="system-alert-actions">
                    ${actionButtonHtml}
                </div>
            `;
            banner.style.display = 'flex';
            
            const rebuildBtn = document.getElementById('rebuild-venv-btn');
            if (rebuildBtn) {
                rebuildBtn.onclick = async () => {
                    if (!confirm('Are you sure you want to completely rebuild the primary virtual environment? This will re-install core dependencies and active modules from scratch. It might take a few minutes.')) {
                        return;
                    }
                    rebuildBtn.disabled = true;
                    rebuildBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Rebuilding...';
                    
                    try {
                        const res = await fetch('/admin/rebuild-venv', {
                            method: 'POST',
                            headers: authHeaders()
                        });
                        
                        if (res.ok) {
                            showGlobal('Rebuild succeeded. Restarting system...', 'success');
                            // Wait for restart and reload
                            setTimeout(() => {
                                const pollStart = Date.now();
                                const poll = setInterval(async () => {
                                    if (Date.now() - pollStart > 60000) {
                                        clearInterval(poll);
                                        showGlobal('Server did not respond after 60s.', 'error');
                                        rebuildBtn.disabled = false;
                                        rebuildBtn.innerHTML = '<i class="fas fa-tools"></i> Rebuild Active Environment';
                                        return;
                                    }
                                    try {
                                        const r = await fetch('/health');
                                        if (r.ok) {
                                            clearInterval(poll);
                                            window.location.reload();
                                        }
                                    } catch (_) {}
                                }, 2000);
                            }, 3000);
                        } else {
                            const err = await res.json();
                            showGlobal('Rebuild failed: ' + (err.detail || 'Unknown error'), 'error');
                            rebuildBtn.disabled = false;
                            rebuildBtn.innerHTML = '<i class="fas fa-tools"></i> Rebuild Active Environment';
                        }
                    } catch (e) {
                        showGlobal('Network error during rebuild: ' + e.message, 'error');
                        rebuildBtn.disabled = false;
                        rebuildBtn.innerHTML = '<i class="fas fa-tools"></i> Rebuild Active Environment';
                    }
                };
            }
        } else {
            banner.style.display = 'none';
        }
    } catch (e) {
        console.error('Failed to load boot status:', e);
    }
}

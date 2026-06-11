const sysBrightness      = document.getElementById('sys-brightness');
const sysBrightnessVal   = document.getElementById('sys-brightness-val');
const sysVolume          = document.getElementById('sys-volume');
const sysVolumeVal       = document.getElementById('sys-volume-val');
const sysRotation        = document.getElementById('sys-rotation');
const sysResolution      = document.getElementById('sys-resolution');
const sysSsh             = document.getElementById('sys-ssh');
const sysSshPasswordGroup = document.getElementById('sys-ssh-password-group');
const sysSshPassword      = document.getElementById('sys-ssh-password');
let initialSshState       = false;
const saveSystemBtn      = document.getElementById('save-system-btn');
const saveSystemLabel    = document.getElementById('save-system-label');
const screenOnBtn        = document.getElementById('screen-on-btn');
const screenOffBtn       = document.getElementById('screen-off-btn');

// Core update UI elements
const coreCurrentVersion = document.getElementById('core-current-version');
const coreCheckBtn       = document.getElementById('core-check-btn');
const coreCheckLabel     = document.getElementById('core-check-label');
const coreUpdateResult   = document.getElementById('core-update-result');

// Display Power Management elements
const sysDisplayMode        = document.getElementById('sys-display-mode');
const displayIntervalGroup  = document.getElementById('display-mode-interval-group');
const displayPirGroup       = document.getElementById('display-mode-pir-group');
const displayButtonGroup    = document.getElementById('display-mode-button-group');

// Time formatting conversions
function convert24to12(time24) {
    const [hStr, mStr] = time24.split(':');
    const h24 = parseInt(hStr, 10);
    const period = h24 >= 12 ? 'PM' : 'AM';
    let h12 = h24 % 12;
    if (h12 === 0) h12 = 12;
    return {
        hour: h12,
        minute: mStr,
        period: period
    };
}

function convert12to24(h12, minute, period) {
    let h24 = h12;
    if (period === 'PM') {
        if (h12 !== 12) h24 = h12 + 12;
    } else {
        if (h12 === 12) h24 = 0;
    }
    return `${String(h24).padStart(2, '0')}:${minute}`;
}

let currentTimeFormat = '24h';

function renderTimeSelects(timeFormat) {
    const startContainer = document.getElementById('sys-display-start-container');
    const endContainer = document.getElementById('sys-display-end-container');
    if (!startContainer || !endContainer) return;
    
    let hOptions = '';
    if (timeFormat === '12h') {
        for (let i = 1; i <= 12; i++) {
            hOptions += `<option value="${i}">${i}</option>`;
        }
    } else {
        for (let i = 0; i <= 23; i++) {
            const hStr = String(i).padStart(2, '0');
            hOptions += `<option value="${i}">${hStr}</option>`;
        }
    }
    
    let mOptions = '';
    for (let i = 0; i <= 59; i++) {
        const mStr = String(i).padStart(2, '0');
        mOptions += `<option value="${mStr}">${mStr}</option>`;
    }
    
    const buildHTML = (prefix) => {
        let html = `
            <div style="display: flex; gap: 6px; align-items: center;">
                <select id="${prefix}-h" class="form-control" style="width: 70px; padding: 4px 8px; font-size: 0.85rem; height: 32px; background: #222; color: white;">
                    ${hOptions}
                </select>
                <span style="color: white; font-weight: bold;">:</span>
                <select id="${prefix}-m" class="form-control" style="width: 70px; padding: 4px 8px; font-size: 0.85rem; height: 32px; background: #222; color: white;">
                    ${mOptions}
                </select>
        `;
        if (timeFormat === '12h') {
            html += `
                <select id="${prefix}-ampm" class="form-control" style="width: 75px; padding: 4px 8px; font-size: 0.85rem; height: 32px; background: #222; color: white;">
                    <option value="AM">AM</option>
                    <option value="PM">PM</option>
                </select>
            `;
        }
        html += `</div>`;
        return html;
    };
    
    startContainer.innerHTML = buildHTML('start');
    endContainer.innerHTML = buildHTML('end');
}

function toggleDisplayModeGroups(mode) {
    displayIntervalGroup.style.display = mode === 'interval' ? 'block' : 'none';
    displayPirGroup.style.display = mode === 'pir' ? 'block' : 'none';
    displayButtonGroup.style.display = mode === 'button' ? 'block' : 'none';
}

sysDisplayMode.onchange = () => {
    toggleDisplayModeGroups(sysDisplayMode.value);
};

sysSsh.onchange = () => {
    if (sysSsh.checked && !initialSshState) {
        sysSshPasswordGroup.style.display = 'block';
    } else {
        sysSshPasswordGroup.style.display = 'none';
    }
};

sysBrightness.oninput = () => {
    sysBrightnessVal.textContent = sysBrightness.value + '%';
};
sysVolume.oninput = () => {
    sysVolumeVal.textContent = sysVolume.value + '%';
};

saveSystemBtn.onclick = async () => {
    let startTimeStr = '07:00';
    let endTimeStr = '22:00';
    
    const startH = document.getElementById('start-h');
    if (startH) {
        const h = Number(startH.value);
        const m = document.getElementById('start-m').value;
        if (currentTimeFormat === '12h') {
            const ampm = document.getElementById('start-ampm').value;
            startTimeStr = convert12to24(h, m, ampm);
        } else {
            startTimeStr = `${String(h).padStart(2, '0')}:${m}`;
        }
    }
    
    const endH = document.getElementById('end-h');
    if (endH) {
        const h = Number(endH.value);
        const m = document.getElementById('end-m').value;
        if (currentTimeFormat === '12h') {
            const ampm = document.getElementById('end-ampm').value;
            endTimeStr = convert12to24(h, m, ampm);
        } else {
            endTimeStr = `${String(h).padStart(2, '0')}:${m}`;
        }
    }

    const payload = {
        rotation: sysRotation.value,
        resolution: sysResolution.value,
        brightness: Number(sysBrightness.value),
        volume: Number(sysVolume.value),
        ssh: sysSsh.checked,
        display_control: {
            mode: sysDisplayMode.value,
            interval: {
                start: startTimeStr,
                end: endTimeStr
            },
            pir: {
                pin: Number(document.getElementById('sys-display-pir-pin').value),
                timeout_minutes: Number(document.getElementById('sys-display-pir-timeout').value)
            },
            button: {
                pin: Number(document.getElementById('sys-display-button-pin').value)
            }
        }
    };
    if (sysSsh.checked && !initialSshState) {
        const pwd = sysSshPassword.value;
        if (!pwd || pwd.length < 8) {
            showGlobal('A password of at least 8 characters is required to enable SSH.', 'error');
            return;
        }
        payload.pi_password = pwd;
    }

    setLoading(saveSystemBtn, saveSystemLabel, true, 'Apply Settings', 'Applying…');
    try {
        const res = await fetch('/admin/system', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        if (res.ok) {
            showGlobal(result.message, 'success');
        } else {
            showGlobal('Error: ' + (result.detail ?? result.message), 'error');
        }
    } catch (err) {
        showGlobal('Network error: ' + err.message, 'error');
    } finally {
        setLoading(saveSystemBtn, saveSystemLabel, false, 'Apply Settings', 'Applying…');
    }
};

const setScreenPowerState = async (state) => {
    const btn = state === 'on' ? screenOnBtn : screenOffBtn;
    const origText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<i class="fas fa-spinner fa-spin" aria-hidden="true"></i> ${state === 'on' ? 'Turning ON…' : 'Turning OFF…'}`;
    try {
        const res = await fetch('/admin/screen', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({ state })
        });
        const result = await res.json();
        if (res.ok) {
            showGlobal(result.message, 'success');
        } else {
            showGlobal('Error: ' + (result.detail ?? result.message), 'error');
        }
    } catch (err) {
        showGlobal('Network error: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = origText;
    }
};

screenOnBtn.onclick = () => setScreenPowerState('on');
screenOffBtn.onclick = () => setScreenPowerState('off');

async function loadSystemSettings() {
    try {
        // Load time format from config globals first
        const configRes = await fetch('/admin/config', { headers: authHeaders() });
        if (configRes.ok) {
            const configData = await configRes.json();
            currentTimeFormat = configData.globals?.time_format || '24h';
        }

        const res = await fetch('/admin/system', { headers: authHeaders() });
        if (!res.ok) {
            showGlobal('Failed to load system settings.', 'error');
            return;
        }
        const data = await res.json();
        const settings = data.settings;
        
        sysBrightness.value = settings.brightness;
        sysBrightnessVal.textContent = settings.brightness + '%';
        
        sysVolume.value = settings.volume;
        sysVolumeVal.textContent = settings.volume + '%';
        
        sysRotation.value = settings.rotation;
        sysSsh.checked = !!settings.ssh;
        initialSshState = !!settings.ssh;
        sysSshPasswordGroup.style.display = 'none';
        
        // Populate display control fields
        const displayCtrl = settings.display_control || {
            mode: 'manual',
            interval: { start: '07:00', end: '22:00' },
            pir: { pin: 18, timeout_minutes: 5 },
            button: { pin: 23 }
        };
        
        sysDisplayMode.value = displayCtrl.mode;
        
        // Render selects using global time format first
        renderTimeSelects(currentTimeFormat);
        
        // Now populate selects based on format
        const startVal = displayCtrl.interval?.start || '07:00';
        const endVal = displayCtrl.interval?.end || '22:00';
        
        if (currentTimeFormat === '12h') {
            const start12 = convert24to12(startVal);
            document.getElementById('start-h').value = start12.hour;
            document.getElementById('start-m').value = start12.minute;
            document.getElementById('start-ampm').value = start12.period;
            
            const end12 = convert24to12(endVal);
            document.getElementById('end-h').value = end12.hour;
            document.getElementById('end-m').value = end12.minute;
            document.getElementById('end-ampm').value = end12.period;
        } else {
            const [sh, sm] = startVal.split(':');
            document.getElementById('start-h').value = parseInt(sh, 10);
            document.getElementById('start-m').value = sm;
            
            const [eh, em] = endVal.split(':');
            document.getElementById('end-h').value = parseInt(eh, 10);
            document.getElementById('end-m').value = em;
        }
        
        toggleDisplayModeGroups(displayCtrl.mode);
        
        sysResolution.innerHTML = '';
        data.resolutions.forEach(resOpt => {
            const label = resOpt === 'auto' ? 'Auto (Default)' : resOpt;
            const selected = settings.resolution === resOpt ? 'selected' : '';
            sysResolution.innerHTML += `<option value="${resOpt}" ${selected}>${label}</option>`;
        });
    } catch (err) {
        showGlobal('Error loading system settings: ' + err.message, 'error');
    }

    // Populate the installed version label without triggering a full check
    checkCoreUpdate(false);
}

// -----------------------------------------------------------------------
// Core self-update helpers
// -----------------------------------------------------------------------

/**
 * Check PyPI for a newer mirrordash-core release.
 * @param {boolean} [showResult=true] - When false, only populate the version
 *   label without displaying the result panel (used during tab load).
 */
async function checkCoreUpdate(showResult = true) {
    setLoading(coreCheckBtn, coreCheckLabel, true, 'Check for Updates', 'Checking…');
    coreUpdateResult.style.display = 'none';

    try {
        const res = await fetch('/admin/core-update-check', { headers: authHeaders() });
        const data = await res.json();

        if (!res.ok) {
            if (showResult) {
                coreUpdateResult.innerHTML = `
                    <div style="color: var(--color-status-warning, #f87171); font-size: 0.85rem;">
                        <i class="fas fa-exclamation-triangle"></i>
                        Could not reach PyPI: ${data.detail || 'Unknown error'}
                    </div>`;
                coreUpdateResult.style.display = 'block';
            }
            return;
        }

        // Update the installed version label
        coreCurrentVersion.textContent = data.current_version || '—';

        if (!showResult) return;

        if (data.update_available) {
            coreUpdateResult.innerHTML = `
                <div style="display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
                            background: rgba(59,130,246,0.08); border: 1px solid rgba(59,130,246,0.2);
                            border-radius: 8px; padding: 12px 16px;">
                    <div style="flex: 1; min-width: 160px;">
                        <span class="status-badge update-avail">Update Available — v${data.latest_version}</span>
                        <p style="font-size: 0.8rem; color: var(--text-muted); margin: 6px 0 0 0;">
                            A new version of the MirrorDash core framework is ready.
                            The system will restart automatically after upgrading.
                        </p>
                    </div>
                    <button type="button" id="core-upgrade-btn" class="btn primary"
                            onclick="triggerCoreUpgrade('${data.latest_version}')">
                        <i class="fas fa-arrow-alt-circle-up"></i>
                        <span id="core-upgrade-label">Upgrade to v${data.latest_version}</span>
                    </button>
                </div>`;
        } else {
            coreUpdateResult.innerHTML = `
                <div style="display: flex; align-items: center; gap: 8px;
                            color: var(--color-status-online, #a0ffba); font-size: 0.85rem;">
                    <i class="fas fa-check-circle"></i>
                    MirrorDash Core v${data.current_version} is up to date.
                </div>`;
        }
        coreUpdateResult.style.display = 'block';
    } catch (err) {
        if (showResult) {
            coreUpdateResult.innerHTML = `
                <div style="color: var(--color-status-warning, #f87171); font-size: 0.85rem;">
                    <i class="fas fa-exclamation-triangle"></i>
                    Network error: ${err.message}
                </div>`;
            coreUpdateResult.style.display = 'block';
        }
    } finally {
        setLoading(coreCheckBtn, coreCheckLabel, false, 'Check for Updates', 'Checking…');
    }
}

coreCheckBtn.onclick = () => checkCoreUpdate(true);

/**
 * Upgrade mirrordash-core and restart the server.
 * @param {string} targetVersion - Display label for the target version.
 */
window.triggerCoreUpgrade = async function(targetVersion) {
    if (!confirm(`Are you sure you want to upgrade MirrorDash Core to v${targetVersion}? The system will restart.`)) return;

    const upgradeBtn  = document.getElementById('core-upgrade-btn');
    const upgradeLabel = document.getElementById('core-upgrade-label');
    if (upgradeBtn) {
        upgradeBtn.disabled = true;
        if (upgradeLabel) upgradeLabel.textContent = 'Upgrading…';
    }

    showGlobal('Upgrading MirrorDash Core… Please wait.', 'info');

    try {
        const res = await fetch('/admin/core-update', {
            method: 'POST',
            headers: authHeaders(),
        });
        const result = await res.json();

        if (res.ok) {
            showGlobal(result.message, 'success');

            // Poll /health until the server comes back up after restart
            const pollStart = Date.now();
            const poll = setInterval(async () => {
                if (Date.now() - pollStart > 60000) {
                    clearInterval(poll);
                    showGlobal('Server did not respond after 60 s. Please reload manually.', 'error');
                    return;
                }
                try {
                    const r = await fetch('/health');
                    if (r.ok) {
                        clearInterval(poll);
                        window.location.reload();
                    }
                } catch (_) {}
            }, 1500);
        } else {
            showGlobal('Upgrade failed: ' + (result.detail ?? result.message), 'error');
            if (upgradeBtn) {
                upgradeBtn.disabled = false;
                if (upgradeLabel) upgradeLabel.textContent = `Upgrade to v${targetVersion}`;
            }
        }
    } catch (err) {
        showGlobal('Network error: ' + err.message, 'error');
        if (upgradeBtn) {
            upgradeBtn.disabled = false;
            if (upgradeLabel) upgradeLabel.textContent = `Upgrade to v${targetVersion}`;
        }
    }
};

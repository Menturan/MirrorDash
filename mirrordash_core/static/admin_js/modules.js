const moduleSearchInput = document.getElementById('module-search-input');
const installedModulesContainer = document.getElementById('installed-modules-container');
const discoverModulesContainer = document.getElementById('discover-modules-container');
const customPackageName = document.getElementById('custom-package-name');
const customInstallBtn = document.getElementById('custom-install-btn');
const customInstallBtnLabel = document.getElementById('custom-install-btn-label');
const customInstallStatus = document.getElementById('custom-install-status');
const updatesCountBadge = document.getElementById('updates-count-badge');

const notesModal        = document.getElementById('notes-modal');
const modalTitle        = document.getElementById('modal-title');
const modalSubtitle     = document.getElementById('modal-subtitle');
const modalBody         = document.getElementById('modal-body');
const modalUpdateBtn    = document.getElementById('modal-update-btn');

// Popular community modules that can be discovered and installed
let communityModules = [];

function getModuleDefaultConfig(schema) {
    const defaults = {};
    if (schema && schema.properties) {
        for (const [key, prop] of Object.entries(schema.properties)) {
            if (prop.default !== undefined) {
                defaults[key] = prop.default;
            } else if (prop.type === 'boolean') {
                defaults[key] = false;
            } else if (prop.type === 'array') {
                defaults[key] = [];
            } else if (prop.type === 'integer' || prop.type === 'number') {
                defaults[key] = 0;
            } else {
                defaults[key] = '';
            }
        }
    }
    // Ensure position and enabled are set if not in schema
    if (defaults.enabled === undefined) defaults.enabled = true;
    if (defaults.position === undefined) defaults.position = 'middle_center';
    return defaults;
}

// Open modal bindings
notesModal.onclick = (e) => {
    if (e.target === notesModal) closeModal();
};
const modalCloseBtn = document.getElementById('modal-close-btn');
const modalCancelBtn = document.getElementById('modal-cancel-btn');
if (modalCloseBtn) modalCloseBtn.onclick = closeModal;
if (modalCancelBtn) modalCancelBtn.onclick = closeModal;

function closeModal() {
    notesModal.classList.remove('open');
    setTimeout(() => {
        notesModal.style.display = 'none';
    }, 250);
}

// Bind search input to re-render lists live
moduleSearchInput.oninput = () => {
    renderModules();
};

async function loadDiskUsage() {
    const summaryEl = document.getElementById('disk-usage-summary');
    const fillEl = document.getElementById('disk-usage-fill');
    const warningEl = document.getElementById('disk-usage-warning');
    if (!summaryEl || !fillEl || !warningEl) return;

    try {
        const res = await fetch('/admin/disk-usage', { headers: authHeaders() });
        if (res.ok) {
            const data = await res.json();
            const totalGB = (data.total_bytes / (1024 * 1024 * 1024)).toFixed(1);
            const usedGB = (data.used_bytes / (1024 * 1024 * 1024)).toFixed(1);
            const freeGB = (data.free_bytes / (1024 * 1024 * 1024)).toFixed(1);
            const percent = data.percent_used;

            summaryEl.textContent = `${usedGB} GB used of ${totalGB} GB (${percent}% used)`;
            fillEl.style.width = `${percent}%`;

            // If free space is less than 500 MB (500 * 1024 * 1024 bytes)
            const isLowSpace = data.free_bytes < (500 * 1024 * 1024);
            if (isLowSpace) {
                fillEl.classList.add('warning');
                warningEl.style.display = 'flex';
            } else {
                fillEl.classList.remove('warning');
                warningEl.style.display = 'none';
            }
        } else {
            summaryEl.textContent = 'Error loading storage stats';
        }
    } catch (err) {
        console.warn('Failed to fetch disk usage:', err);
        summaryEl.textContent = 'Error loading storage stats';
    }
}

async function loadModules() {
    const authOk = await checkAuthStatus();
    if (!authOk) return;

    try {
        // Fetch config
        const configRes = await fetch('/admin/config', { headers: authHeaders() });
        if (configRes.ok) {
            currentConfig = await configRes.json();
            if (!currentConfig.modules) currentConfig.modules = {};
        }

        // Fetch installed modules list
        const modulesRes = await fetch('/admin/modules', { headers: authHeaders() });
        if (modulesRes.ok) {
            const mData = await modulesRes.json();
            installedModules = mData.modules;
        }

        // Fetch discoverable community modules list
        try {
            const communityRes = await fetch('/admin/community-modules', { headers: authHeaders() });
            if (communityRes.ok) {
                communityModules = await communityRes.json();
            }
        } catch (e) {
            console.warn('Failed to fetch community modules:', e);
        }

        // Fetch root disk usage
        try {
            await loadDiskUsage();
        } catch (e) {
            console.warn('Failed to load disk usage:', e);
        }

        renderModules();

        // Start checking updates in background once
        if (!updatesCheckStarted) {
            updatesCheckStarted = true;
            checkAllUpdates();
        }
    } catch (err) {
        showGlobal('Error loading modules: ' + err.message, 'error');
    }
}

function renderModules() {
    const query = moduleSearchInput.value.toLowerCase().trim();
    
    // 1. Render Installed Modules
    installedModulesContainer.innerHTML = '';
    const installedList = Object.entries(installedModules);
    
    const filteredInstalled = installedList.filter(([name, meta]) => {
        const title = meta.schema?.title || name;
        return name.toLowerCase().includes(query) || title.toLowerCase().includes(query);
    });

    if (filteredInstalled.length === 0) {
        installedModulesContainer.innerHTML = `<div class="no-modules-msg" style="text-align:center; color: var(--text-muted); padding:2rem;">No installed modules match your query.</div>`;
    } else {
        filteredInstalled.forEach(([name, meta]) => {
            const configKey = getConfiguredModuleKey(name);
            const isConfigured = configKey !== null;
            const isEnabled = isConfigured && currentConfig.modules[configKey].enabled !== false;
            const title = meta.schema?.title || name.replace("mirrordash-", "").replace("mirrordash_", "").replace("mymm-", "").replace("mymm_", "").split(/[-_]/).map(w => w ? w.charAt(0).toUpperCase() + w.slice(1) : '').join(' ');
            
            const card = document.createElement('div');
            card.className = 'module-card';
            card.id = `module-card-${name}`;
            if (isConfigured && !isEnabled) {
                card.classList.add('disabled-module');
            }
            
            // Build Status Badges
            let badgeHtml = '';
            if (isConfigured) {
                if (isEnabled) {
                    badgeHtml += `<span class="status-badge" style="background: rgba(16, 185, 129, 0.1); color: #10b981; border: 1px solid rgba(16,185,129,0.2);">Active on Mirror</span>`;
                } else {
                    badgeHtml += `<span class="status-badge" style="background: rgba(239, 68, 68, 0.1); color: #ef4444; border: 1px solid rgba(239,68,68,0.2);">Disabled</span>`;
                }
            } else {
                badgeHtml += `<span class="status-badge" style="background: rgba(255, 255, 255, 0.05); color: var(--text-muted); border: 1px solid rgba(255,255,255,0.1);">Inactive</span>`;
            }

            const updateInfo = pypiReleaseCache[name];
            let updateBadgeHtml = '';
            let upgradeActionBtnHtml = '';
            if (updateInfo && updateInfo.status === 'update_available') {
                updateBadgeHtml = `<span class="status-badge update-avail" style="margin-left: 8px;">Update Available (v${updateInfo.latestVersion})</span>`;
                upgradeActionBtnHtml = `
                    <button class="btn secondary btn-sm" onclick="viewReleaseNotes('${name}')" style="margin-right: 5px;">
                        <i class="fas fa-file-alt"></i> Notes
                    </button>
                    <button class="btn primary btn-sm" onclick="triggerModuleUpgrade('${name}')" style="margin-right: 5px;">
                        <i class="fas fa-arrow-alt-circle-up"></i> Upgrade
                    </button>
                `;
            }

            // Expand configuration button
            let configBtnText = isConfigured ? '<i class="fas fa-cog"></i> Configure' : '<i class="fas fa-plus"></i> Add to Mirror';
            let configBtnClass = isConfigured ? 'btn secondary btn-sm' : 'btn primary btn-sm';
            
            card.innerHTML = `
                <div class="module-card-header" style="display: flex; justify-content: space-between; align-items: flex-start; padding-bottom: 12px; border-bottom: 1px solid rgba(255,255,255,0.05);">
                    <div>
                        <h3 style="margin: 0; font-size: 1.15rem; color: white;">${title}</h3>
                        <span class="module-identifier" style="font-family: monospace; font-size: 0.8rem; color: var(--text-muted);">${name} <span style="margin-left:8px;">v${meta.version || '0.0.0'}</span></span>
                        <div style="display:flex; align-items:center; margin-top:6px; flex-wrap:wrap; gap:4px;">
                            ${badgeHtml}
                            ${updateBadgeHtml}
                        </div>
                    </div>
                    <div style="display: flex; gap: 8px; align-items: center;">
                        ${upgradeActionBtnHtml}
                        <button class="${configBtnClass}" onclick="toggleConfigureModule('${name}')" id="config-btn-${name}">
                            ${configBtnText}
                        </button>
                        <button class="btn danger btn-sm" onclick="triggerModuleUninstall('${name}')" title="Uninstall plugin package from mirror">
                            <i class="fas fa-trash-alt"></i> Uninstall
                        </button>
                    </div>
                </div>
                <div class="module-card-body" style="padding-top: 10px;">
                    <p style="margin: 0; font-size: 0.88rem; color: #a1a1aa;">${meta.schema?.description || 'No description available for this module.'}</p>
                </div>
                
                <!-- Expanding config drawer -->
                <div id="config-drawer-${name}" class="config-drawer" style="display: none; padding-top: 15px; border-top: 1px dashed #27272a; margin-top: 15px;">
                    <div id="config-fields-${name}">
                        <!-- Renders single module configuration forms -->
                    </div>
                </div>
            `;
            installedModulesContainer.appendChild(card);
        });
    }

    // 2. Render Discover Modules
    discoverModulesContainer.innerHTML = '';
    const discoverable = communityModules.filter(m => {
        const notInstalled = !installedModules[m.name];
        const matchesQuery = m.name.toLowerCase().includes(query) || m.title.toLowerCase().includes(query) || m.description.toLowerCase().includes(query);
        return notInstalled && matchesQuery;
    });

    if (discoverable.length === 0) {
        document.getElementById('discover-modules-section').style.display = 'none';
    } else {
        document.getElementById('discover-modules-section').style.display = 'block';
        discoverable.forEach(m => {
            const card = document.createElement('div');
            card.className = 'module-card';
            card.style.display = 'flex';
            card.style.flexDirection = 'column';
            card.style.justify = 'space-between';
            card.style.padding = '1rem';
            
            card.innerHTML = `
                <div>
                    <h4 style="margin: 0 0 4px 0; font-size: 1.05rem; color: white;">${m.title}</h4>
                    <span style="font-family: monospace; font-size: 0.75rem; color: var(--text-muted); display: block; margin-bottom: 8px;">${m.name}</span>
                    <p style="margin: 0; font-size: 0.82rem; color: #a1a1aa; line-height: 1.4;">${m.description}</p>
                </div>
                <div style="margin-top: 15px; display: flex; justify-content: flex-end; gap: 8px;">
                    <button class="btn secondary btn-sm" onclick="viewCommunityModuleDetails('${m.name}')">
                        <i class="fas fa-info-circle"></i> Details
                    </button>
                    <button class="btn primary btn-sm" onclick="installCommunityModule('${m.name}')">
                        <i class="fas fa-download"></i> Install
                    </button>
                </div>
            `;
            discoverModulesContainer.appendChild(card);
        });
    }
    
    if (typeof lucide !== 'undefined') {
        lucide.createIcons();
    }
}

window.toggleConfigureModule = function(moduleName) {
    const configKey = getConfiguredModuleKey(moduleName);
    const isConfigured = configKey !== null;
    if (!isConfigured) {
        // Add default values to config immediately
        const schema = getInstalledModuleMeta(moduleName)?.schema;
        const defaults = getModuleDefaultConfig(schema);
        const newKey = moduleName.replace(/_/g, '-');
        currentConfig.modules[newKey] = defaults;
        saveModuleConfigImmediately(moduleName, `Added ${getInstalledModuleMeta(moduleName)?.schema?.title || moduleName} to mirror.`);
        return;
    }

    const drawer = document.getElementById(`config-drawer-${moduleName}`);
    const btn = document.getElementById(`config-btn-${moduleName}`);
    
    if (drawer.style.display === 'none') {
        renderModuleConfig(moduleName);
        drawer.style.display = 'block';
        btn.innerHTML = '<i class="fas fa-chevron-up"></i> Hide';
    } else {
        drawer.style.display = 'none';
        btn.innerHTML = '<i class="fas fa-cog"></i> Configure';
    }
};

function renderModuleConfig(moduleName) {
    const container = document.getElementById(`config-fields-${moduleName}`);
    if (!container) return;
    
    container.innerHTML = '';
    
    const meta = getInstalledModuleMeta(moduleName);
    const schema = meta?.schema || {};
    const configKey = getConfiguredModuleKey(moduleName) || moduleName.replace(/_/g, '-');
    const moduleCfg = currentConfig.modules[configKey] || {};
    
    let formFieldsHtml = '';
    
    // Add standard checkbox and positions
    const enabledId = `field-${moduleName}-enabled`;
    const isEnabled = moduleCfg.enabled !== false;
    formFieldsHtml += `
        <div class="form-group toggle-group" style="margin-bottom: 12px;">
            <div class="form-label-desc">
                <label for="${enabledId}" style="font-weight:600; color: white;">Enabled</label>
                <p class="field-description" style="font-size:0.75rem; margin: 2px 0 0 0;">Display this module on the mirror screen.</p>
            </div>
            <label class="switch">
                <input type="checkbox" id="${enabledId}" data-key="enabled" ${isEnabled ? 'checked' : ''}>
                <span class="slider round"></span>
            </label>
        </div>
    `;

    const posId = `field-${moduleName}-position`;
    const posVal = moduleCfg.position || 'middle_center';
    const positions = ["top_bar", "top_left", "top_center", "top_right", "upper_third", "middle_left", "middle_center", "middle_right", "lower_third", "bottom_left", "bottom_center", "bottom_right", "bottom_bar"];
    let posOptions = '';
    positions.forEach(pos => {
        posOptions += `<option value="${pos}" ${posVal === pos ? 'selected' : ''}>${pos.replace('_', ' ').toUpperCase()}</option>`;
    });
    
    formFieldsHtml += `
        <div class="form-group" style="margin-bottom: 15px;">
            <div class="form-label-desc">
                <label for="${posId}" style="font-weight:600; color: white;">Screen Position</label>
                <p class="field-description" style="font-size:0.75rem; margin: 2px 0 0 0;">Dashboard layout region anchor.</p>
            </div>
            <select id="${posId}" data-key="position" class="form-control">
                ${posOptions}
            </select>
        </div>
    `;

    // Render schema properties
    if (schema.properties) {
        for (const [key, prop] of Object.entries(schema.properties)) {
            if (key === 'enabled' || key === 'position') continue;
            
            const val = moduleCfg[key] !== undefined ? moduleCfg[key] : (prop.default !== undefined ? prop.default : '');
            const fieldId = `field-${moduleName}-${key}`;
            
            if (prop.type === 'boolean') {
                formFieldsHtml += `
                    <div class="form-group toggle-group" style="margin-bottom: 12px;">
                        <div class="form-label-desc">
                            <label for="${fieldId}" style="font-weight:600; color: white;">${prop.title || key}</label>
                            <p class="field-description" style="font-size:0.75rem; margin: 2px 0 0 0;">${prop.description || ''}</p>
                        </div>
                        <label class="switch">
                            <input type="checkbox" id="${fieldId}" data-key="${key}" ${val ? 'checked' : ''}>
                            <span class="slider round"></span>
                        </label>
                    </div>
                `;
            } else if (prop.enum) {
                let optionsHtml = '';
                for (const opt of prop.enum) {
                    optionsHtml += `<option value="${opt}" ${val === opt ? 'selected' : ''}>${opt.replace('_', ' ').toUpperCase()}</option>`;
                }
                formFieldsHtml += `
                    <div class="form-group" style="margin-bottom: 12px;">
                        <div class="form-label-desc">
                            <label for="${fieldId}" style="font-weight:600; color: white;">${prop.title || key}</label>
                            <p class="field-description" style="font-size:0.75rem; margin: 2px 0 0 0;">${prop.description || ''}</p>
                        </div>
                        <select id="${fieldId}" data-key="${key}" class="form-control">
                            ${optionsHtml}
                        </select>
                    </div>
                `;
            } else if (prop.type === 'array' && prop.items && prop.items.type === 'object') {
                const items = Array.isArray(val) ? val : [];
                let itemsHtml = '';
                
                items.forEach((item, index) => {
                    let subFieldsHtml = '';
                    const subProperties = prop.items.properties || {};
                    
                    for (const [subKey, subProp] of Object.entries(subProperties)) {
                        const subVal = item[subKey] !== undefined ? item[subKey] : (subProp.default !== undefined ? subProp.default : '');
                        const subFieldId = `field-${moduleName}-${key}-${index}-${subKey}`;
                        
                        if (subKey === 'color') {
                            const colors = [
                                { name: "White", value: "#ffffff", hex: "#ffffff" },
                                { name: "Ice Blue", value: "var(--color-ice-blue)", hex: "#cceeff" },
                                { name: "Rose Pink", value: "var(--color-rose-pink)", hex: "#ffccd5" },
                                { name: "Green", value: "var(--color-status-online)", hex: "#a0ffba" },
                                { name: "Red", value: "var(--color-status-warning)", hex: "#f87171" },
                                { name: "Gray", value: "var(--color-standard-gray)", hex: "#999999" },
                                { name: "Charcoal", value: "var(--color-dimmed-charcoal)", hex: "#666666" }
                            ];
                            
                            let swatchesHtml = '';
                            colors.forEach(col => {
                                const isSelected = subVal === col.value;
                                swatchesHtml += `
                                    <button type="button" 
                                            class="color-swatch-btn ${isSelected ? 'active' : ''}" 
                                            style="width: 20px; height: 20px; border-radius: 50%; border: ${isSelected ? '2px solid white' : '1px solid #52525b'}; background-color: ${col.hex}; cursor: pointer; outline: none; transition: transform 0.1s; transform: ${isSelected ? 'scale(1.15)' : 'none'}; box-shadow: ${isSelected ? '0 0 8px white' : 'none'};" 
                                            title="${col.name}" 
                                            onclick="selectSubFieldColor(this, '${moduleName}', '${key}', ${index}, '${subKey}', '${col.value}')">
                                    </button>
                                `;
                            });
                            
                            subFieldsHtml += `
                                <div class="sub-form-group" style="margin-bottom: 8px; grid-column: span 2;">
                                    <label for="${subFieldId}" style="font-size: 0.8rem; color: #a1a1aa; display: block; margin-bottom: 6px;">${subProp.title || subKey}</label>
                                    <div style="display: flex; align-items: center; gap: 10px;">
                                        <div style="display: flex; gap: 6px; flex-wrap: wrap; align-items: center;">
                                            ${swatchesHtml}
                                        </div>
                                        <input type="text" id="${subFieldId}" data-module="${moduleName}" data-key="${key}" data-index="${index}" data-subkey="${subKey}" data-array-sub="true" value="${subVal}" class="form-control form-control-sm" style="font-size: 0.8rem; padding: 2px 6px; width: 120px; background: #09090b; border: 1px solid #27272a; color: white;" oninput="syncSubFieldToConfig(this)">
                                    </div>
                                </div>
                            `;
                        } else if (subKey === 'icon') {
                            const icons = [
                                "calendar", "clock", "users", "briefcase", "home", "heart", "gift", "trophy", 
                                "music", "plane", "shopping-cart", "utensils", "alert-circle", "book-open", "coffee", "film"
                            ];
                            
                            let iconsGridHtml = '';
                            icons.forEach(ic => {
                                const isSelected = subVal === ic;
                                iconsGridHtml += `
                                    <button type="button" 
                                            class="icon-picker-btn ${isSelected ? 'active' : ''}" 
                                            style="width: 28px; height: 28px; display: inline-flex; align-items: center; justify-content: center; background: ${isSelected ? '#3f3f46' : '#09090b'}; border: 1px solid ${isSelected ? 'white' : '#27272a'}; border-radius: 4px; color: ${isSelected ? 'white' : '#a1a1aa'}; cursor: pointer; outline: none; transition: background 0.1s;" 
                                            title="${ic}" 
                                            onclick="selectSubFieldIcon(this, '${moduleName}', '${key}', ${index}, '${subKey}', '${ic}')">
                                        <i data-lucide="${ic}" style="width: 14px; height: 14px; stroke-width: 2px;"></i>
                                    </button>
                                `;
                            });
                            
                            subFieldsHtml += `
                                <div class="sub-form-group" style="margin-bottom: 8px; grid-column: span 2;">
                                    <label for="${subFieldId}" style="font-size: 0.8rem; color: #a1a1aa; display: block; margin-bottom: 6px;">${subProp.title || subKey}</label>
                                    <div style="display: flex; align-items: flex-start; gap: 10px; flex-direction: column;">
                                        <div style="display: grid; grid-template-columns: repeat(8, 1fr); gap: 6px; width: 100%;">
                                            ${iconsGridHtml}
                                        </div>
                                        <div style="display: flex; align-items: center; gap: 8px; width: 100%;">
                                            <span style="font-size: 0.75rem; color: #71717a;">Custom Name:</span>
                                            <input type="text" id="${subFieldId}" data-module="${moduleName}" data-key="${key}" data-index="${index}" data-subkey="${subKey}" data-array-sub="true" value="${subVal}" class="form-control form-control-sm" style="font-size: 0.8rem; padding: 2px 6px; flex-grow: 1; background: #09090b; border: 1px solid #27272a; color: white;" oninput="syncSubFieldToConfig(this)">
                                        </div>
                                    </div>
                                </div>
                            `;
                        } else {
                            subFieldsHtml += `
                                <div class="sub-form-group" style="margin-bottom: 8px;">
                                    <label for="${subFieldId}" style="font-size: 0.8rem; color: #a1a1aa; display: block; margin-bottom: 4px;">${subProp.title || subKey}</label>
                                    <input type="text" id="${subFieldId}" data-module="${moduleName}" data-key="${key}" data-index="${index}" data-subkey="${subKey}" data-array-sub="true" value="${subVal}" class="form-control form-control-sm" style="font-size: 0.85rem; padding: 4px 8px; background: #09090b; border: 1px solid #27272a; color: white;" oninput="syncSubFieldToConfig(this)">
                                </div>
                            `;
                        }
                    }
                    
                    itemsHtml += `
                        <div class="array-item-card" style="border: 1px solid #3f3f46; border-radius: 6px; padding: 12px; margin-bottom: 12px; position: relative; background: #18181b;">
                            <button type="button" class="btn danger btn-sm" style="position: absolute; top: 8px; right: 8px; padding: 2px 6px; font-size: 0.75rem;" onclick="removeArrayItem('${moduleName}', '${key}', ${index})">
                                <i class="fas fa-trash"></i>
                            </button>
                            <div style="font-size: 0.8rem; font-weight: 600; color: #e4e4e7; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.05em;">#${index + 1}: ${item.name || 'Item'}</div>
                            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                                ${subFieldsHtml}
                            </div>
                        </div>
                    `;
                });
                
                formFieldsHtml += `
                    <div class="form-group" style="border-left: 2px solid #52525b; padding-left: 12px; margin-top: 15px; margin-bottom: 15px;">
                        <div class="form-label-desc" style="margin-bottom: 10px;">
                            <label style="font-weight: 600; font-size: 0.95rem; color: white;">${prop.title || key}</label>
                            <p class="field-description" style="font-size: 0.75rem; color: #a1a1aa; margin: 2px 0 0 0;">${prop.description || ''}</p>
                        </div>
                        <div class="array-items-container">
                            ${itemsHtml}
                        </div>
                        <button type="button" class="btn secondary btn-sm" onclick="addArrayItem('${moduleName}', '${key}', '${prop.items.title || 'Item'}')">
                            <i class="fas fa-plus"></i> Add ${prop.items.title || 'Item'}
                        </button>
                    </div>
                `;
            } else if (prop.type === 'integer' || prop.type === 'number') {
                formFieldsHtml += `
                    <div class="form-group" style="margin-bottom: 12px;">
                        <div class="form-label-desc">
                            <label for="${fieldId}" style="font-weight:600; color: white;">${prop.title || key}</label>
                            <p class="field-description" style="font-size:0.75rem; margin: 2px 0 0 0;">${prop.description || ''}</p>
                        </div>
                        <input type="number" id="${fieldId}" data-key="${key}" value="${val}" class="form-control">
                    </div>
                `;
            } else {
                formFieldsHtml += `
                    <div class="form-group" style="margin-bottom: 12px;">
                        <div class="form-label-desc">
                            <label for="${fieldId}" style="font-weight:600; color: white;">${prop.title || key}</label>
                            <p class="field-description" style="font-size:0.75rem; margin: 2px 0 0 0;">${prop.description || ''}</p>
                        </div>
                        <input type="text" id="${fieldId}" data-key="${key}" value="${val}" class="form-control">
                    </div>
                `;
            }
        }
    }
    
    container.innerHTML = `
        <div style="background: rgba(255,255,255,0.02); padding: 1.25rem; border-radius: 6px; border: 1px solid #27272a;">
            <h4 style="margin: 0 0 15px 0; color: white; font-size: 1rem;"><i class="fas fa-sliders-h" style="margin-right: 6px; color: var(--accent-color);"></i>Configuration Parameters</h4>
            ${formFieldsHtml}
            
            <div style="margin-top: 20px; display: flex; gap: 10px; justify-content: flex-end;">
                <button class="btn danger btn-sm" onclick="removeModuleConfig('${moduleName}')">
                    <i class="fas fa-times"></i> Remove from Mirror
                </button>
                <button class="btn primary btn-sm" id="save-module-btn-${moduleName}" onclick="saveModuleConfig('${moduleName}')">
                    <i class="fas fa-save"></i> Save Configuration
                </button>
            </div>
        </div>
    `;
    
    if (typeof lucide !== 'undefined') {
        lucide.createIcons();
    }
}

window.saveModuleConfig = async function(moduleName) {
    const container = document.getElementById(`config-fields-${moduleName}`);
    if (!container) return;
    
    const inputs = container.querySelectorAll('[data-key]');
    const configKey = getConfiguredModuleKey(moduleName) || moduleName.replace(/_/g, '-');
    if (!currentConfig.modules[configKey]) {
        currentConfig.modules[configKey] = {};
    }
    
    inputs.forEach(input => {
        if (input.dataset.arraySub) return;
        
        const key = input.dataset.key;
        const schema = getInstalledModuleMeta(moduleName)?.schema;
        const propSchema = schema?.properties?.[key];
        
        let val;
        if (input.type === 'checkbox') {
            val = input.checked;
        } else if (input.type === 'number') {
            val = input.value === '' ? '' : Number(input.value);
        } else {
            val = input.value;
        }
        
        if (propSchema) {
            if (propSchema.type === 'integer' && typeof val === 'number') {
                val = Math.round(val);
            }
        }
        currentConfig.modules[configKey][key] = val;
    });
    
    await saveModuleConfigImmediately(moduleName, 'Module configuration saved.');
};

window.removeModuleConfig = function(moduleName) {
    if (!confirm('Are you sure you want to deactivate and remove this module from the mirror screen?')) return;
    const configKey = getConfiguredModuleKey(moduleName) || moduleName.replace(/_/g, '-');
    delete currentConfig.modules[configKey];
    saveModuleConfigImmediately(moduleName, 'Module removed from mirror display.');
};

async function saveModuleConfigImmediately(moduleName, successMsg) {
    const saveBtn = document.getElementById(`save-module-btn-${moduleName}`);
    let origText = '';
    if (saveBtn) {
        origText = saveBtn.innerHTML;
        saveBtn.disabled = true;
        saveBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';
    }
    
    try {
        const res = await fetch('/admin/config', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify(currentConfig)
        });
        const result = await res.json();
        if (res.ok) {
            showGlobal(successMsg, 'success');
            loadModules();
        } else {
            showGlobal('Error saving config: ' + (result.detail ?? result.message), 'error');
        }
    } catch (err) {
        showGlobal('Network error saving config: ' + err.message, 'error');
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.innerHTML = origText;
        }
    }
}

// Scoped array handlers
window.syncSubFieldToConfig = function(input) {
    const moduleName = input.dataset.module;
    const key = input.dataset.key;
    const index = parseInt(input.dataset.index);
    const subKey = input.dataset.subkey;
    
    const configKey = getConfiguredModuleKey(moduleName) || moduleName.replace(/_/g, '-');
    if (currentConfig.modules[configKey] && currentConfig.modules[configKey][key]) {
        const items = currentConfig.modules[configKey][key];
        if (items[index]) {
            items[index][subKey] = input.value;
            if (subKey === 'name') {
                const headerEl = input.closest('.array-item-card').querySelector('div');
                if (headerEl) {
                    headerEl.textContent = `#${index + 1}: ${input.value || 'Item'}`;
                }
            }
        }
    }
};

window.addArrayItem = function(moduleName, key, itemTitle) {
    const configKey = getConfiguredModuleKey(moduleName) || moduleName.replace(/_/g, '-');
    if (!currentConfig.modules[configKey]) return;
    if (!currentConfig.modules[configKey][key]) {
        currentConfig.modules[configKey][key] = [];
    }
    
    const meta = getInstalledModuleMeta(moduleName);
    const propSchema = meta?.schema?.properties?.[key];
    const subProperties = propSchema?.items?.properties || {};
    
    const newItem = {};
    for (const [subKey, subProp] of Object.entries(subProperties)) {
        newItem[subKey] = subProp.default !== undefined ? subProp.default : '';
    }
    
    currentConfig.modules[configKey][key].push(newItem);
    renderModuleConfig(moduleName);
};

window.removeArrayItem = function(moduleName, key, index) {
    const configKey = getConfiguredModuleKey(moduleName) || moduleName.replace(/_/g, '-');
    if (currentConfig.modules[configKey] && currentConfig.modules[configKey][key]) {
        currentConfig.modules[configKey][key].splice(index, 1);
        renderModuleConfig(moduleName);
    }
};

window.selectSubFieldColor = function(btn, moduleName, key, index, subKey, value) {
    const cardEl = btn.closest('.array-item-card');
    const inputId = `field-${moduleName}-${key}-${index}-${subKey}`;
    const inputEl = document.getElementById(inputId);
    if (inputEl) {
        inputEl.value = value;
        syncSubFieldToConfig(inputEl);
    }
    
    const swatches = cardEl.querySelectorAll('.color-swatch-btn');
    swatches.forEach(sw => {
        const isSelected = sw === btn;
        sw.classList.toggle('active', isSelected);
        sw.style.border = isSelected ? '2px solid white' : '1px solid #52525b';
        sw.style.transform = isSelected ? 'scale(1.15)' : 'none';
        sw.style.boxShadow = isSelected ? '0 0 8px white' : 'none';
    });
};

window.selectSubFieldIcon = function(btn, moduleName, key, index, subKey, value) {
    const cardEl = btn.closest('.array-item-card');
    const inputId = `field-${moduleName}-${key}-${index}-${subKey}`;
    const inputEl = document.getElementById(inputId);
    if (inputEl) {
        inputEl.value = value;
        syncSubFieldToConfig(inputEl);
    }
    
    const btns = cardEl.querySelectorAll('.icon-picker-btn');
    btns.forEach(b => {
        const isSelected = b === btn;
        b.classList.toggle('active', isSelected);
        b.style.background = isSelected ? '#3f3f46' : '#09090b';
        b.style.border = isSelected ? '1px solid white' : '1px solid #27272a';
        b.style.color = isSelected ? 'white' : '#a1a1aa';
    });
};

// Install, Uninstall, Upgrade Actions
window.installCommunityModule = async function(packageName) {
    customPackageName.value = packageName;
    triggerManualInstall();
};

customInstallBtn.onclick = triggerManualInstall;
customPackageName.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') triggerManualInstall();
});

async function triggerManualInstall() {
    const pkg = customPackageName.value.trim();
    if (!pkg) return;

    customInstallStatus.textContent = `Installing module '${pkg}'... Please wait.`;
    customInstallStatus.className = 'status-msg loading';
    setLoading(customInstallBtn, customInstallBtnLabel, true, 'Install', 'Installing…');

    try {
        const res = await fetch('/admin/install', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({ package_name: pkg })
        });
        const result = await res.json();
        if (res.ok) {
            customInstallStatus.textContent = result.message;
            customInstallStatus.className = 'status-msg success';
            setTimeout(loadModules, 1000);
        } else {
            customInstallStatus.textContent = 'Error: ' + (result.detail ?? result.message);
            customInstallStatus.className = 'status-msg error';
        }
    } catch (err) {
        customInstallStatus.textContent = 'Network error: ' + err.message;
        customInstallStatus.className = 'status-msg error';
    } finally {
        setLoading(customInstallBtn, customInstallBtnLabel, false, 'Install', 'Installing…');
    }
}

window.triggerModuleUninstall = async function(moduleName) {
    const meta = getInstalledModuleMeta(moduleName);
    const packageName = meta ? meta.package_name : moduleName;

    if (!confirm(`Are you sure you want to completely uninstall the package '${packageName}'? This will delete the plugin from the system and restart the server.`)) return;

    showGlobal(`Uninstalling ${packageName}... Please wait.`, 'info');
    try {
        const res = await fetch('/admin/uninstall', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({ package_name: packageName })
        });
        const result = await res.json();
        
        if (res.ok) {
            showGlobal(result.message, 'success');
            restartBtn.disabled = true;
            restartBtn.textContent = 'Restarting…';
            
            const pollStart = Date.now();
            const poll = setInterval(async () => {
                if (Date.now() - pollStart > 30000) { 
                    clearInterval(poll); 
                    showGlobal('Server did not respond after 30s.', 'error'); 
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
            showGlobal('Error uninstalling: ' + (result.detail ?? result.message), 'error');
        }
    } catch (err) {
        showGlobal('Network error uninstalling: ' + err.message, 'error');
    }
};

window.triggerModuleUpgrade = async function(moduleName) {
    const updateInfo = pypiReleaseCache[moduleName];
    const packageName = updateInfo ? updateInfo.packageName : (getInstalledModuleMeta(moduleName)?.package_name || moduleName);
    
    if (!confirm(`Are you sure you want to upgrade the package ${packageName}? This will restart the system.`)) return;
    
    showGlobal(`Starting upgrade for ${packageName}... Please wait.`, 'info');
    
    try {
        const res = await fetch('/admin/update', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({ package_name: packageName })
        });
        const result = await res.json();
        
        if (res.ok) {
            showGlobal(result.message, 'success');
            restartBtn.disabled = true;
            restartBtn.textContent = 'Restarting…';
            
            const pollStart = Date.now();
            const poll = setInterval(async () => {
                if (Date.now() - pollStart > 30000) { 
                    clearInterval(poll); 
                    showGlobal('Server did not respond after 30s.', 'error'); 
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
            showGlobal('Error upgrading: ' + (result.detail ?? result.message), 'error');
        }
    } catch (err) {
        showGlobal('Network error upgrading: ' + err.message, 'error');
    }
};

// PyPI Release Check logic
async function checkAllUpdates() {
    const promises = [];
    for (const [name, meta] of Object.entries(installedModules)) {
        if (meta.installed && meta.package_name) {
            promises.push(checkModuleUpdates(name, meta.package_name, meta.version));
        }
    }
    await Promise.all(promises);
    updateCountBadge();
    if (pagePanelModules.style.display === 'block') {
        renderModules();
    }
}

async function checkModuleUpdates(moduleName, packageName, currentVersion) {
    try {
        const res = await fetch(`https://pypi.org/pypi/${packageName}/json`);
        if (!res.ok) {
            pypiReleaseCache[moduleName] = {
                latestVersion: currentVersion,
                packageName: packageName,
                notes: "",
                status: 'local'
            };
            return;
        }
        
        const data = await res.json();
        const latestVersion = data.info?.version || currentVersion;
        const releaseNotes = data.info?.description || "No release notes available.";
        
        let homepageUrl = "";
        if (data.info?.project_urls) {
            homepageUrl = data.info.project_urls.Source || data.info.project_urls.Homepage || data.info.project_urls["Code"] || "";
        }
        if (!homepageUrl && data.info?.home_page) {
            homepageUrl = data.info.home_page;
        }

        const isNewer = isNewerVersion(currentVersion, latestVersion);
        
        pypiReleaseCache[moduleName] = {
            latestVersion: latestVersion,
            packageName: packageName,
            notes: releaseNotes,
            homepageUrl: homepageUrl,
            status: isNewer ? 'update_available' : 'up_to_date'
        };
    } catch (e) {
        console.warn(`Failed to check updates for ${packageName}:`, e);
        pypiReleaseCache[moduleName] = {
            latestVersion: currentVersion,
            packageName: packageName,
            notes: "",
            status: 'error'
        };
    }
}

function isNewerVersion(current, latest) {
    try {
        const cParts = current.split('.').map(Number);
        const lParts = latest.split('.').map(Number);
        for (let i = 0; i < Math.max(cParts.length, lParts.length); i++) {
            const c = cParts[i] || 0;
            const l = lParts[i] || 0;
            if (l > c) return true;
            if (c > l) return false;
        }
        return false;
    } catch (e) {
        return current !== latest;
    }
}

function updateCountBadge() {
    let count = 0;
    for (const info of Object.values(pypiReleaseCache)) {
        if (info.status === 'update_available') {
            count++;
        }
    }
    if (count > 0) {
        updatesCountBadge.textContent = count;
        updatesCountBadge.style.display = 'inline-block';
    } else {
        updatesCountBadge.style.display = 'none';
    }
}

function makeRelativeImagesAbsolute(descriptionHtml, homepageUrl) {
    if (!homepageUrl || !homepageUrl.includes("github.com")) {
        return descriptionHtml;
    }

    // Convert github.com/user/repo to raw.githubusercontent.com/user/repo/main/
    const rawBaseUrl = homepageUrl
        .replace("github.com", "raw.githubusercontent.com")
        .replace(/\/+$/, "") + "/main/"; 

    const parser = new DOMParser();
    const doc = parser.parseFromString(descriptionHtml, 'text/html');
    const images = doc.querySelectorAll('img');

    images.forEach(img => {
        const src = img.getAttribute('src');
        if (src && !src.startsWith('http://') && !src.startsWith('https://')) {
            const cleanSrc = src.replace(/^\.\//, ''); // Remove leading ./
            img.setAttribute('src', rawBaseUrl + cleanSrc);
            
            // Failsafe Branch Fallback
            img.setAttribute('onerror', `
                this.onerror = null; 
                if (this.src.includes('/main/')) { 
                    this.src = this.src.replace('/main/', '/master/'); 
                }
            `);
        }
    });

    return doc.body.innerHTML;
}

window.viewCommunityModuleDetails = async function(moduleName) {
    showGlobal(`Loading details for ${moduleName}...`, 'info');
    try {
        const res = await fetch(`https://pypi.org/pypi/${moduleName}/json`);
        if (!res.ok) {
            throw new Error(`Failed to fetch module details from PyPI: ${res.statusText}`);
        }
        const data = await res.json();
        
        const latestVersion = data.info?.version || "0.0.0";
        const title = data.info?.summary || moduleName;
        const rawNotes = data.info?.description || "No description available.";
        
        let homepageUrl = "";
        if (data.info?.project_urls) {
            homepageUrl = data.info.project_urls.Source || data.info.project_urls.Homepage || data.info.project_urls["Code"] || "";
        }
        if (!homepageUrl && data.info?.home_page) {
            homepageUrl = data.info.home_page;
        }

        modalTitle.innerHTML = `<i class="fas fa-puzzle-piece"></i> ${moduleName}`;
        modalSubtitle.textContent = `Community Module | Version v${latestVersion}`;
        
        let notesHtml = rawNotes;
        if (window.marked && window.DOMPurify) {
            const renderedHtml = marked.parse(rawNotes);
            notesHtml = DOMPurify.sanitize(renderedHtml);
        } else {
            notesHtml = `<pre style="white-space: pre-wrap; font-family: inherit;">${rawNotes}</pre>`;
        }
        
        if (homepageUrl) {
            notesHtml = makeRelativeImagesAbsolute(notesHtml, homepageUrl);
        }
        
        modalBody.innerHTML = notesHtml;
        
        modalUpdateBtn.innerHTML = '<i class="fas fa-download"></i> Install Module';
        modalUpdateBtn.className = 'btn primary';
        modalUpdateBtn.onclick = () => {
            closeModal();
            installCommunityModule(moduleName);
        };
        modalUpdateBtn.style.display = 'inline-block';
        
        notesModal.style.display = 'flex';
        notesModal.offsetHeight;
        notesModal.classList.add('open');
    } catch (err) {
        showGlobal(`Error loading module details: ${err.message}`, 'error');
    }
};

window.viewReleaseNotes = function(moduleName) {
    const updateInfo = pypiReleaseCache[moduleName];
    if (!updateInfo) return;
    
    const title = getInstalledModuleMeta(moduleName)?.schema?.title || moduleName;
    modalTitle.innerHTML = `<i class="fas fa-file-alt"></i> ${title} Release Notes`;
    modalSubtitle.textContent = `${updateInfo.packageName} v${updateInfo.latestVersion}`;
    
    let html = '';
    if (window.marked && window.DOMPurify) {
        html = DOMPurify.sanitize(marked.parse(updateInfo.notes));
    } else {
        html = `<pre style="white-space: pre-wrap; font-family: inherit;">${updateInfo.notes}</pre>`;
    }
    
    if (updateInfo.homepageUrl) {
        html = makeRelativeImagesAbsolute(html, updateInfo.homepageUrl);
    }
    
    modalBody.innerHTML = html;
    
    modalUpdateBtn.innerHTML = '<i class="fas fa-arrow-alt-circle-up"></i> Upgrade';
    modalUpdateBtn.className = 'btn primary';
    modalUpdateBtn.style.display = 'inline-block';
    
    modalUpdateBtn.onclick = () => {
        closeModal();
        triggerModuleUpgrade(moduleName);
    };
    
    notesModal.style.display = 'flex';
    notesModal.offsetHeight;
    notesModal.classList.add('open');
};

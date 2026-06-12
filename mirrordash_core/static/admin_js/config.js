const configEditor      = document.getElementById('config-editor');
const saveBtn           = document.getElementById('save-config-btn');
const saveBtnLabel      = document.getElementById('save-btn-label');
const visualForm        = document.getElementById('visual-form-container');

let globalsSchema = null;

// Config Tab Switching
const tabVisual = document.getElementById('tab-visual');
const tabRaw    = document.getElementById('tab-raw');
const panelVisual = document.getElementById('panel-visual');
const panelRaw    = document.getElementById('panel-raw');

function initTabs() {
    tabVisual.onclick = () => {
        try {
            const parsedGlobals = JSON.parse(configEditor.value);
            currentConfig.globals = parsedGlobals;
            renderVisualEditor();
            
            tabVisual.classList.add('active');
            tabVisual.setAttribute('aria-selected', 'true');
            tabRaw.classList.remove('active');
            tabRaw.setAttribute('aria-selected', 'false');
            
            panelVisual.style.display = 'block';
            panelRaw.style.display = 'none';
        } catch (err) {
            showGlobal('Cannot switch: Raw JSON is invalid. Please fix syntax errors first.', 'error');
        }
    };

    tabRaw.onclick = () => {
        tabRaw.classList.add('active');
        tabRaw.setAttribute('aria-selected', 'true');
        tabVisual.classList.remove('active');
        tabVisual.setAttribute('aria-selected', 'false');
        
        panelRaw.style.display = 'block';
        panelVisual.style.display = 'none';
    };
}

function renderVisualEditor() {
    try {
        visualForm.innerHTML = '';
        
        // Globals Schema definition for the Visual Editor
        if (!globalsSchema) {
            globalsSchema = {
                title: "Global Settings",
                description: "System-wide preferences inherited by all modules.",
                properties: {
                    language: { type: "string", default: "en", title: "System Language", description: "Language for translations (e.g. en, sv, de, fr, nl)." },
                    timezone: { type: "string", default: "Europe/Stockholm", title: "Timezone", description: "System timezone (e.g. Europe/Stockholm, America/New_York)." },
                    time_format: { type: "string", default: "24h", enum: ["24h", "12h"], title: "Clock Time Format", description: "Global standard for clocks and times." },
                    temperature_unit: { type: "string", default: "C", enum: ["C", "F"], title: "Temperature Unit", description: "Unit for thermometer and weather readouts." },
                    distance_unit: { type: "string", default: "km", enum: ["km", "miles"], title: "Distance Unit", description: "Unit for travel, range, and maps." },
                    latitude: { type: "number", default: 59.3293, title: "Decimal Latitude", description: "Latitude coordinates for weather/astronomy." },
                    longitude: { type: "number", default: 18.0686, title: "Decimal Longitude", description: "Longitude coordinates for weather/astronomy." }
                }
            };
        }

        // Render Globals Card
        if (currentConfig.globals) {
            const card = document.createElement('div');
            card.className = 'module-card global-card';
            card.style.borderLeft = '4px solid var(--accent-color, #3b82f6)';
            
            let formFieldsHtml = '';
            for (const [key, prop] of Object.entries(globalsSchema.properties)) {
                const val = currentConfig.globals[key] !== undefined ? currentConfig.globals[key] : (prop.default !== undefined ? prop.default : '');
                const fieldId = `field-globals-${key}`;
                
                if (prop.type === 'boolean') {
                    formFieldsHtml += `
                        <div class="form-group toggle-group">
                            <div class="form-label-desc">
                                <label for="${fieldId}">${prop.title || key}</label>
                                <p class="field-description">${prop.description || ''}</p>
                            </div>
                            <label class="switch">
                                <input type="checkbox" id="${fieldId}" data-module="globals" data-key="${key}" ${val ? 'checked' : ''} onchange="syncFormToConfig()">
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
                        <div class="form-group">
                            <div class="form-label-desc">
                                <label for="${fieldId}">${prop.title || key}</label>
                                <p class="field-description">${prop.description || ''}</p>
                            </div>
                            <select id="${fieldId}" data-module="globals" data-key="${key}" class="form-control" onchange="syncFormToConfig()">
                                ${optionsHtml}
                            </select>
                        </div>
                    `;
                } else if (prop.type === 'number') {
                    formFieldsHtml += `
                        <div class="form-group">
                            <div class="form-label-desc">
                                <label for="${fieldId}">${prop.title || key}</label>
                                <p class="field-description">${prop.description || ''}</p>
                            </div>
                            <input type="number" step="any" id="${fieldId}" data-module="globals" data-key="${key}" value="${val}" class="form-control" oninput="syncFormToConfig()">
                        </div>
                    `;
                } else {
                    formFieldsHtml += `
                        <div class="form-group">
                            <div class="form-label-desc">
                                <label for="${fieldId}">${prop.title || key}</label>
                                <p class="field-description">${prop.description || ''}</p>
                            </div>
                            <input type="text" id="${fieldId}" data-module="globals" data-key="${key}" value="${val}" class="form-control" oninput="syncFormToConfig()">
                        </div>
                    `;
                }
            }
            
            card.innerHTML = `
                <div class="module-card-header">
                    <div class="module-title-desc">
                        <h3><i class="fas fa-globe" aria-hidden="true" style="margin-right:8px; color: var(--accent-color, #3b82f6);"></i>${globalsSchema.title}</h3>
                        <p class="module-card-description">${globalsSchema.description}</p>
                    </div>
                </div>
                <div class="module-card-body">
                    ${formFieldsHtml}
                </div>
            `;
            visualForm.appendChild(card);
        } else {
            visualForm.innerHTML = '<div class="no-modules-msg">Global configuration is missing.</div>';
        }
    } catch (err) {
        console.error("Error rendering visual global editor:", err);
        visualForm.innerHTML = `<div class="status-msg error" style="margin:0;">Error rendering global settings: ${err.message}. Please use Raw JSON editor.</div>`;
    }
}

window.syncFormToConfig = function() {
    const inputs = visualForm.querySelectorAll('[data-module]');
    inputs.forEach(input => {
        const moduleName = input.dataset.module;
        const key = input.dataset.key;
        
        if (moduleName === 'globals') {
            if (!currentConfig.globals) currentConfig.globals = {};
            let val;
            if (input.type === 'checkbox') {
                val = input.checked;
            } else if (input.type === 'number') {
                val = input.value === '' ? '' : Number(input.value);
            } else {
                val = input.value;
            }
            currentConfig.globals[key] = val;
        }
    });
    
    configEditor.value = JSON.stringify(currentConfig.globals, null, 2);
};

async function loadConfig() {
    const authOk = await checkAuthStatus();
    if (!authOk) return;

    try {
        const res = await fetch('/admin/config', { headers: authHeaders() });
        if (res.status === 401) { 
            localStorage.removeItem('mirrordash_api_key');
            localStorage.removeItem('mymm_api_key');
            currentApiKey = '';
            showGlobal('Invalid password — clear storage and reload.', 'error');
            alert('Invalid password. Please reload the page to try again.');
            return; 
        }
        if (res.status === 403) {
            showGlobal('Password not set. Please complete setup.', 'error');
            return;
        }
        if (!res.ok) { showGlobal('Failed to load configuration.', 'error'); return; }
        
        const config = await res.json();
        currentConfig = config;
        if (!currentConfig.globals) currentConfig.globals = {};
        if (!currentConfig.modules) currentConfig.modules = {};
        
        // Fetch globals schema dynamically
        try {
            const schemaRes = await fetch('/admin/globals-schema', { headers: authHeaders() });
            if (schemaRes.ok) {
                globalsSchema = await schemaRes.json();
            }
        } catch (e) {
            console.warn('Failed to fetch globals schema:', e);
        }

        configEditor.value = JSON.stringify(currentConfig.globals, null, 2);
        renderVisualEditor();
    } catch (err) {
        showGlobal('Network error loading configuration: ' + err.message, 'error');
    }
}

saveBtn.onclick = async () => {
    let parsedGlobals;
    try {
        parsedGlobals = JSON.parse(configEditor.value);
    } catch (err) {
        showGlobal('Invalid JSON: ' + err.message, 'error');
        return;
    }
    
    currentConfig.globals = parsedGlobals;
    
    setLoading(saveBtn, saveBtnLabel, true, 'Save Settings', 'Saving…');
    try {
        const res = await fetch('/admin/config', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify(currentConfig)
        });
        const result = await res.json();
        if (res.ok) {
            showGlobal('Global settings saved successfully.', 'success');
            renderVisualEditor();
        } else {
            showGlobal('Error: ' + (result.detail ?? result.message), 'error');
        }
    } catch (err) {
        showGlobal('Network error: ' + err.message, 'error');
    } finally {
        setLoading(saveBtn, saveBtnLabel, false, 'Save Settings', 'Saving…');
    }
};

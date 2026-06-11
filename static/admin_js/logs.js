const logTypeSelect      = document.getElementById('log-type-select');
const logModuleSelectContainer = document.getElementById('log-module-select-container');
const logModuleSelect          = document.getElementById('log-module-select');
const logLinesSelect     = document.getElementById('log-lines-select');
const refreshLogsBtn     = document.getElementById('refresh-logs-btn');
const copyLogsBtn        = document.getElementById('copy-logs-btn');
const logsViewer         = document.getElementById('logs-viewer');

logTypeSelect.onchange = loadLogs;
logModuleSelect.onchange = loadLogs;
logLinesSelect.onchange = loadLogs;
refreshLogsBtn.onclick = loadLogs;
copyLogsBtn.onclick = () => {
    navigator.clipboard.writeText(logsViewer.textContent)
        .then(() => showGlobal('Logs copied to clipboard.', 'success'))
        .catch(() => showGlobal('Failed to copy logs.', 'error'));
};

async function loadLogs() {
    logsViewer.textContent = 'Loading logs...';
    const logType = logTypeSelect.value;
    const logLines = logLinesSelect.value;
    
    if (logType === 'modules') {
        logModuleSelectContainer.style.display = 'inline-flex';
        const currentVal = logModuleSelect.value;
        logModuleSelect.innerHTML = '<option value="">All Modules</option>';
        for (const name of Object.keys(installedModules)) {
            const title = getInstalledModuleMeta(name)?.schema?.title || name;
            logModuleSelect.innerHTML += `<option value="${name}">${title}</option>`;
        }
        logModuleSelect.value = currentVal;
    } else {
        logModuleSelectContainer.style.display = 'none';
    }
    
    const selectedModule = (logType === 'modules') ? logModuleSelect.value : '';
    let url = `/admin/logs?type=${logType}&lines=${logLines}`;
    if (selectedModule) {
        url += `&module=${encodeURIComponent(selectedModule)}`;
    }

    try {
        const res = await fetch(url, {
            headers: authHeaders()
        });
        if (!res.ok) {
            logsViewer.textContent = 'Error loading logs from server.';
            return;
        }
        const data = await res.json();
        logsViewer.textContent = data.logs || 'No logs found.';
        
        // Auto scroll to bottom
        const container = logsViewer.parentElement;
        container.scrollTop = container.scrollHeight;
    } catch (err) {
        logsViewer.textContent = 'Failed to load logs: ' + err.message;
    }
}

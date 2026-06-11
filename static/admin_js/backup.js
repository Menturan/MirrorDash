let activeUploadFilename = '';
let activeValidationManifest = null;
let activeDecryptionPassword = '';
let isLocalValidation = false;

const backupEncryptToggle = document.getElementById('backup-encrypt-toggle');
const backupPasswordContainer = document.getElementById('backup-password-container');
const backupPasswordInput = document.getElementById('backup-password');
const createBackupBtn = document.getElementById('create-backup-btn');
const createBackupSpinner = document.getElementById('create-backup-spinner');
const createBackupIcon = document.getElementById('create-backup-icon');
const backupDropZone = document.getElementById('backup-drop-zone');
const backupFileInput = document.getElementById('backup-file-input');
const backupPasswordModal = document.getElementById('backup-password-modal');
const modalBackupPassword = document.getElementById('modal-backup-password');
const modalBackupPasswordError = document.getElementById('modal-backup-password-error');
const modalBackupPwdSubmitBtn = document.getElementById('modal-backup-pwd-submit-btn');
const modalBackupPwdCancelBtn = document.getElementById('modal-backup-pwd-cancel-btn');
const backupValidationPanel = document.getElementById('backup-validation-panel');
const valManifestVersion = document.getElementById('val-manifest-version');
const valTimestamp = document.getElementById('val-timestamp');
const valModulesCount = document.getElementById('val-modules-count');
const valModulesListUl = document.getElementById('val-modules-list-ul');
const backupProgressPanel = document.getElementById('backup-progress-panel');
const backupProgressLog = document.getElementById('backup-progress-log');
const runRestoreBtn = document.getElementById('run-restore-btn');
const cancelRestoreBtn = document.getElementById('cancel-restore-btn');

async function loadBackupsList() {
    const tbody = document.getElementById('backups-list-tbody');
    tbody.innerHTML = '<tr><td colspan="5" style="text-align: center;">Loading backups...</td></tr>';
    try {
        const res = await fetch('/admin/backup/list', { headers: authHeaders() });
        if (!res.ok) throw new Error('Failed to load backups list');
        const data = await res.json();
        tbody.innerHTML = '';
        if (data.backups.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: #999;">No backups saved.</td></tr>';
            return;
        }
        data.backups.forEach(backup => {
            const date = new Date(backup.created_at).toLocaleString();
            const size = (backup.size_bytes / 1024).toFixed(1) + ' KB';
            const isEnc = backup.encrypted 
                ? '<span class="status-badge" style="background-color: #ffb300; color: #000; padding: 2px 6px; border-radius: 4px; font-size: 0.8rem;"><i class="fas fa-lock"></i> Yes</span>' 
                : '<span style="color: #666;"><i class="fas fa-unlock"></i> No</span>';
            
            tbody.innerHTML += `
                <tr>
                    <td><strong>${backup.filename}</strong></td>
                    <td>${date}</td>
                    <td>${size}</td>
                    <td>${isEnc}</td>
                    <td style="text-align: right;">
                        <button class="btn secondary btn-sm" onclick="downloadBackup('${backup.filename}')" title="Download file"><i class="fas fa-download"></i></button>
                        <button class="btn primary btn-sm" onclick="triggerRestoreLocal('${backup.filename}', ${backup.encrypted})" title="Restore from local"><i class="fas fa-undo"></i> Restore</button>
                        <button class="btn btn-sm" style="background-color: #ff3333; color: white;" onclick="deleteBackup('${backup.filename}')" title="Delete"><i class="fas fa-trash"></i></button>
                    </td>
                </tr>
            `;
        });
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: #ff6666;">Error loading backups: ${err.message}</td></tr>`;
    }
}

window.downloadBackup = async function(filename) {
    try {
        showGlobal('Starting download...', 'info');
        const res = await fetch(`/admin/backup/download/${filename}`, {
            headers: authHeaders()
        });
        if (!res.ok) throw new Error('Download failed');
        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
        showGlobal('Download complete.', 'success');
    } catch (err) {
        showGlobal('Download error: ' + err.message, 'error');
    }
};

window.deleteBackup = async function(filename) {
    if (!confirm(`Are you sure you want to delete backup ${filename}?`)) return;
    try {
        const res = await fetch(`/admin/backup/delete/${filename}`, {
            method: 'DELETE',
            headers: authHeaders()
        });
        if (res.ok) {
            showGlobal('Backup deleted successfully.', 'success');
            loadBackupsList();
        } else {
            const data = await res.json();
            showGlobal('Failed to delete: ' + (data.detail || 'Unknown error'), 'error');
        }
    } catch (err) {
        showGlobal('Error: ' + err.message, 'error');
    }
};

window.triggerRestoreLocal = async function(filename, isEncrypted) {
    activeUploadFilename = filename;
    activeValidationManifest = null;
    activeDecryptionPassword = '';
    isLocalValidation = true;
    
    backupValidationPanel.style.display = 'none';
    backupProgressPanel.style.display = 'none';

    if (isEncrypted) {
        modalBackupPassword.value = '';
        modalBackupPasswordError.style.display = 'none';
        backupPasswordModal.style.display = 'flex';
    } else {
        showGlobal('Loading local backup details...', 'info');
        try {
            const res = await fetch('/admin/backup/validate-local', {
                method: 'POST',
                headers: { ...authHeaders(), 'Content-Type': 'application/json' },
                body: JSON.stringify({ filename })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Validation failed');
            showValidationSummary(data.manifest);
        } catch (err) {
            showGlobal('Validation error: ' + err.message, 'error');
        }
    }
};

backupEncryptToggle.onchange = () => {
    backupPasswordContainer.style.display = backupEncryptToggle.checked ? 'block' : 'none';
    if (!backupEncryptToggle.checked) {
        backupPasswordInput.value = '';
    }
};

createBackupBtn.onclick = async () => {
    if (backupEncryptToggle.checked && !backupPasswordInput.value) {
        alert('Please enter a password to encrypt the backup.');
        return;
    }
    createBackupBtn.disabled = true;
    createBackupSpinner.style.display = 'inline-block';
    createBackupIcon.style.display = 'none';
    try {
        const payload = {};
        if (backupEncryptToggle.checked) {
            payload.password = backupPasswordInput.value;
        }
        const res = await fetch('/admin/backup/create', {
            method: 'POST',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok) {
            showGlobal('Backup created successfully: ' + data.filename, 'success');
            backupPasswordInput.value = '';
            backupEncryptToggle.checked = false;
            backupPasswordContainer.style.display = 'none';
            loadBackupsList();
        } else {
            showGlobal('Failed to create backup: ' + (data.detail || 'Unknown error'), 'error');
        }
    } catch (err) {
        showGlobal('Network error: ' + err.message, 'error');
    } finally {
        createBackupBtn.disabled = false;
        createBackupSpinner.style.display = 'none';
        createBackupIcon.style.display = 'inline-block';
    }
};

backupDropZone.onclick = () => {
    backupFileInput.click();
};

backupFileInput.onchange = () => {
    if (backupFileInput.files.length > 0) {
        handleBackupUpload(backupFileInput.files[0]);
    }
};

backupDropZone.ondragover = (e) => {
    e.preventDefault();
    backupDropZone.classList.add('dragover');
};

backupDropZone.ondragleave = () => {
    backupDropZone.classList.remove('dragover');
};

backupDropZone.ondrop = (e) => {
    e.preventDefault();
    backupDropZone.classList.remove('dragover');
    if (e.dataTransfer.files.length > 0) {
        handleBackupUpload(e.dataTransfer.files[0]);
    }
};

async function handleBackupUpload(file) {
    showGlobal('Uploading backup for validation...', 'info');
    backupValidationPanel.style.display = 'none';
    backupProgressPanel.style.display = 'none';
    isLocalValidation = false;
    
    const formData = new FormData();
    formData.append('file', file);
    
    try {
        const res = await fetch('/admin/backup/upload', {
            method: 'POST',
            headers: { 'X-API-Key': currentApiKey },
            body: formData
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Upload failed');
        
        if (data.status === 'needs_password') {
            activeUploadFilename = data.filename;
            activeDecryptionPassword = '';
            modalBackupPassword.value = '';
            modalBackupPasswordError.style.display = 'none';
            backupPasswordModal.style.display = 'flex';
        } else if (data.status === 'ready') {
            showValidationSummary(data.manifest);
        }
    } catch (err) {
        showGlobal('Upload error: ' + err.message, 'error');
    }
}

modalBackupPwdCancelBtn.onclick = () => {
    backupPasswordModal.style.display = 'none';
};

modalBackupPwdSubmitBtn.onclick = async () => {
    const pwd = modalBackupPassword.value;
    if (!pwd) {
        modalBackupPasswordError.textContent = 'Please enter a password.';
        modalBackupPasswordError.style.display = 'block';
        return;
    }
    modalBackupPwdSubmitBtn.disabled = true;
    try {
        let res, data;
        if (isLocalValidation) {
            res = await fetch('/admin/backup/validate-local', {
                method: 'POST',
                headers: { ...authHeaders(), 'Content-Type': 'application/json' },
                body: JSON.stringify({ filename: activeUploadFilename, password: pwd })
            });
        } else {
            res = await fetch('/admin/backup/validate-password', {
                method: 'POST',
                headers: { ...authHeaders(), 'Content-Type': 'application/json' },
                body: JSON.stringify({ filename: activeUploadFilename, password: pwd })
            });
        }
        data = await res.json();
        if (res.ok) {
            activeDecryptionPassword = pwd;
            backupPasswordModal.style.display = 'none';
            showValidationSummary(data.manifest);
        } else {
            modalBackupPasswordError.textContent = data.detail || 'Invalid password.';
            modalBackupPasswordError.style.display = 'block';
        }
    } catch (err) {
        modalBackupPasswordError.textContent = 'Error: ' + err.message;
        modalBackupPasswordError.style.display = 'block';
    } finally {
        modalBackupPwdSubmitBtn.disabled = false;
    }
};

function showValidationSummary(manifest) {
    activeValidationManifest = manifest;
    
    valManifestVersion.textContent = manifest.backup_version || '1.0';
    valTimestamp.textContent = new Date(manifest.timestamp).toLocaleString();
    valModulesCount.textContent = (manifest.modules || []).length;
    
    valModulesListUl.innerHTML = '';
    if (manifest.modules && manifest.modules.length > 0) {
        manifest.modules.forEach(mod => {
            const typeBadge = mod.type === 'local' 
                ? '<span style="color: #66ff66;">[Local Source]</span>' 
                : '<span style="color: #66b3ff;">[PyPI Package]</span>';
            valModulesListUl.innerHTML += `<li><strong>${mod.package_name}</strong> (v${mod.version}) - ${typeBadge}</li>`;
        });
    } else {
        valModulesListUl.innerHTML = '<li>No custom modules to restore.</li>';
    }
    
    backupValidationPanel.style.display = 'block';
    showGlobal('Backup verified. Ready to restore.', 'success');
}

cancelRestoreBtn.onclick = () => {
    backupValidationPanel.style.display = 'none';
    activeValidationManifest = null;
    activeDecryptionPassword = '';
};

runRestoreBtn.onclick = async () => {
    if (!confirm('Are you sure you want to proceed with the restoration? This will overwrite your current configurations, re-install modules, restore settings, and reboot the mirror.')) return;
    
    runRestoreBtn.disabled = true;
    cancelRestoreBtn.disabled = true;
    
    backupProgressPanel.style.display = 'block';
    backupProgressLog.textContent = 'Initializing restoration workflow...\n';
    backupProgressLog.textContent += 'Merging configurations and restoring settings...\n';
    backupProgressLog.textContent += 'Reinstalling standard and local modules... (This could take a minute)\n';
    
    const payload = {};
    if (activeDecryptionPassword) {
        payload.password = activeDecryptionPassword;
    }
    
    try {
        const res = await fetch('/admin/backup/restore', {
            method: 'POST',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok) {
            backupProgressLog.textContent += '\nRestoration successful!\nRebooting mirror system... Page will reload automatically.\n';
            showGlobal('Restoration successful. Server restarting...', 'success');
            
            const pollStart = Date.now();
            const poll = setInterval(async () => {
                if (Date.now() - pollStart > 45000) {
                    clearInterval(poll);
                    backupProgressLog.textContent += 'Timeout waiting for reboot. Please refresh manually.\n';
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
        } else {
            backupProgressLog.textContent += `\nError during restoration: ${data.detail || 'Unknown error'}\n`;
            showGlobal('Restoration failed.', 'error');
            runRestoreBtn.disabled = false;
            cancelRestoreBtn.disabled = false;
        }
    } catch (err) {
        backupProgressLog.textContent += `\nNetwork error: ${err.message}\n`;
        showGlobal('Restoration failed due to network error.', 'error');
        runRestoreBtn.disabled = false;
        cancelRestoreBtn.disabled = false;
    }
};

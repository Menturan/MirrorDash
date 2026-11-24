// The "Shared as" row under a switch with a schema "copy_value" (forms.py, schema_form.html toggle): the template in
// its data-copy-value, "{key}" filled from the sibling field of the same form level (the toggle's own name with its
// last [part] swapped, so it holds after a row is renumbered). Follows typing and the switch by delegation, no ids:
// forms are swapped in and out. textContent only: a module's author wrote the template.
const copySiblingValue = (toggle, key) => {
    const name = toggle.name.replace(/\[[^\]]*\]$|^[^[]+$/, (last) => last[0] === '[' ? `[${key}]` : key);
    const field = (toggle.form || document).querySelector(`[name="${CSS.escape(name)}"]:not([type=hidden])`);
    return field ? field.value.trim() : '';
};

function refreshCopyRows(scope) {
    scope.querySelectorAll('.copy-row').forEach((row) => {
        const toggle = row.previousElementSibling?.querySelector('input[type=checkbox]');
        if (!toggle) return;
        row.hidden = !toggle.checked;
        const text = row.dataset.copyValue.replace(/\{(\w+)\}/g, (_, key) => copySiblingValue(toggle, key) || `<${key}>`);
        row.querySelector('.copy-chip').textContent = text;
        row.querySelector('.copy-btn').setAttribute('aria-label', 'Copy ' + text);
    });
}

// navigator.clipboard exists only on https and localhost; the admin is usually plain http on the LAN
function copyText(chip) {
    if (navigator.clipboard) return navigator.clipboard.writeText(chip.textContent);
    const range = document.createRange();
    range.selectNodeContents(chip);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    return document.execCommand('copy') ? Promise.resolve() : Promise.reject(new Error('copy failed'));
}

document.addEventListener('input', (event) => refreshCopyRows(event.target.form || document));
document.addEventListener('change', (event) => refreshCopyRows(event.target.form || document));

document.addEventListener('click', async (event) => {
    const button = event.target.closest('.copy-btn');
    if (!button) return;
    try {
        await copyText(button.closest('.copy-row').querySelector('.copy-chip'));
    } catch {
        return;
    }
    const icon = button.querySelector('i');
    icon.className = 'fas fa-check';
    setTimeout(() => { icon.className = 'far fa-copy'; }, 1500);
});

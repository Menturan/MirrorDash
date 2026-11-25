// A save the server refuses (422 {"detail", "fields": {input name: message}}, routes.py save_module_config_route)
// shows every message at its field: the field marked, the message under it, its folds opened, the first one
// scrolled to, and "N fields need fixing" beside Save. Editing a field clears its message.

const sheetFormNow = () => document.querySelector('#config-fields-global .module-config-form');

// The input a name belongs to: the one so named, else the first of a setting's parts (a list's [id], a group's boxes)
function fieldNamed(form, name) {
    const inputs = [...form.elements];
    return inputs.find((input) => input.name === name) || inputs.find((input) => input.name.startsWith(`${name}[`));
}

function updateErrorCount(form) {
    const count = form.querySelectorAll('.field-error').length;
    form.querySelector('.save-status').textContent = count ? `${count} field${count === 1 ? ' needs' : 's need'} fixing` : '';
}

function showFieldErrors(form, fields) {
    clearFieldErrors(form);
    const shown = [];
    for (const [name, message] of Object.entries(fields)) {
        const input = fieldNamed(form, name);
        if (!input) continue;
        window.revealField(input);  // admin.js: its folds opened, show_if shown
        input.setAttribute('aria-invalid', 'true');
        const text = Object.assign(document.createElement('p'), { className: 'field-error', textContent: message });
        text.dataset.for = name;
        text.setAttribute('role', 'alert');
        (input.closest('.form-group, .shared-field') || input.parentElement).append(text);
        shown.push(input);
    }
    updateErrorCount(form);
    const first = shown.sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1))[0];
    first?.scrollIntoView({ block: 'center' });
    first?.focus({ preventScroll: true });
    return shown.length > 0;
}

function clearFieldErrors(form, name = null) {
    form.querySelectorAll('.field-error').forEach((text) => {
        if (name !== null && name !== text.dataset.for && !name.startsWith(`${text.dataset.for}[`)) return;
        form.querySelectorAll('[aria-invalid]').forEach((input) => {
            if (input.name === text.dataset.for || input.name.startsWith(`${text.dataset.for}[`)) input.removeAttribute('aria-invalid');
        });
        text.remove();
    });
    updateErrorCount(form);
}

document.body.addEventListener('htmx:responseError', (event) => {
    const form = sheetFormNow();
    if (!form || event.detail.elt !== form || event.detail.xhr.status !== 422) return;
    let fields;
    try { fields = JSON.parse(event.detail.xhr.responseText).fields; } catch (_) { return; }
    if (fields && !showFieldErrors(form, fields)) showGlobal(Object.values(fields)[0], 'error');  // none of them is on the form
});

for (const type of ['input', 'change']) {
    document.addEventListener(type, (event) => {
        const form = event.target.closest?.('#config-fields-global .module-config-form');
        if (form && event.target.name) clearFieldErrors(form, event.target.name);
    });
}
document.body.addEventListener('htmx:afterRequest', (event) => {
    const form = sheetFormNow();
    if (form && event.detail.successful && event.detail.elt === form) clearFieldErrors(form);
});

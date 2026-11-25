// "Reset to <default>" on a changed field: forms.py puts data-default (JSON) and data-default-text on the wrapper
// of each field that has a default; the link shows on its label row while the value differs, and a click puts
// the default back, firing input and change so show_if, the preview and the summaries follow.

function resetControl(wrapper) {
    return wrapper.querySelector('input[name]:not([type=hidden]), select[name], textarea[name]');
}

function resetValueIsDefault(control, defaultValue) {
    return control.type === 'checkbox' ? control.checked === defaultValue : control.value === String(defaultValue);
}

function updateResetLink(wrapper) {
    const control = resetControl(wrapper);
    if (!control) return;
    let link = wrapper.querySelector('.reset-link');
    if (!link) {
        link = document.createElement('button');
        link.type = 'button';
        link.className = 'reset-link';
        link.innerHTML = '<i class="fas fa-rotate-left" aria-hidden="true"></i> <span></span>';
        link.querySelector('span').textContent = `Reset to ${wrapper.dataset.defaultText}`;
        wrapper.querySelector('.field-title-row')?.append(link);
    }
    link.hidden = resetValueIsDefault(control, JSON.parse(wrapper.dataset.default));
}

function updateResetLinks(root) {
    root.querySelectorAll('[data-default]').forEach(updateResetLink);
}

function resetToDefault(wrapper) {
    const control = resetControl(wrapper);
    const defaultValue = JSON.parse(wrapper.dataset.default);
    if (control.type === 'checkbox') control.checked = defaultValue;
    else control.value = defaultValue;
    for (const type of ['input', 'change']) control.dispatchEvent(new Event(type, {bubbles: true}));
}

document.addEventListener('click', (event) => {
    const link = event.target.closest?.('.reset-link');
    if (link) resetToDefault(link.closest('[data-default]'));
});
for (const type of ['input', 'change']) {
    document.addEventListener(type, (event) => {
        const wrapper = event.target.closest?.('[data-default]');
        if (wrapper) updateResetLink(wrapper);
    });
}
// The sheet is swapped in by htmx
document.addEventListener('htmx:afterSettle', (event) => updateResetLinks(event.target));
updateResetLinks(document);

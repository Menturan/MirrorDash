// A setting shown only while other fields of its form have some values (ARCHITECTURE #50): forms.py wraps it
// in data-show-if='{"modules[photos][source]": ["immich"]}' and hides it from the start; this follows the
// form as the owner changes it. Hidden it is still sent, so its value stays saved.
// A value list {"not": [...]} means anything but those.
// A switch is two inputs under one name (a hidden "false" and the checkbox): its value is whether it's ticked.

function showIfValue(form, name) {
    const field = form.elements[name];
    const box = field instanceof RadioNodeList ? [...field].find((input) => input.type === 'checkbox') : null;
    return box ? String(box.checked) : field?.value;
}

function followForm(event) {
    const form = event.target.closest?.('form');
    form?.querySelectorAll('[data-show-if]').forEach((wrapper) => {
        const rules = JSON.parse(wrapper.dataset.showIf);
        wrapper.hidden = !Object.entries(rules).every(([name, values]) => {
            const value = showIfValue(form, name);
            return values.not ? !values.not.includes(value) : values.includes(value);
        });
    });
}

// input too: a text box (a carousel group) should show what depends on it while it is typed in
document.addEventListener('change', followForm);
document.addEventListener('input', followForm);

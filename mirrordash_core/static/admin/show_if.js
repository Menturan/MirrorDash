// A setting shown only while other fields of its form have some values (ARCHITECTURE #50): forms.py wraps it
// in data-show-if='{"modules[photos][source]": ["immich"]}' and hides it from the start; this follows the
// form as the owner changes it. Hidden it is still sent, so its value stays saved.
// ponytail: compares a field's .value, so a select or a text box; a switch (two inputs, one name) isn't one.

document.addEventListener('change', (event) => {
    const form = event.target.closest?.('form');
    form?.querySelectorAll('[data-show-if]').forEach((wrapper) => {
        const rules = JSON.parse(wrapper.dataset.showIf);
        wrapper.hidden = !Object.entries(rules).every(([name, values]) => values.includes(form.elements[name]?.value));
    });
});

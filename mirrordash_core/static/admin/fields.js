// A fields setting (ARCHITECTURE #63, schema_form.html "fields"): its boxes are built by the module, asked with
// the unsaved form (routes.py module_fields_route) when the form opens and again when another field of its
// list item (or, for a field outside a list, of the form) changes; never when its own boxes change. The route
// answers with the form's ordinary boxes as HTML, which are swapped in and set up like any new part of the form.
// When the module can't answer, its text shows and the setting as saved stays (saved as __saved, in the row).

function fieldsUrl(box) {
    const form = box.closest('form');
    const saved = box.querySelector(':scope > input[name$="[__saved]"]');
    const url = new URL(form.getAttribute('hx-post').replace(/\/config\/([^/?]+)\/save/, '/fields/$1'), location.href);
    url.searchParams.set('field', saved.name.replace(/\[__saved\]$/, ''));
    return url;
}

function showFieldsError(box, text) {
    const kept = box.querySelector(':scope > input[name$="[__saved]"]').value !== '{}';
    const p = Object.assign(document.createElement('p'), { className: 'field-description fields-status error-text' });
    p.textContent = (/[.!?]$/.test(text) ? text : `${text}.`) + (kept ? ' The saved settings are kept.' : '');
    box.querySelector('.fields-body').replaceChildren(p);  // no boxes: the save keeps __saved
}

async function loadFields(box) {
    const run = box.fieldsRun = (box.fieldsRun || 0) + 1;
    let response = null;
    try {
        response = await fetch(fieldsUrl(box), { method: 'POST', body: new FormData(box.closest('form')) });
    } catch (e) {
        // the mirror isn't reachable: handled below
    }
    if (run !== box.fieldsRun || !box.isConnected) return;  // a newer one is on its way, or the row is gone
    const text = response ? await response.text() : "Couldn't reach the mirror.";
    if (run !== box.fieldsRun || !box.isConnected) return;
    if (!response?.ok) {
        showFieldsError(box, response?.status === 422 ? text : `Couldn't load the settings (${response?.status ?? 'offline'}).`);
        return;
    }
    box.querySelector('.fields-body').innerHTML = text;
    // The same set-up as for any part swapped into the form (icons, choices, Lucide: their afterSwap listeners)
    box.dispatchEvent(new CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: box } }));
    schedulePreview({ target: box });  // the boxes' values and defaults are part of the form now
}

function setUpFieldsSettings(root) {
    root.querySelectorAll('.fields-setting').forEach((box) => {
        if (box.fieldsReady) return;  // a new list item swaps in beside boxes already set up
        box.fieldsReady = true;
        loadFields(box);
    });
}

// A changed field asks again the boxes that may depend on it, as choices.js does for lists: those in its own list
// item, those outside any item, and, for a field outside any item, all of them
document.addEventListener('change', (event) => {
    const form = event.target.closest?.('#config-fields-global form');
    if (!form) return;
    const scope = event.target.closest('.array-item-card') || form;
    form.querySelectorAll('.fields-setting').forEach((box) => {
        if (box.contains(event.target)) return;
        if (scope.contains(box) || !box.closest('.array-item-card')) {
            clearTimeout(box.fieldsTimer);
            box.fieldsTimer = setTimeout(() => loadFields(box), 400);
        }
    });
});
document.body.addEventListener('htmx:afterSwap', (event) => {
    if (event.target.closest?.('#config-fields-global')) setUpFieldsSettings(event.target);
});

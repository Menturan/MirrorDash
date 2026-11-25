// Leaving the module sheet with edits not saved (closing it, or switching to another instance's chip) asks first,
// inline under the header: "Keep editing" or "Discard changes". An edit is any change of a field of the form;
// a Save, a new form and closing the sheet start clean again.

let sheetDirty = false, afterDiscard = null;
const unsavedBar = () => document.getElementById('sheetUnsaved');
const sheetForm = () => document.querySelector('#config-fields-global .module-config-form');

function hideUnsavedBar() {
    unsavedBar().hidden = true;
    afterDiscard = null;
}

// Runs proceed at once when nothing is unsaved (and says true); else asks and runs it on "Discard changes"
window.sheetGuard = function (proceed) {
    if (!sheetDirty) {
        proceed();
        return true;
    }
    afterDiscard = proceed;
    document.getElementById('sheetUnsavedText').textContent = `You have unsaved changes to ${sheetForm()?.dataset.title || 'this module'}.`;
    unsavedBar().hidden = false;
    document.getElementById('sheetKeepEditing').focus();
    return false;
};

document.getElementById('sheetKeepEditing').addEventListener('click', hideUnsavedBar);
document.getElementById('sheetDiscard').addEventListener('click', () => {
    const proceed = afterDiscard;
    sheetDirty = false;
    hideUnsavedBar();
    proceed?.();
});

// A box with a name is a field of the form; the "On the mirror" switch and the like are not
for (const type of ['input', 'change']) {
    document.addEventListener(type, (event) => {
        if (event.target.name && event.target.closest?.('#config-fields-global .module-config-form')) sheetDirty = true;
    });
}

const startClean = () => {
    sheetDirty = false;
    hideUnsavedBar();
};
document.addEventListener('sheet-closed', startClean);
document.body.addEventListener('htmx:afterSwap', (event) => { if (event.target.id === 'config-fields-global') startClean(); });
document.body.addEventListener('htmx:afterRequest', (event) => {
    if (event.detail.successful && event.detail.elt === sheetForm()) sheetDirty = false;
});

// Another instance's chip
document.body.addEventListener('htmx:beforeRequest', (event) => {
    const chip = event.detail.elt;
    if (!chip.matches?.('.sheet-chip[hx-get]') || !sheetDirty) return;
    event.preventDefault();
    window.sheetGuard(() => htmx.ajax('GET', chip.getAttribute('hx-get'), { target: '#config-fields-global' }));
});

// Escape answers the question with "Keep editing" instead of asking again
window.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || unsavedBar().hidden) return;
    event.stopImmediatePropagation();
    hideUnsavedBar();
}, true);

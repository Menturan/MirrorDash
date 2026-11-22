// A shared setting's "Own value" switch (ARCHITECTURE #62): forms.py draws the box, a note and the switch
// under data-shared-control="<the box's id>" data-shared-value="<what the other instances have>". Off, the
// box holds the shared value; on, the instance's own, which starts as a copy of the shared one. Both are
// kept here until the form is saved, so switching back and forth loses nothing. A switch (boolean) box is
// a checkbox, read as "true" or "false". Only the switch is saved with the form (shared_own[key]).

function sharedRead(box) {
    return box.type === 'checkbox' ? String(box.checked) : box.value;
}

function sharedWrite(box, value) {
    if (box.type === 'checkbox') box.checked = value === 'true';
    else box.value = value;
    box.dispatchEvent(new Event('input', {bubbles: true}));  // the colour box's swatches, show_if
}

document.addEventListener('change', (event) => {
    const own = event.target.closest?.('.shared-own input');
    const row = own?.closest('.shared-row');
    const box = row && document.getElementById(row.dataset.sharedControl);
    if (!box) return;
    if (own.checked) {  // what was typed while it was the shared value is the shared value
        row.dataset.sharedValue = sharedRead(box);
        sharedWrite(box, row.dataset.ownValue ?? row.dataset.sharedValue);
    } else {
        row.dataset.ownValue = sharedRead(box);
        sharedWrite(box, row.dataset.sharedValue);
    }
    row.querySelector('.shared-note-linked').hidden = own.checked;
    row.querySelector('.shared-note-own').hidden = !own.checked;
    const shown = row.querySelector('.shared-note-value');  // a secret's note has none
    if (shown) shown.textContent = row.dataset.sharedValue === '' ? 'empty' : row.dataset.sharedValue;
});

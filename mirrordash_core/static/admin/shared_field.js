// A shared setting's "Own value" switch (ARCHITECTURE #62): forms.py draws a line saying the value is set in
// Shared Settings, the switch, and the box in a disabled fieldset (neither sent nor checked) that is shown
// only while the switch is on: data-shared-control="<the box's id>" data-shared-value="<the shared value>"
// (empty for a secret, which is never printed). Turned on, the box starts as a copy of the shared value;
// the own value is kept here while it is off, so switching back and forth loses nothing. A switch (boolean)
// box is a checkbox, read as "true" or "false". Only the switch and an own value are saved with the form.

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
    const fieldset = row.parentElement.querySelector('.shared-box');
    if (own.checked) {
        fieldset.disabled = fieldset.hidden = false;
        sharedWrite(box, row.dataset.ownValue ?? row.dataset.sharedValue);
    } else {
        row.dataset.ownValue = sharedRead(box);
        fieldset.disabled = fieldset.hidden = true;
    }
    row.querySelector('.shared-note-linked').hidden = own.checked;
    row.querySelector('.shared-note-own').hidden = !own.checked;
});

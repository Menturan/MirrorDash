// The info button of a field with a schema "help" (forms.py, schema_form.html help_button): its data-help and
// data-help-title fill one popup, made on the first click and shown by the browser's own popover (Esc and a click
// outside close it). textContent only: a module's author wrote the text.
document.addEventListener('click', event => {
    const button = event.target.closest('.help-btn');
    if (!button) return;
    event.preventDefault();  // in an accordion's summary a click would also fold it
    let popup = document.getElementById('help-popup');
    if (!popup) {
        popup = document.createElement('div');
        popup.id = 'help-popup';
        popup.className = 'help-popup';
        popup.popover = 'auto';
        popup.setAttribute('role', 'dialog');
        popup.innerHTML = '<div class="help-popup-head"><h3 class="help-popup-title"></h3>'
            + '<button type="button" class="help-popup-close" aria-label="Close"><i class="fas fa-xmark" aria-hidden="true"></i></button></div>'
            + '<p class="help-popup-text"></p>';
        popup.querySelector('.help-popup-close').addEventListener('click', () => popup.hidePopover());
        document.body.append(popup);
    }
    popup.querySelector('.help-popup-title').textContent = button.dataset.helpTitle;
    popup.querySelector('.help-popup-text').textContent = button.dataset.help;
    popup.showPopover();
});

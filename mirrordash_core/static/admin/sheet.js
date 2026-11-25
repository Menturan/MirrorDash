// The module sheet's form (admin_module_config.html) once it is in the page: what the server doesn't know (the
// module's name, set into the footer's words), the header's position, the summaries; and after a Save the parts
// that changed (the chips with a new name or position, the Save button of a new one, the footer) are swapped
// by themselves, so the open rows and the scroll stay.

function finishSheet(root) {
    const title = document.getElementById('profileTitle').textContent;
    root.querySelectorAll('[data-module-title]').forEach((el) => { el.textContent = title; });
    const uninstall = root.querySelector('[data-uninstall]');
    uninstall?.setAttribute('hx-confirm', `Uninstall ${title}? This removes the package and restarts the mirror.`);
    const form = document.querySelector('#config-fields-global .module-config-form');
    document.getElementById('profileDesc').textContent = form?.dataset.where || '';
    window.updateSheetSummaries(root);
}

document.body.addEventListener('htmx:afterSwap', (event) => {
    if (event.target.id === 'config-fields-global') finishSheet(event.target);
});

document.body.addEventListener('configSaved', async (event) => {
    const { module, instance } = event.detail;
    const fields = document.getElementById('config-fields-global');
    if (!openSettings || openSettings.name !== module || !document.getElementById('configOverlay').classList.contains('open')) return;
    const response = await fetch(`/admin/panels/modules/config/${encodeURIComponent(module)}?instance_id=${encodeURIComponent(instance)}`);
    if (!response.ok || !document.getElementById('configOverlay').classList.contains('open')) return;
    const fresh = Object.assign(document.createElement('div'), { innerHTML: await response.text() });
    fresh.querySelectorAll('[data-region]').forEach((region) => {
        const old = fields.querySelector(`[data-region="${region.dataset.region}"]`);
        if (!old) return;
        old.replaceWith(region);
        htmx.process(region);
    });
    Object.assign(fields.querySelector('.module-config-form').dataset, fresh.querySelector('.module-config-form').dataset);
    finishSheet(fields);
});

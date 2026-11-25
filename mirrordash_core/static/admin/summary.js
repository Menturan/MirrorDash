// The one-line summaries on the module sheet's folded rows (admin_module_config.html), worked out from the form
// as it is now, so they follow the owner's edits before Save: Placement, When it shows, Heading, and the shared
// block's (what is missing while a required shared setting is empty). Also the instance chip's name box, as wide
// as its name.

const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
const DAY_NAMES = { mon: 'Mon', tue: 'Tue', wed: 'Wed', thu: 'Thu', fri: 'Fri', sat: 'Sat', sun: 'Sun' };

// A setting's input in a row, by the end of its name (a switch is a hidden "false" and a checkbox: the checkbox)
const rowInput = (row, key) => row.querySelector(`[name$="[${key}]"]:not([type=hidden])`);

function placementSummary(row) {
    const position = rowInput(row, 'position');
    const width = Number(rowInput(row, 'width_percent')?.value || 0);
    const height = Number(rowInput(row, 'height_percent')?.value || 0);
    const parts = [position?.selectedOptions[0]?.text, width ? `${width}% wide` : 'auto width'];
    if (height) parts.push(`${height}% tall`);
    return parts.filter(Boolean).join(' · ');
}

function daysText(days) {
    const sorted = DAYS.filter((day) => days.includes(day));
    if (sorted.length === 7) return 'Every day';
    if (sorted.join() === 'mon,tue,wed,thu,fri') return 'Weekdays';
    if (sorted.join() === 'sat,sun') return 'Weekends';
    return sorted.length ? sorted.map((day) => DAY_NAMES[day]).join(', ') : 'No days';
}

function whenSummary(row) {
    const days = [...row.querySelectorAll('[name$="[schedule][days]"]:checked')].map((box) => box.value);
    const from = rowInput(row, 'from')?.value, to = rowInput(row, 'to')?.value;
    const times = from && to ? `${from}–${to}` : from ? `from ${from}` : to ? `until ${to}` : 'all day';
    return `${daysText(days)} · ${times}`;
}

function headingSummary(row) {
    if (!rowInput(row, 'show_header')?.checked) return 'Hidden';
    const text = rowInput(row, 'heading')?.value.trim();
    return `Shown · ${text || document.getElementById('profileTitle').textContent}`;
}

const SUMMARIES = { placement: placementSummary, when: whenSummary, heading: headingSummary };

// The block is open for a required shared setting that is empty (the form marks the required ones); only the
// empty required boxes are named. A required secret is never filled in the page (a saved one shows empty), so
// it counts as missing only while the server says something is (data-needs-input)
function sharedSummary(block) {
    const slot = block.querySelector('.acc-summary');
    const boxes = block.querySelectorAll('input:not([type=hidden], [type=checkbox], [type=range])[required], select[required]');
    const missing = [...boxes].filter((input) => !input.value.trim() && (input.type !== 'password' || block.hasAttribute('data-needs-input')))
        .map((input) => input.closest('.form-group, .shared-field')?.querySelector('.field-title')?.textContent);
    const warn = block.hasAttribute('data-needs-input') && missing.length;
    slot.classList.toggle('warn', Boolean(warn));
    if (!warn) return void (slot.textContent = slot.dataset.text);
    const icon = Object.assign(document.createElement('i'), { className: 'fas fa-circle-exclamation' });
    icon.setAttribute('aria-hidden', 'true');
    slot.replaceChildren(icon, ` ${missing.filter(Boolean).join(', ') || 'Required setting'} missing`);
}

function sizeChipName(input) {
    input.size = Math.max(6, (input.value || input.placeholder).length + 1);
}

window.updateSheetSummaries = function (root = document) {
    root.querySelectorAll('[data-summary]').forEach((row) => {
        if (SUMMARIES[row.dataset.summary]) row.querySelector('.acc-summary').textContent = SUMMARIES[row.dataset.summary](row);
    });
    root.querySelectorAll('[data-shared]').forEach(sharedSummary);
    root.querySelectorAll('.chip-name').forEach(sizeChipName);
};

const inSheet = (event) => event.target.closest?.('#config-fields-global .module-config-form');
for (const type of ['input', 'change']) {
    document.addEventListener(type, (event) => { if (inSheet(event)) window.updateSheetSummaries(); });
}

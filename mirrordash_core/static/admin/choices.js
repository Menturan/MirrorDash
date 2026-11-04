// A list or search setting (ARCHITECTURE #48, schema_form.html "choice"): its choices come from the module,
// asked with the unsaved form (routes.py module_choices_route), so they can follow the other fields. A list
// loads when the form opens and again when a field it may depend on changes (a school list follows the
// municipality chosen in its row); a search asks as the owner types. Saved as [id] and [name].

function choicesUrl(field, query) {
    const form = field.closest('form');
    const url = new URL(form.getAttribute('hx-post').replace(/\/config\/([^/?]+)\/save/, '/choices/$1'), location.href);
    url.searchParams.set('field', field.querySelector('[data-choice-name]').name.replace(/\[name\]$/, ''));
    if (query !== undefined) url.searchParams.set('query', query);
    return url;
}

async function askChoices(field, query) {
    try {
        const response = await fetch(choicesUrl(field, query), { method: 'POST', body: new FormData(field.closest('form')) });
        return response.ok ? await response.json() : { error: `Couldn't load the choices (${response.status}).` };
    } catch (e) {
        return { error: "Couldn't reach the mirror." };
    }
}

function setChoiceStatus(field, text, isError = false) {
    const status = field.querySelector('.choice-status');
    status.textContent = text || '';
    status.classList.toggle('error-text', isError);
}

// A list: the browser's own <select>. Never disabled while it holds a saved choice: a disabled control isn't
// posted, and saving would drop it.
async function loadChoiceList(field) {
    const select = field.querySelector('select[data-choice-input]');
    const name = field.querySelector('[data-choice-name]');
    const run = field.choiceRun = (field.choiceRun || 0) + 1;
    setChoiceStatus(field, 'Loading…');
    const answer = await askChoices(field);
    if (run !== field.choiceRun) return;  // a newer one is on its way
    const saved = select.value;
    if (answer.error) {
        setChoiceStatus(field, saved ? `${answer.error} Your choice is kept.` : answer.error, !saved);
        select.disabled = !saved;
        return;
    }
    const choices = answer.choices || [];
    const options = [new Option(choices.length ? 'Choose…' : 'Nothing to choose from', '')];
    for (const choice of choices) {
        const option = new Option(choice.detail ? `${choice.name} · ${choice.detail}` : choice.name, choice.id);
        option.dataset.name = choice.name;
        options.push(option);
    }
    select.replaceChildren(...options);
    select.value = choices.some((choice) => choice.id === saved) ? saved : '';
    name.value = select.selectedOptions[0]?.dataset.name || '';
    select.disabled = false;
    setChoiceStatus(field, '');
}

// A search: a text box with the module's suggestions under it (a listbox the arrow keys and Enter work).
function setUpChoiceSearch(field) {
    const input = field.querySelector('[data-choice-input]');
    const list = field.querySelector('.choice-suggestions');
    const id = field.querySelector('[data-choice-id]');
    const name = field.querySelector('[data-choice-name]');
    let timer = null, run = 0, active = -1, shown = [];

    const close = () => { list.hidden = true; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); active = -1; };
    const highlight = (index) => {
        active = index;
        [...list.children].forEach((item, i) => item.classList.toggle('active', i === index));
        if (index >= 0) input.setAttribute('aria-activedescendant', list.children[index].id);
    };
    const choose = (choice) => {
        input.value = name.value = choice.name;
        id.value = choice.id;
        close();
        setChoiceStatus(field, '');
        id.dispatchEvent(new Event('change', { bubbles: true }));  // the preview, and lists that follow it
    };
    const show = (answer) => {
        shown = answer.choices || [];
        list.replaceChildren(...shown.map((choice, i) => {
            const item = Object.assign(document.createElement('li'), { id: `${list.id}-${i}`, textContent: choice.name });
            item.setAttribute('role', 'option');
            if (choice.detail) item.append(Object.assign(document.createElement('span'), { textContent: choice.detail }));
            item.addEventListener('mousedown', (event) => { event.preventDefault(); choose(choice); });
            return item;
        }));
        list.hidden = !shown.length;
        input.setAttribute('aria-expanded', String(shown.length > 0));
        highlight(-1);
        setChoiceStatus(field, answer.error || (shown.length ? '' : 'No matches.'), Boolean(answer.error));
    };

    input.addEventListener('input', () => {
        if (input.value !== name.value) id.value = name.value = '';  // what's typed is no choice until one is picked
        clearTimeout(timer);
        const query = input.value.trim();
        if (!query) { close(); setChoiceStatus(field, ''); return; }
        timer = setTimeout(async () => {
            const mine = ++run;
            const answer = await askChoices(field, query);
            if (mine === run) show(answer);
        }, 300);
    });
    input.addEventListener('keydown', (event) => {
        if (list.hidden) return;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            const step = event.key === 'ArrowDown' ? 1 : -1;
            highlight((active + step + shown.length) % shown.length);
        } else if (event.key === 'Enter' && active >= 0) {
            event.preventDefault();  // a choice, not a save
            choose(shown[active]);
        } else if (event.key === 'Escape') {
            close();
        }
    });
    input.addEventListener('blur', () => {
        close();
        if (input.value.trim() && !id.value) setChoiceStatus(field, 'Pick one of the suggestions.', true);
    });
}

function setUpChoiceFields(root) {
    root.querySelectorAll('[data-choice-field]').forEach((field) => {
        if (field.choiceReady) return;  // a new list item swaps in beside fields already set up
        field.choiceReady = true;
        if (field.dataset.choiceField === 'list') {
            field.querySelector('select').addEventListener('change', (event) => {
                field.querySelector('[data-choice-name]').value = event.target.selectedOptions[0]?.dataset.name || '';
            });
            loadChoiceList(field);
        } else {
            setUpChoiceSearch(field);
        }
    });
}

// A changed field reloads the lists it may feed: those in its own list item, or, for a field outside any
// item (an API key), every list of the form.
let choiceReloadTimer = null;
document.addEventListener('change', (event) => {
    const form = event.target.closest?.('#config-fields-global form');
    if (!form) return;
    const scope = event.target.closest('.array-item-card') || form;
    clearTimeout(choiceReloadTimer);
    choiceReloadTimer = setTimeout(() => {
        scope.querySelectorAll('[data-choice-field="list"]').forEach((field) => {
            if (!field.contains(event.target)) loadChoiceList(field);
        });
    }, 400);
});
document.body.addEventListener('htmx:afterSwap', (event) => {
    if (event.target.closest?.('#config-fields-global')) setUpChoiceFields(event.target);
});

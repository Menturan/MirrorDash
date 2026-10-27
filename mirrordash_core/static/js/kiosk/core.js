/**
 * Kiosk core: WebSocket connection, module rendering, carousel management.
 * Uses Shadow DOM for style isolation (design tokens injected separately).
 */

// The page's script nonce (its CSP runs no other script): given to the scripts of cards that may run them
const NONCE = document.currentScript.nonce;
const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const wsUrl = `${protocol}//${window.location.host}/ws`;
let socket;
let retryDelay = 2000;
const MAX_RETRY_DELAY = 30000;
// After this long without the app, say so instead of "Reconnecting…" forever (it keeps trying)
const NOT_RESPONDING_AFTER = 2 * 60 * 1000;
let lostAt = null;
// The MirrorDash version this page was loaded with (the server says it on every connect)
let pageVersion = null;
let pageBootId = null;
let hideStatusTimer = null;

function setStatus(state) {
    if (state !== 'connected' && lostAt && Date.now() - lostAt > NOT_RESPONDING_AFTER) state = 'failed';
    const statusEl = document.getElementById('ws-status');
    const statusLabel = statusEl.querySelector('.ws-status__label');
    
    statusEl.className = `ws-status ws-status--${state}`;
    statusLabel.textContent = { connected: 'Connected', disconnected: 'Reconnecting…', connecting: 'Connecting…',
                                failed: 'MirrorDash isn\u2019t responding' }[state] ?? state;
    
    const iconEl = statusEl.querySelector('[data-lucide]');
    if (iconEl) {
        iconEl.setAttribute('data-lucide', state === 'disconnected' || state === 'failed' ? 'wifi-off' : 'wifi');
        if (window.lucide) {
            lucide.createIcons({ root: statusEl });
        }
    }
}

function updateCarouselGroup(groupContainer) {
    const slides = Array.from(groupContainer.querySelectorAll('.carousel-slide'));
    if (slides.length === 0) {
        if (groupContainer._carouselTimer) {
            clearInterval(groupContainer._carouselTimer);
            groupContainer._carouselTimer = null;
        }
        return;
    }

    let activeSlide = groupContainer.querySelector('.carousel-slide.carousel-active');
    if (!activeSlide) {
        activeSlide = slides[0];
        activeSlide.classList.add('carousel-active');
    }

    // A module updating often must not restart the rotation: only a changed slide count or interval
    // does, or a video starting or stopping (which hands the stepping to the minute timer and back)
    const key = `${slides.length}/${carouselInterval(groupContainer)}/${videoIsPlaying}`;
    if (groupContainer._carouselTimer && groupContainer._carouselKey === key) return;
    clearInterval(groupContainer._carouselTimer);
    groupContainer._carouselTimer = null;

    // While a video plays, the minute timer (setVideoIsPlaying) steps the carousels instead
    if (slides.length > 1 && !videoIsPlaying) {
        groupContainer._carouselKey = key;
        groupContainer._carouselTimer = setInterval(() => stepCarousel(groupContainer), carouselInterval(groupContainer));
    }
}

function carouselInterval(groupContainer) {
    return (parseInt(groupContainer.getAttribute('data-carousel-interval'), 10) || 15) * 1000;
}

function stepCarousel(groupContainer) {
    const currentSlides = Array.from(groupContainer.querySelectorAll('.carousel-slide'));
    if (currentSlides.length <= 1) return;
    groupContainer._steppedAt = Date.now();

    const currentActive = groupContainer.querySelector('.carousel-slide.carousel-active');
    let nextIndex = 0;
    if (currentActive) {
        const currentIndex = currentSlides.indexOf(currentActive);
        nextIndex = (currentIndex + 1) % currentSlides.length;
        currentActive.classList.remove('carousel-active');
    }
    currentSlides[nextIndex].classList.add('carousel-active');
}

// While a module plays a video, the page redraws once a minute, on the minute, so the video's window
// stays smooth (ARCHITECTURE #33): the server sends the module updates then, the carousels step then
// (without their fade, style.css), and modules see data-video-is-playing on their host and the
// mirrordash:video_is_playing event, and tick once a minute.
let videoIsPlaying = false;
let minuteTimer = null;
const untilTheMinute = () => 60000 - Date.now() % 60000;

function setVideoIsPlaying(playing) {
    if (playing === videoIsPlaying) return;
    videoIsPlaying = playing;
    for (const el of [document.documentElement, ...document.querySelectorAll('[data-module]')]) {
        el.toggleAttribute('data-video-is-playing', playing);
    }
    clearTimeout(minuteTimer);
    if (playing) {
        const onTheMinute = () => {
            // A carousel slower than a minute keeps its own pace; a faster one steps once a minute
            for (const group of document.querySelectorAll('.carousel-group-container')) {
                if (Date.now() - (group._steppedAt || 0) >= carouselInterval(group) - 1000) stepCarousel(group);
            }
            minuteTimer = setTimeout(onTheMinute, untilTheMinute());
        };
        minuteTimer = setTimeout(onTheMinute, untilTheMinute());
    }
    document.querySelectorAll('.carousel-group-container').forEach(updateCarouselGroup);  // stops or restarts their timers
    window.dispatchEvent(new CustomEvent('mirrordash:video_is_playing', { detail: { playing } }));
}

// An area's carousel group's container (made on first use), which steps through its cards; its interval
// follows the settings. The group's name is the owner's free text: escaped before it goes into a selector.
function carouselGroupContainer(area, groupName, intervalSeconds) {
    let groupContainer = area.querySelector(`.carousel-group-container[data-carousel-group="${CSS.escape(groupName)}"]`);
    if (!groupContainer) {
        groupContainer = document.createElement('div');
        groupContainer.className = 'carousel-group-container';
        groupContainer.setAttribute('data-carousel-group', groupName);
        area.appendChild(groupContainer);
    }
    groupContainer.setAttribute('data-carousel-interval', intervalSeconds || 15);
    return groupContainer;
}

function moduleCard(instanceName) {
    return document.querySelector(`[data-module="${CSS.escape(instanceName)}"]`);
}

// A card slides in once (module-enter), then the class goes so a later redraw doesn't slide it again
function slideIn(card) {
    card.classList.add('module-enter');
    card.addEventListener('animationend', () => card.classList.remove('module-enter'), { once: true });
}

// A module's video_window asks where its video goes: the element with data-video-window on its card, in
// CSS pixels, which are the screen's (features/video/window.py places the module's player there)
function answerWhereTheVideoWindowIs(instanceName) {
    const card = moduleCard(instanceName);
    card?.getAnimations().forEach(animation => animation.finish());  // a new card slides in (fadeIn): its place is where it ends
    const box = card?.shadowRoot?.querySelector('[data-video-window]')?.getBoundingClientRect();
    const rect = box && box.width > 0 && box.height > 0
        ? [box.left, box.top, box.width, box.height].map(Math.round) : null;
    socket.send(JSON.stringify({ type: 'video_window', module: instanceName, rect }));
}

// Execute any script tags inside the shadow DOM (since innerHTML doesn't execute them), if the
// module may run scripts (ARCHITECTURE #41: they could read the other cards); otherwise they go.
// Each script gets its module's shadow root as `root` (document.currentScript is null in a shadow tree).
// The root survives re-renders, so per-instance state like timers can live on it: root._timer.
// ponytail: inline classic scripts run synchronously on insert, so one global hand-off is enough;
// a type="module" script runs later and would see the wrong root — give it a per-script id if one ever needs it.
function runCardScripts(shadow, scriptsAllowed) {
    window.__mirrordashRoot = shadow;
    for (const oldScript of shadow.querySelectorAll('script')) {
        if (!scriptsAllowed) {
            oldScript.remove();
            continue;
        }
        const newScript = document.createElement('script');
        for (const attribute of oldScript.attributes) {
            newScript.setAttribute(attribute.name, attribute.value);
        }
        newScript.nonce = NONCE;
        newScript.textContent = `(function (root) {\n${oldScript.textContent}\n})(window.__mirrordashRoot);`;
        oldScript.parentNode.replaceChild(newScript, oldScript);
    }
}

// A module's HTML: its card (made, or moved to its area or carousel group), its wrapper settings, and its
// content in the card's shadow root with the design tokens and the component library
function showModuleHtml(data) {
    const area = document.getElementById(data.position);
    if (!area) return;
    let card = moduleCard(data.module);
    const oldParent = card ? card.parentElement : null;
    const groupContainer = data.carousel_group
        ? carouselGroupContainer(area, data.carousel_group, data.carousel_interval) : null;
    const parentContainer = groupContainer || area;

    if (card) {
        if (card.parentElement !== parentContainer) insertInOrder(parentContainer, card, data.order);
        if (card.classList.contains('module-loading-placeholder')) {
            card.replaceChildren();  // the pulsing loading content goes, not just hidden behind the module
            card.classList.remove('module-loading-placeholder');
            slideIn(card);
        }
    } else {
        card = document.createElement('div');
        card.setAttribute('data-module', data.module);
        insertInOrder(parentContainer, card, data.order);
        slideIn(card);
    }

    // toggle, not className=: a redraw keeps the slide that is showing and its entrance animation
    card.classList.toggle('carousel-slide', !!data.carousel_group);
    if (!data.carousel_group) card.classList.remove('carousel-active');

    // Apply standard per-module wrapper properties from config.
    // Any CSS-valid value works (e.g. max_width "400px", opacity 0.5, z_index 10).
    // Cleared to '' when absent so removing a config value takes effect live.
    card.style.maxWidth  = data.max_width  || '';
    card.style.maxHeight = data.max_height || '';
    card.style.zIndex    = data.z_index != null ? String(data.z_index) : '';
    card.style.opacity   = data.opacity  != null ? String(data.opacity)  : '';
    card.toggleAttribute('data-video-is-playing', videoIsPlaying);

    const shadow = card.shadowRoot || card.attachShadow({ mode: 'open' });
    shadow.innerHTML = `<style>${DESIGN_TOKENS_CSS}${MODULES_CSS}</style>` + data.html;
    if (window.lucide) lucide.createIcons({ root: shadow });
    runCardScripts(shadow, data.scripts);

    if (oldParent && oldParent.classList.contains('carousel-group-container') && oldParent !== parentContainer) {
        updateCarouselGroup(oldParent);
        if (oldParent.children.length === 0) oldParent.remove();
    }
    if (groupContainer) updateCarouselGroup(groupContainer);
}

// What the server sends: "hello" on connecting, a module's HTML, or a change of state
function handleServerMessage(data) {
    if (data.action === 'reload') {
        window.location.reload();
    } else if (data.type === 'ping') {
        // only keeps the connection alive
    } else if (data.type === 'hello') {
        // A new version (update, rollback) brings new page code, and any restart (module install,
        // backup restore) may change the modules: reload when the version or the boot id differs
        if ((pageVersion && pageVersion !== data.version) || (pageBootId && pageBootId !== data.boot_id)) {
            window.location.reload();
            return;
        }
        pageVersion = data.version;
        pageBootId = data.boot_id;
        showOffline(data.online === false);
        setVideoIsPlaying(data.video_is_playing === true);
    } else if (data.type === 'video_is_playing') {
        setVideoIsPlaying(data.playing === true);
    } else if (data.type === 'video_window') {
        answerWhereTheVideoWindowIs(data.module);
    } else if (data.type === 'internet') {
        showOffline(!data.online);
    } else {
        showModuleHtml(data);
    }
}

function connect() {
    setStatus('connecting');
    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
        retryDelay = 2000;
        lostAt = null;
        setStatus('connected');
        const statusEl = document.getElementById('ws-status');
        hideStatusTimer = setTimeout(() => statusEl.classList.add('ws-status--hidden'), 3000);
    };

    socket.onmessage = (event) => {
        let data;
        try {
            data = JSON.parse(event.data);
        } catch (e) {
            console.warn('Received malformed WebSocket message:', event.data);
            return;
        }
        handleServerMessage(data);
    };

    socket.onclose = () => {
        lostAt = lostAt ?? Date.now();
        clearTimeout(hideStatusTimer);
        setStatus('disconnected');
        const statusEl = document.getElementById('ws-status');
        statusEl.classList.remove('ws-status--hidden');
        console.log(`WebSocket closed. Reconnecting in ${retryDelay / 1000}s...`);
        setTimeout(connect, retryDelay);
        retryDelay = Math.min(retryDelay * 2, MAX_RETRY_DELAY);
    };

    socket.onerror = (err) => {
        console.error('WebSocket error:', err);
        socket.close();
    };
}

// An area's cards in the settings' order (order), whichever arrives first: after a restart the page can
// load before the modules run, with no placeholders to hold their places (the clock took Home Assistant's)
function insertInOrder(container, card, order) {
    if (Number.isInteger(order)) card.dataset.order = order;
    const ownOrder = Number(card.dataset.order);
    const nextCard = [...container.children].find(other => other !== card && Number(other.dataset.order) > ownOrder);
    container.insertBefore(card, nextCard || null);
}

// After an update undone or in Safe mode, a card at the top right says so
function showBootStatus(bootStatus) {
    const area = document.getElementById('top_right');
    if (!area || (bootStatus !== 'rollback' && bootStatus !== 'safe_mode')) return;
    const safeMode = bootStatus === 'safe_mode';
    const color = safeMode ? 'var(--color-error)' : 'var(--color-status-warning)';
    const alertDiv = document.createElement('div');
    alertDiv.className = 'card alert-callout';
    alertDiv.style.marginBottom = '1rem';
    alertDiv.style.borderColor = color;
    alertDiv.innerHTML = `
        <div style="display:flex; gap:10px; align-items:center;">
            <i data-lucide="${safeMode ? 'shield-alert' : 'rotate-ccw'}" style="width:20px; height:20px; color:${color};"></i>
            <h3 style="margin:0; font-size:1rem; font-weight:600; text-transform:uppercase; letter-spacing:0.05em;">${safeMode ? 'Safe Mode' : 'Update Undone'}</h3>
        </div>
        <p style="margin:8px 0 0 0; font-size:0.875rem; color:var(--color-text-secondary); line-height:1.4;">${safeMode
            ? 'MirrorDash couldn’t start, so it started the version that came with the system, without your modules. See the admin page.'
            : 'The new version of MirrorDash didn’t start, so the mirror went back to the version before.'}</p>
    `;
    area.insertBefore(alertDiv, area.firstChild);
    if (window.lucide) lucide.createIcons({ root: alertDiv });
}

// A pulsing "Loading <title>…" card in a module's place until its first HTML arrives
function addLoadingPlaceholder(module) {
    const area = document.getElementById(module.position);
    if (!area || moduleCard(module.name)) return;
    const groupContainer = module.carousel_group
        ? carouselGroupContainer(area, module.carousel_group, module.carousel_interval) : null;
    const parentContainer = groupContainer || area;

    const placeholder = document.createElement('div');
    placeholder.setAttribute('data-module', module.name);
    placeholder.className = 'module-loading-placeholder';
    placeholder.innerHTML = `
        <div class="module-loading-content">
            <span class="module-loading-spinner"></span>
            <span class="module-loading-text"></span>
        </div>
    `;
    // The title comes from the module's translations: text, never markup in the page itself
    placeholder.querySelector('.module-loading-text').textContent = `Loading ${module.title}…`;
    if (module.carousel_group) {
        placeholder.classList.add('carousel-slide');
        if (parentContainer.querySelectorAll('.carousel-slide').length === 0) placeholder.classList.add('carousel-active');
    }
    insertInOrder(parentContainer, placeholder, module.order);
    if (groupContainer) updateCarouselGroup(groupContainer);
}

async function loadActiveModules() {
    try {
        const response = await fetch('/api/active-modules');
        const data = await response.json();
        if (!data) return;
        for (const side of ['top', 'bottom', 'left', 'right']) {
            const margin = data[`safe_margin_${side}`];
            if (margin) document.documentElement.style.setProperty(`--safe-margin-${side}`, margin);
        }
        showBootStatus(data.boot_status);
        for (const module of data.modules || []) addLoadingPlaceholder(module);
    } catch (err) {
        console.error('Failed to load active modules list:', err);
    }
}

loadActiveModules();

// The component library (modules.css, what /design shows) goes into every module's shadow root, so it
// is loaded once before any module renders. Inline <style>, not a <link>: that would reload on every update.
let MODULES_CSS = '';
fetch('/static/modules.css')
    .then(r => (r.ok ? r.text() : ''))
    .catch(() => '')
    .then(css => { MODULES_CSS = css; connect(); });

// Tells the app this page still runs: mirrordash-app-watch restarts the kiosk when it stops (a crashed
// or frozen WebKit can keep its window, and even its WebSocket, open)
setInterval(() => { if (socket && socket.readyState === WebSocket.OPEN) socket.send('alive'); }, 60 * 1000);

// The Offline badge: whether the mirror reaches the internet, as the app sees it (features/kiosk/internet.py).
// Not navigator.onLine: in Cog it said offline after a start while the mirror was online, and online
// while the Wi-Fi was joined but nothing got through.
let offlineShown = false;
function showOffline(offline) {
    const indicator = document.getElementById('offline-indicator');
    if (!indicator || offline === offlineShown) return;
    offlineShown = offline;
    if (offline) {
        indicator.style.display = 'flex';
        // Small delay to ensure display:flex has applied before opacity transition
        setTimeout(() => indicator.style.opacity = '1', 10);
    } else {
        indicator.style.opacity = '0';
        setTimeout(() => { if (!offlineShown) indicator.style.display = 'none'; }, 500);
    }
}
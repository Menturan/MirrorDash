/**
 * Kiosk core: WebSocket connection, module rendering, carousel management.
 * Uses Shadow DOM for style isolation (design tokens injected separately).
 */

const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const wsUrl = `${protocol}//${window.location.host}/ws`;
let socket;
let retryDelay = 2000;
const MAX_RETRY_DELAY = 30000;

function setStatus(state) {
    const statusEl = document.getElementById('ws-status');
    const statusLabel = statusEl.querySelector('.ws-status__label');
    
    statusEl.className = `ws-status ws-status--${state}`;
    statusLabel.textContent = { connected: 'Connected', disconnected: 'Reconnecting…', connecting: 'Connecting…' }[state] ?? state;
    
    const iconEl = statusEl.querySelector('[data-lucide]');
    if (iconEl) {
        iconEl.setAttribute('data-lucide', state === 'disconnected' ? 'wifi-off' : 'wifi');
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

    if (groupContainer._carouselTimer) {
        clearInterval(groupContainer._carouselTimer);
        groupContainer._carouselTimer = null;
    }

    if (slides.length > 1) {
        const intervalSec = parseInt(groupContainer.getAttribute('data-carousel-interval'), 10) || 15;
        groupContainer._carouselTimer = setInterval(() => {
            const currentSlides = Array.from(groupContainer.querySelectorAll('.carousel-slide'));
            if (currentSlides.length <= 1) return;
            
            const currentActive = groupContainer.querySelector('.carousel-slide.carousel-active');
            let nextIndex = 0;
            if (currentActive) {
                const currentIndex = currentSlides.indexOf(currentActive);
                nextIndex = (currentIndex + 1) % currentSlides.length;
                currentActive.classList.remove('carousel-active');
            }
            currentSlides[nextIndex].classList.add('carousel-active');
        }, intervalSec * 1000);
    }
}

function connect() {
    setStatus('connecting');
    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
        retryDelay = 2000;
        setStatus('connected');
        const statusEl = document.getElementById('ws-status');
        setTimeout(() => statusEl.classList.add('ws-status--hidden'), 3000);
    };

    socket.onmessage = (event) => {
        let data;
        try {
            data = JSON.parse(event.data);
        } catch (e) {
            console.warn('Received malformed WebSocket message:', event.data);
            return;
        }

        if (data.action === 'reload') {
            window.location.reload();
            return;
        }

        if (data.type === 'ping') return;

        const targetElement = document.getElementById(data.position);
        if (!targetElement) return;

        let moduleDiv = document.querySelector(`[data-module="${data.module}"]`);
        const oldParent = moduleDiv ? moduleDiv.parentElement : null;

        let groupContainer = null;
        if (data.carousel_group) {
            groupContainer = targetElement.querySelector(`.carousel-group-container[data-carousel-group="${data.carousel_group}"]`);
            if (!groupContainer) {
                groupContainer = document.createElement('div');
                groupContainer.className = 'carousel-group-container';
                groupContainer.setAttribute('data-carousel-group', data.carousel_group);
                groupContainer.setAttribute('data-carousel-interval', data.carousel_interval || 15);
                targetElement.appendChild(groupContainer);
            } else {
                groupContainer.setAttribute('data-carousel-interval', data.carousel_interval || 15);
            }
        }

        const parentContainer = groupContainer || targetElement;

        if (moduleDiv) {
            if (moduleDiv.parentElement !== parentContainer) {
                parentContainer.appendChild(moduleDiv);
            }
            if (moduleDiv.classList.contains('module-loading-placeholder')) {
                moduleDiv.classList.remove('module-loading-placeholder');
                moduleDiv.classList.add('module-enter');
                moduleDiv.addEventListener('animationend', () => {
                    moduleDiv.classList.remove('module-enter');
                }, { once: true });
            }
        } else {
            moduleDiv = document.createElement('div');
            moduleDiv.setAttribute('data-module', data.module);
            moduleDiv.classList.add('module-enter');
            parentContainer.appendChild(moduleDiv);
            moduleDiv.addEventListener('animationend', () => {
                moduleDiv.classList.remove('module-enter');
            }, { once: true });
        }

        if (data.carousel_group) {
            moduleDiv.className = 'carousel-slide';
        } else {
            moduleDiv.className = '';
        }

        // Apply standard per-module wrapper properties from config.
        // Any CSS-valid value works (e.g. max_width "400px", opacity 0.5, z_index 10).
        // Cleared to '' when absent so removing a config value takes effect live.
        moduleDiv.style.maxWidth  = data.max_width  || '';
        moduleDiv.style.maxHeight = data.max_height || '';
        moduleDiv.style.zIndex    = data.z_index != null ? String(data.z_index) : '';
        moduleDiv.style.opacity   = data.opacity  != null ? String(data.opacity)  : '';

        const shadow = moduleDiv.shadowRoot || moduleDiv.attachShadow({ mode: 'open' });
        shadow.innerHTML = `<style>${DESIGN_TOKENS_CSS}</style>` + data.html;
        if (window.lucide) {
            lucide.createIcons({ root: shadow });
        }

        if (oldParent && oldParent.classList.contains('carousel-group-container') && oldParent !== parentContainer) {
            updateCarouselGroup(oldParent);
            if (oldParent.children.length === 0) oldParent.remove();
        }

        if (groupContainer) updateCarouselGroup(groupContainer);
    };

    socket.onclose = () => {
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

async function loadActiveModules() {
    try {
        const response = await fetch('/api/active-modules');
        const data = await response.json();

        if (data) {
            if (data.safe_margin_top) document.documentElement.style.setProperty('--safe-margin-top', data.safe_margin_top);
            if (data.safe_margin_bottom) document.documentElement.style.setProperty('--safe-margin-bottom', data.safe_margin_bottom);
            if (data.safe_margin_left) document.documentElement.style.setProperty('--safe-margin-left', data.safe_margin_left);
            if (data.safe_margin_right) document.documentElement.style.setProperty('--safe-margin-right', data.safe_margin_right);
        }

        if (data && (data.boot_status === 'rollback' || data.boot_status === 'safe_mode')) {
            const notifyContainer = document.getElementById('top_right');
            if (notifyContainer) {
                const alertDiv = document.createElement('div');
                alertDiv.className = 'card alert-callout';
                alertDiv.style.marginBottom = '1rem';
                alertDiv.style.borderColor = data.boot_status === 'safe_mode' ? 'var(--color-error)' : 'var(--color-status-warning)';
                
                const title = data.boot_status === 'safe_mode' ? 'Safe Mode Active' : 'System Restored';
                const icon = data.boot_status === 'safe_mode' ? 'shield-alert' : 'rotate-ccw';
                const msg = data.boot_status === 'safe_mode' 
                    ? 'Core system booted from Golden Copy. Custom modules are disabled.'
                    : 'Recovered from boot failure. Update automatically rolled back.';
                
                alertDiv.innerHTML = `
                    <div style="display:flex; gap:10px; align-items:center;">
                        <i data-lucide="${icon}" style="width:20px; height:20px; color:${data.boot_status === 'safe_mode' ? 'var(--color-error)' : 'var(--color-status-warning)'};"></i>
                        <h3 style="margin:0; font-size:1rem; font-weight:600; text-transform:uppercase; letter-spacing:0.05em;">${title}</h3>
                    </div>
                    <p style="margin:8px 0 0 0; font-size:0.875rem; color:var(--color-text-secondary); line-height:1.4;">${msg}</p>
                `;
                notifyContainer.insertBefore(alertDiv, notifyContainer.firstChild);
                if (window.lucide) lucide.createIcons({ root: alertDiv });
            }
        }

        if (data && data.modules) {
            for (const mod of data.modules) {
                const targetElement = document.getElementById(mod.position);
                if (!targetElement) continue;

                if (document.querySelector(`[data-module="${mod.name}"]`)) continue;

                let groupContainer = null;
                if (mod.carousel_group) {
                    groupContainer = targetElement.querySelector(`.carousel-group-container[data-carousel-group="${mod.carousel_group}"]`);
                    if (!groupContainer) {
                        groupContainer = document.createElement('div');
                        groupContainer.className = 'carousel-group-container';
                        groupContainer.setAttribute('data-carousel-group', mod.carousel_group);
                        groupContainer.setAttribute('data-carousel-interval', mod.carousel_interval || 15);
                        targetElement.appendChild(groupContainer);
                    }
                }

                const parentContainer = groupContainer || targetElement;

                const moduleDiv = document.createElement('div');
                moduleDiv.setAttribute('data-module', mod.name);
                moduleDiv.className = 'module-loading-placeholder';
                moduleDiv.innerHTML = `
                    <div class="module-loading-content">
                        <span class="module-loading-spinner"></span>
                        <span class="module-loading-text">Loading ${mod.title}…</span>
                    </div>
                `;

                if (mod.carousel_group) {
                    moduleDiv.classList.add('carousel-slide');
                    if (parentContainer.querySelectorAll('.carousel-slide').length === 0) {
                        moduleDiv.classList.add('carousel-active');
                    }
                }

                parentContainer.appendChild(moduleDiv);

                if (groupContainer) updateCarouselGroup(groupContainer);
            }
        }
    } catch (err) {
        console.error('Failed to load active modules list:', err);
    }
}

loadActiveModules();
connect();

// Global Internet Connectivity Monitoring
function updateOfflineIndicator() {
    const indicator = document.getElementById('offline-indicator');
    if (!indicator) return;
    
    if (!navigator.onLine) {
        indicator.style.display = 'flex';
        // Small delay to ensure display:flex has applied before opacity transition
        setTimeout(() => indicator.style.opacity = '1', 10);
    } else {
        indicator.style.opacity = '0';
        setTimeout(() => { 
            if (navigator.onLine) indicator.style.display = 'none'; 
        }, 500);
    }
}

window.addEventListener('online', updateOfflineIndicator);
window.addEventListener('offline', updateOfflineIndicator);
updateOfflineIndicator(); // Initialize state on load
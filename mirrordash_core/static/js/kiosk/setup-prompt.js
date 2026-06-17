/**
 * Setup Prompt Web Component
 * Displays WiFi setup or admin setup prompt based on backend state.
 * Uses Shadow DOM for style isolation.
 */
class SetupPrompt extends HTMLElement {
    constructor() {
        super();
        this.attachShadow({ mode: 'open' });
        this.state = null;
    }

    connectedCallback() {
        this.renderSkeleton();
        this.checkStatus();
        this.interval = setInterval(() => this.checkStatus(), 10000);
    }

    disconnectedCallback() {
        if (this.interval) clearInterval(this.interval);
    }

    renderSkeleton() {
        this.shadowRoot.innerHTML = `
            <style>
                :host {
                    all: initial;
                    display: none;
                    position: fixed;
                    inset: 0;
                    z-index: 1000;
                    background: rgba(0,0,0,0.85);
                    backdrop-filter: blur(4px);
                    align-items: center;
                    justify-content: center;
                    opacity: 0;
                    transition: opacity 0.6s ease;
                    font-family: "Inter", system-ui, -apple-system, sans-serif;
                }
                .card {
                    max-width: 520px;
                    width: 90%;
                    background: rgba(0,0,0,0.93);
                    border-radius: 1rem;
                    padding: 2.5rem;
                    border: 1px solid var(--color-dimmed-charcoal, #666666);
                    text-align: center;
                }
                .icon {
                    margin-bottom: 1.5rem;
                }
                h1 {
                    font-size: 1.25rem;
                    font-weight: 600;
                    letter-spacing: 0.08em;
                    text-transform: uppercase;
                    color: var(--color-high-contrast, #ffffff);
                    margin-bottom: 0.75rem;
                }
                p {
                    font-size: 0.9375rem;
                    color: var(--color-standard-gray, #999999);
                    line-height: 1.6;
                    margin: 0 0 2rem 0;
                }
                .url-box {
                    background: var(--surface-container, #1f1f1f);
                    border-radius: 0.5rem;
                    padding: 1rem 1.5rem;
                    margin-bottom: 2rem;
                    font-family: monospace;
                    font-size: 1.0625rem;
                    color: var(--color-high-contrast, #ffffff);
                    letter-spacing: 0.04em;
                }
                .subtext {
                    font-size: 0.8125rem;
                    color: var(--color-dimmed-charcoal, #666666);
                    line-height: 1.5;
                    margin: 0;
                }
            </style>
            <div class="card">
                <div class="icon"><i data-lucide=""></i></div>
                <h1></h1>
                <p class="main-text"></p>
                <div class="url-box"></div>
                <p class="subtext"></p>
            </div>
        `;
    }

    async checkStatus() {
        try {
            const res = await fetch('/admin/auth/status');
            const data = await res.json();
            this.updateState(data);
        } catch (_) {}
    }

    updateState(data) {
        const wasHidden = this.state === null || !this.state.showPrompt;
        const icon = this.shadowRoot.querySelector('.icon i');
        const title = this.shadowRoot.querySelector('h1');
        const mainText = this.shadowRoot.querySelector('.main-text');
        const urlBox = this.shadowRoot.querySelector('.url-box');
        const subtext = this.shadowRoot.querySelector('.subtext');

        let showPrompt = false;

        if (data.wifi_hotspot_active) {
            showPrompt = true;
            icon.setAttribute('data-lucide', 'wifi');
            title.textContent = 'WiFi Setup Mode';
            mainText.textContent = 'Your mirror is offline. Connect your phone or computer to the WiFi network below to configure a connection:';
            urlBox.innerHTML = `<span style="color:var(--color-standard-gray,#999);font-size:0.875rem;">SSID:</span> MirrorDash-Setup<br><span style="color:var(--color-standard-gray,#999);font-size:0.875rem;">URL:</span> http://10.42.0.1:8000/wifi-setup`;
            subtext.textContent = 'Hotspot password: mirrordash. Open this URL in your browser to select your home Wi-Fi network.';
        } else if (data.setup_required) {
            showPrompt = true;
            icon.setAttribute('data-lucide', 'monitor-smartphone');
            title.textContent = 'Welcome to MirrorDash';
            mainText.textContent = 'Your mirror is running. Open a browser on any device connected to the same network and visit the admin dashboard to complete setup.';
            urlBox.textContent = 'mirrordash.local/admin';
            subtext.textContent = 'Set an admin password to secure your mirror and configure modules. This message will disappear automatically once setup is complete.';
        }

        if (window.lucide) {
            lucide.createIcons({ root: this.shadowRoot });
        }

        if (showPrompt) {
            if (wasHidden) {
                this.style.display = 'flex';
                requestAnimationFrame(() => requestAnimationFrame(() => { this.style.opacity = '1'; }));
            }
        } else {
            this.style.opacity = '0';
            setTimeout(() => { this.style.display = 'none'; }, 700);
        }

        this.state = { showPrompt };
    }
}

customElements.define('setup-prompt', SetupPrompt);
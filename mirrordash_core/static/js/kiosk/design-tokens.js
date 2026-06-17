/**
 * Design tokens for Shadow DOM module isolation.
 * Variables are inherited from :root in style.css, but we also provide
 * fallback values for offline scenarios or when styles don't propagate.
 */
const DESIGN_TOKENS_CSS = `
    :host {
        all: initial;
        display: block;
        color-scheme: dark;
        box-sizing: border-box;
        font-family: "Inter", system-ui, -apple-system, sans-serif;
    }
    :host *, :host *::before, :host *::after {
        box-sizing: border-box;
    }
    :host {
        --background: #131313;
        --on-background: #e2e2e2;
        --surface: #131313;
        --surface-dim: #131313;
        --surface-bright: #393939;
        --surface-container-lowest: #0e0e0e;
        --surface-container-low: #1b1b1b;
        --surface-container: #1f1f1f;
        --surface-container-high: #2a2a2a;
        --surface-container-highest: #353535;
        --on-surface: #e2e2e2;
        --on-surface-variant: #c4c7c8;
        --outline: #8e9192;
        --outline-variant: #444748;
        --color-void: #000000;
        --color-high-contrast: #ffffff;
        --color-standard-gray: #999999;
        --color-dimmed-charcoal: #666666;
        --color-error: #ffb4ab;
        --color-status-online: #a0ffba;
        --color-status-warning: #f87171;
        --safe-margin: 60px;
        --widget-gap: 30px;
        --internal-padding: 16px;
        --label-gap: 8px;
        --mirror-gutter: 2rem;
        --radius-sm: 0.125rem;
        --radius-default: 0.25rem;
        --radius-md: 0.375rem;
        --radius-lg: 0.5rem;
        --radius-xl: 0.75rem;
        --radius-container: var(--radius-default);
        --radius-alert: 1rem;
    }
    .fa {
        font-family: var(--fa-style-family, "Font Awesome 6 Free");
        font-weight: var(--fa-style, 900);
        -moz-osx-font-smoothing: grayscale;
        -webkit-font-smoothing: antialiased;
        display: var(--fa-display, inline-block);
        font-style: normal;
        font-variant: normal;
        line-height: 1;
        text-rendering: auto;
    }
`;
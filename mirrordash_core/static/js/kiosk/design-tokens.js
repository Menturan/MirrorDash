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
        --safe-margin-top: var(--safe-margin-top, 60px);
        --safe-margin-bottom: var(--safe-margin-bottom, 60px);
        --safe-margin-left: var(--safe-margin-left, 60px);
        --safe-margin-right: var(--safe-margin-right, 60px);
        --safe-margin: var(--safe-margin, 60px);
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
    /* Module building blocks: the same values as in style.css, which can't reach into a shadow root */
    .display-xl {
        font-size: 75px;
        font-weight: 100;
        line-height: 1.0;
        letter-spacing: -0.04em;
        color: var(--color-high-contrast, #ffffff);
    }
    .display-lg {
        font-size: 64px;
        font-weight: 300;
        line-height: 1.1;
        letter-spacing: -0.02em;
        color: var(--color-high-contrast, #ffffff);
    }
    .headline-md { font-size: 32px; font-weight: 500; line-height: 1.2; }
    .body-base   { font-size: 20px; font-weight: 400; line-height: 1.5; }
    .body-sm     { font-size: 16px; font-weight: 400; line-height: 1.4; }
    .text-primary   { color: var(--color-high-contrast, #ffffff); }
    .text-secondary { color: var(--color-standard-gray, #999999); }
    .text-dimmed    { color: var(--color-dimmed-charcoal, #666666); }
    .text-error     { color: var(--color-error, #ffb4ab); }
    .flex-row { display: flex; align-items: center; gap: 8px; }
    .flex-row-between { display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%; }
    .flex-column { display: flex; flex-direction: column; gap: 8px; }
    .flex-center { display: flex; align-items: center; justify-content: center; }
    /* A calm line with an icon: "API key missing", "Could not update", "Nothing today" */
    .module-message {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 16px;
        line-height: 1.4;
        color: var(--color-standard-gray, #999999);
    }
    .module-message svg { width: 18px; height: 18px; flex: none; stroke-width: 1.5px; }
    h2, .module-header, .label-caps {
        font-size: 0.875rem;
        font-weight: 600;
        line-height: 1.0;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: var(--color-standard-gray, #999999);
        border-bottom: 1px solid var(--color-dimmed-charcoal, #666666);
        padding-bottom: var(--label-gap, 8px);
        margin-bottom: var(--internal-padding, 16px);
        display: block;
        width: 100%;
    }
`;
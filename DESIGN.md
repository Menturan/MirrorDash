# MirrorDash Design System

The rules behind how the mirror looks, and why. The components themselves, with markup to copy, are in the library at `/design` on any mirror (source: `mirrordash_core/static/modules.css`); the mirror's own UI is in `style.css`, the admin page in `static/admin/admin.css`.

## Principles

- **Black is the glass.** A black pixel is an unlit pixel, invisible behind a two-way mirror. The background is always pure black, and only what should be seen gives off light.
- **The centre stays empty.** Information sits at the edges so the mirror is still a mirror. Only a temporary alert may use the centre.
- **Passive, not interactive.** Nobody touches the mirror: no cursor (`cursor: none`), no buttons, no hover states. Everything is changed from the admin page on a phone.
- **Calm.** Glanceable from across the room, nothing that blinks or competes for attention.

## Colours

| Token | Value | Use |
|---|---|---|
| `--color-void` | `#000000` | The background, always |
| `--color-high-contrast` | `#ffffff` | Primary values: the time, temperatures, headings. The only colour that cuts through room light |
| `--color-standard-gray` | `#999999` | Body text and secondary information |
| `--color-dimmed-charcoal` | `#666666` | Non-critical information, dividers, borders |
| `--color-ice-blue` | `#cceeff` | Cold or inactive, sparingly |
| `--color-rose-pink` | `#ffccd5` | Warm or urgent, sparingly |
| `--color-status-online` | `#a0ffba` | Online, active |
| `--color-status-warning` | `#f87171` | Warnings, a lost connection |
| `--color-error` | `#ffb4ab` | Error text |

Accents are low-saturation and rare: through glass, colour reads as noise long before white does.

## Typography

**Inter**, bundled with the mirror (`static/fonts/`), so no network is needed. Large text is thin so it doesn't glare; small text is heavier so the glass doesn't wash its strokes out.

| Class | Size / weight |
|---|---|
| `.display-xl` | 75px / 100 |
| `.display-lg` | 64px / 300 |
| `.headline-md` | 32px / 500 |
| `.body-base` | 20px / 400 |
| `.body-sm` | 16px / 400 |
| `.label-caps` | 14px / 600, uppercase, tracked, with a 1px `#666666` line below |

A module starts with a `label-caps` heading; separators are horizontal 1px lines, never vertical (use space instead).

## Layout

- **Safe margin:** 60px from every edge (`--safe-margin-*`, adjustable per mirror in the admin page), for the bezel and the frame.
- **Nine anchors:** `top_left` … `bottom_right`, each pinned to its corner, edge or centre. Modules grow away from their edge: top and middle regions downward, bottom regions upward (the first module hugs the edge). Regions are independent; nothing clips or pushes.
- **Rhythm:** 30px between stacked modules (`--widget-gap`), 16px inside (`--internal-padding`), 8px between a label and its value (`--label-gap`).
- **Flexible widths:** no fixed-width columns. Swedish or German strings are much longer than English, so use flex or grid with `min-width`/`flex: 1` and ellipsis.

## Depth and shape

Shadows are invisible in a mirror; depth comes from opacity and light.

- **Cards and alerts:** 93% black (`rgba(0, 0, 0, 0.93)`), hiding just enough of the reflection to make the text readable.
- **Glow** (`.glow`, a 4px white drop shadow) only for critical alerts; ambient data stays flat.
- **Behind a modal,** the modules are blurred 2px and dimmed to half brightness.
- **Corners:** 4px for ambient containers (`--radius-default`), 16px for alerts and system messages (`--radius-alert`), full for dots and dials.

## Motion, loading and errors

Loading is always visible: anything that waits shows calm motion (the breathing monogram during boot, the gliding line in `loading.html`, the pulsing connection dot, the admin's loading line, the Wi-Fi page's three dots). Anything that fails stops moving and says what happened and what to do; a wait that never ends turns into an error (`loading.html` after 4 minutes, the mirror's connection status after 2). Motion respects `prefers-reduced-motion` by slowing down, never by disappearing.

## Monogram and boot screens

- **Monogram:** the line monogram "MD" (`static/favicon.svg`; path in `scripts/render_boot_images.py`), white on pure black. Small sizes (favicon) use a heavier stroke so it stays legible.
- **The monogram says "you are in MirrorDash"** and is always on our own surfaces: the favicon and app icon, the admin page (sidebar, and the top of the page on a phone), its login, the Wi-Fi setup, the boot, restart and shutdown screens, `loading.html` and the README. It is never on the mirror's screen next to the modules: there the mirror should look like a mirror.
- **Boot, restart and shutdown:** Plymouth shows `splash.png` (the monogram alone), `restart.png` and `shutdown.png` (monogram plus one quiet word), all rendered by `scripts/render_boot_images.py`. They are small and pure black, so Plymouth shows them at their own size on any screen (never scaled, never a visible box). The kiosk's first page, `static/loading.html`, draws the monogram at exactly the same size and place, so the handover from Plymouth to the mirror is seamless; its only motion is a slow breathing pulse.

## Icons

- **The mirror and modules:** [Lucide](https://lucide.dev/icons) outlines (`<i data-lucide="cloud-rain"></i>`), 1.5px stroke, no fill, in the text colour. No emoji and no coloured icons.
- **The admin page:** the bundled Font Awesome (`static/fontawesome/`).

## The admin page

A phone page, not the mirror, but the same family: near-black, white type, **white as the only accent** (the active tab, a primary button, focus rings). Colour means status only: `#a0ffba` fine, `#f59e0b` needs a look, `#f87171` wrong or destructive. One filled white button per card is its main action; everything else is outlined. A card's title names what's in it, never the page it's on.

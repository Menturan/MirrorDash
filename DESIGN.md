---
name: Ethereal Mirror
colors:
  surface: '#131313'
  surface-dim: '#131313'
  surface-bright: '#393939'
  surface-container-lowest: '#0e0e0e'
  surface-container-low: '#1b1b1b'
  surface-container: '#1f1f1f'
  surface-container-high: '#2a2a2a'
  surface-container-highest: '#353535'
  on-surface: '#e2e2e2'
  on-surface-variant: '#c4c7c8'
  inverse-surface: '#e2e2e2'
  inverse-on-surface: '#303030'
  outline: '#8e9192'
  outline-variant: '#444748'
  surface-tint: '#c6c6c7'
  primary: '#ffffff'
  on-primary: '#2f3131'
  primary-container: '#e2e2e2'
  on-primary-container: '#636565'
  inverse-primary: '#5d5f5f'
  secondary: '#c7c6c6'
  on-secondary: '#2f3131'
  secondary-container: '#464747'
  on-secondary-container: '#b5b5b5'
  tertiary: '#ffffff'
  on-tertiary: '#303031'
  tertiary-container: '#e4e2e2'
  on-tertiary-container: '#646464'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#e2e2e2'
  primary-fixed-dim: '#c6c6c7'
  on-primary-fixed: '#1a1c1c'
  on-primary-fixed-variant: '#454747'
  secondary-fixed: '#e3e2e2'
  secondary-fixed-dim: '#c7c6c6'
  on-secondary-fixed: '#1a1c1c'
  on-secondary-fixed-variant: '#464747'
  tertiary-fixed: '#e4e2e2'
  tertiary-fixed-dim: '#c7c6c6'
  on-tertiary-fixed: '#1b1c1c'
  on-tertiary-fixed-variant: '#464747'
  background: '#131313'
  on-background: '#e2e2e2'
  surface-variant: '#353535'
typography:
  display-xl:
    fontFamily: Inter
    fontSize: 75px
    fontWeight: '100'
    lineHeight: '1.0'
    letterSpacing: -0.04em
  display-lg:
    fontFamily: Inter
    fontSize: 64px
    fontWeight: '300'
    lineHeight: '1.1'
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '500'
    lineHeight: '1.2'
    letterSpacing: '0'
  body-base:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '400'
    lineHeight: '1.5'
    letterSpacing: '0'
  body-sm:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: '1.4'
    letterSpacing: '0'
  label-caps:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '600'
    lineHeight: '1.0'
    letterSpacing: 0.1em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  safe-margin: 60px
  widget-gap: 30px
  internal-padding: 16px
  label-gap: 8px

## Table of Contents

- [Brand & Style](#brand--style)
- [Colors](#colors)
- [Typography](#typography)
- [Layout & Spacing](#layout--spacing)
- [Elevation & Depth](#elevation--depth)
- [Shapes](#shapes)
- [Components](#components)
- [Iconography](#iconography)

## Brand & Style

The design system is engineered for ambient, glanceable interfaces viewed through semi-reflective glass. The brand personality is **Futuristic, Sophisticated, and Unobtrusive**. It adopts a **Minimalist-Holographic** style, where the interface exists as floating light rather than physical digital surfaces. 

The primary goal is to maintain the utility of a physical mirror while overlaying high-value information. By leveraging a "Zero-Light" philosophy, every design decision prioritizes high-contrast legibility against a deep black void, creating a heads-up display (HUD) that feels integrated into the user's environment.

**Key Stylistic Pillars:**
- **Zero-Light Background:** Pure black surfaces ensure pixels are off, allowing the mirror's reflectivity to remain functional.
- **Atmospheric Clarity:** Pushing information to the periphery preserves the center for physical reflection.
- **Technical Precision:** Sharp lines, refined typography, and purposeful spacing evoke a sense of high-end aerospace or medical instrumentation.
- **Passive Presentation:** The mirror display is a passive HUD, not an interactive interface. The mouse cursor is globally hidden (`cursor: none;`) to maintain a clean visual look.

## Colors

The palette is strictly functional, optimized for light transmission through two-way glass. 

- **The Void (#000000):** The mandatory background color. It must be absolute black to ensure the screen remains invisible behind the mirror.
- **High-Contrast White (#ffffff):** Used for primary data points like the current time, active temperatures, and headers. This is the only color that "breaks" through ambient room light effectively.
- **Standard Gray (#999999):** The baseline for secondary information and body text.
- **Dimmed Charcoal (#666666):** Used for non-critical information, dividers, and subtle borders.
- **Functional Accents:** Low-saturation tints are used sparingly for data visualization. **Soft Ice Blue** signifies cold or inactive states, while **Soft Rose Pink** signifies warmth or urgent alerts.

## Typography

Typography is the primary vehicle for the UI. We use **Inter** for its exceptional legibility and modern, neutral character. 

**Hierarchical Strategy:**
- **Thin for Large:** To prevent excessive light glare, display sizes use thin weights (`100`–`300`). This maintains an "etched in glass" look.
- **Medium for Small:** As font size decreases, weights increase slightly to ensure the physical glass doesn't wash out thin strokes.
- **Uppercase Labels:** Section headers use tracked-out uppercase styles with a `1px` bottom border to create structural anchors in a grid-less environment.

## Layout & Spacing

The layout follows a **Peripheral Modular Grid**. Content is strictly prohibited from the center of the screen to maintain the mirror's primary function.

- **Hardware Safety Boundary:** A mandatory `60px` margin is applied to all four edges of the viewport to account for physical monitor bezels and frame overlap.
- **Regional Anchoring:** Modules are snapped to a symmetric 3x3 grid containing regions: `top_left`, `top_center`, `top_right`, `middle_left`, `middle_center`, `middle_right`, `bottom_left`, `bottom_center`, and `bottom_right`. 
- **Modular Rhythm:** A `30px` vertical gap is maintained between stacked widgets. 
- **Center Void:** The horizontal and vertical center of the screen should remain unoccupied unless a temporary modal alert is triggered.
- **Responsive Fluidity:** All module layouts must be designed to be as responsive and flexible as possible. Avoid hardcoded fixed-width columns (e.g. in lists or forecast rows) because localized strings in other languages (such as Swedish or German) can be significantly longer than their English counterparts. Use flexbox or CSS Grid with flexible sizing (`flex: 1`, `min-width`, `max-content`) and text truncation utilities (`text-overflow: ellipsis`) to handle arbitrary string lengths gracefully.

## Elevation & Depth

In a mirror environment, traditional shadows are invisible. Depth is instead communicated through **Tonal Opacity and Glow**.

- **Surface Layers:** Containers (like notification cards) use a `93%` opaque black fill. This creates a "cut-out" effect that obscures the mirror reflection just enough to prioritize the text.

- **Atmospheric Glow:** Important elements (like active alerts) may use a very subtle, tight white outer glow (`blur: 4px`) to simulate light bleeding through the glass.
- **Backdrop Blur:** During critical system alerts, the background modules are softened with a `2px` blur and `50%` brightness reduction to pull focus to the foreground modal.

## Shapes

The shape language is **Precision Geometric**. 

- **Containers:** Most widgets and modules are sharp-edged or use a very subtle `0.25rem` (4px) radius to maintain a technical, HUD-like feel.
- **Alerts & Modals:** Temporary, high-priority notifications use a more pronounced `1rem` (16px) radius to distinguish "system" messages from "ambient" data.
- **Analog Elements:** Elements like clock faces or status pips use a `full` (pill) radius for perfect circularity.

## Components

Components are designed for **Passive Observation** rather than active interaction.

- **Widgets:** Every widget starts with a `label-caps` header followed by a `1px` border in `#666666`. Content below follows a list or grid format.
- **Notification Cards:** Use a `rgba(0, 0, 0, 0.93)` background and `16px` of internal padding.
- **Data Lists:** Use a simple table or flex-row layout. Labels are left-aligned in `#999999`, while primary values (temperatures, times) are right-aligned in `#ffffff`.
- **Clock Module:** The centerpiece. Digital hours and minutes are grouped tightly with `display-xl` sizing. Seconds are rendered in `headline-md` using `#666666` to reduce visual noise.
- **Status Indicators:** Small 8px circles. Green (`#a0ffba`) for "online/active", and the primary `accent_warm` for "error/warning".
- **Separators**: Use horizontal lines `1px` thick in `#666666`. Never use vertical separators; use whitespace instead.
- **Carousel Containers**: Use a `.carousel-group-container` wrapper with individual widgets marked as `.carousel-slide`. The slides are layered atop each other in a single grid cell (`grid-area: 1 / 1 / 2 / 2`) and cross-fade smoothly using `opacity` and `visibility` over a `0.8s` ease-in-out curve to prevent vertical layout shifting.
- **Boot Splash Screen**: Represented by the design system asset [splash.png](file:///home/menturan/repos/mymagicmirror/mirrordash_core/static/splash.png) (1280x1024). It features a centered monochromatic MirrorDash monogram logo, tracking uppercase brand headers, a pulsating initialization state, and a minimalist loader bar. It is integrated as the early boot screen using Plymouth to guarantee a professional visual startup experience.

## Iconography

Icons are used as minimalist glyphs to represent data contexts (e.g. weather, connection states).

- **Format:** SVG or modern font-free vector outline icon systems—specifically **Lucide Icons** (searchable at [lucide.dev/icons](https://lucide.dev/icons))—to ensure pixel-perfect rendering behind semi-reflective glass without high-contrast glares.
- **Color:** Monochromatic, inheriting from text colors (`--color-high-contrast`, `--color-standard-gray`, or `--color-dimmed-charcoal`). Colored or multi-color emojis are strictly prohibited.
- **Stroke/Weight:** Thin outlines (default `1.5px` stroke weight) to align with display typography weights (`100`–`300`). Solid fills are avoided to maintain floating light aesthetics.
- **Atmospheric Glow:** Critical system alert icons should receive a subtle `.glow` filter, while ambient data icons should remain flat.
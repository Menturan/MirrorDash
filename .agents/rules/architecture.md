# Architecture Patterns

## Design System

The design rules and tokens are in `DESIGN.md`, implemented in `mirrordash_core/static/style.css` (the mirror's own UI) and `modules.css` (what modules may use). The components themselves are documented in the `/design` library (`static/design.html`), not in DESIGN.md.

- **Never put module-specific CSS in `mirrordash_core/static/style.css`.** Module styles belong inside a `<style>` block in the module's own Jinja2 template.
- **Keep DESIGN.md design-system-only.** Do not add documentation, parameters, or configurations for specific modules to `DESIGN.md`. It must contain only the overall core design principles, layout grids, colors, typography, and shapes. Specific module documentation belongs in the module's own `README.md` or in `MODULE_GUIDE.md`.
- CSS custom properties (variables) defined in `:root` in `style.css` are available in all module templates.
- The design values to know: background is always `#000000`, primary text `#ffffff`, secondary text `#999999`, dimmed `#666666`.
- **No outer borders on modules.** Modules must never have borders (e.g., `1px #666` or similar) to preserve the clean, grid-less "floating light" design.
- **Header formatting.** Module/section headers (`.label-caps`, `h2`, `.module-header`) should have tracked-out uppercase styling and a `1px` bottom border in `var(--color-dimmed-charcoal)` (`#666666`) to serve as structural anchors.
- **Card containers & notifications.** Notification cards use a `93%` opaque black fill with `16px` of internal padding and a `1rem` (`var(--radius-alert)`) border radius.
- **Backdrop blur.** System modals or alerts use `.backdrop-blur-active` to blur background widgets (blur `2px`, brightness `50%`).

## Core Modifications

The core is split by feature (vertical slices, ARCHITECTURE #27):

- **A feature is a folder:** `mirrordash_core/features/<feature>/` holds its routes (`routes.py`), its logic and its `templates/`. A new feature is a new folder plus one line in `app.py`; changing a feature should touch one folder.
- **Shared code sits at the package root** (`admin.py`, `config.py`, `forms.py`, `venv.py`, `host.py`, `fetch.py`, `system_settings.py`, `event_bus.py`). Only what two or more features use goes there.
- **No route without a caller.** The admin panels call feature functions directly; add an HTTP route only for something that requests it (a template, the kiosk, Home Assistant). Every admin route has the `require_api_key` dependency.
- **`features/modules/loader.py`:** Any new function defined inside the `start_modules()` loop — check for closure bugs. Use factories.
- **`features/kiosk/ws.py`:** Frame cache (`latest_messages`) only stores messages with both `"module"` and `"html"` keys. `clear_cache()` is called on `stop_modules()`.
- **Package changes** (install, update, uninstall, rebuild) go through `venv.venv_swap()`, never straight into the active venv.
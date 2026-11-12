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
- **Shared code sits at the package root** (`admin.py`, `config.py`, `forms.py`, `venv.py`, `host.py`, `fetch.py`, `screen.py`, `notifications.py`, `event_bus.py`). Only what two or more features use goes there, and it imports no feature (see Layers).
- **No route without a caller.** The admin panels call feature functions directly; add an HTTP route only for something that requests it (a template, the kiosk, Home Assistant). Every admin route has the `require_api_key` dependency, except the ones that must work before or without a login: the page shell (`GET /admin`), `/admin/auth/status|setup|login|logout|recover`, `POST /admin/auto-update` (loopback only: the nightly timer), and the old open `POST /admin/screen` (kept for existing Home Assistant automations; new ones use the token-protected `/api/v1/screen`, ARCHITECTURE #24).
- **`features/modules/loader.py`:** Any new function defined inside the `start_modules()` loop — check for closure bugs. Use factories.
- **`screen.py`:** Frame cache (`latest_messages`) only stores messages with both `"module"` and `"html"` keys. `clear_cache()` is called by `reload_modules()` (`features/modules/client.py`), before the screen reloads.
- **Package changes** go into a copy that is switched in only when it works: the app's own through `venv.venv_swap()`, modules (install, update, uninstall) through `module_host.packages()` (the next module site, `features/modules/client.py`). Never straight into the active venv or site.

## Layers

Which part of the core may import which (ARCHITECTURE #59); `tests/test_import_layers.py` checks it over every import, also the ones inside a function.

- **The package root imports no feature.** Only `app.py` (puts the app together) and `main.py` (starts it) do.
- **No two features import each other.** A feature imports the root and other features, never one that imports it back. The one exception is `modules` and `power`: one topic in two folders, imports inside functions only.
- **When the test fails**, don't add to its lists (they only shrink). Instead:
  - both features need the same thing: move it down to the root (`host.py`, `screen.py`) or to the feature that owns it (as `features/modules/installed.py`);
  - a lower part has to tell a higher one something: send an event (`event_bus.publish`) or add a hook, and connect it in `app.py` (`event_bus.subscribe("hardware.button", buttons.on_button)`, `module_host.on_state = mqtt_bridge.module_state`);
  - a route sits in the wrong feature: move it to the one whose logic it runs (as `features/backup/setup_restore.py`).
- **An import inside a function** is for a slow or optional library, not for hiding a circle.

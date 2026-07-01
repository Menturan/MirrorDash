# Architecture Patterns

## Design System

The visual system is documented in `DESIGN.md` and implemented in `mirrordash_core/static/style.css`. These two files must always be kept in sync.

- **Never put module-specific CSS in `mirrordash_core/static/style.css`.** Module styles belong inside a `<style>` block in the module's own Jinja2 template.
- **Keep DESIGN.md design-system-only.** Do not add documentation, parameters, or configurations for specific modules to `DESIGN.md`. It must contain only the overall core design principles, layout grids, colors, typography, and shapes. Specific module documentation belongs in the module's own `README.md` or in `MODULE_GUIDE.md`.
- CSS custom properties (variables) defined in `:root` in `style.css` are available in all module templates.
- The design values to know: background is always `#000000`, primary text `#ffffff`, secondary text `#999999`, dimmed `#666666`.
- **No outer borders on modules.** Modules must never have borders (e.g., `1px #666` or similar) to preserve the clean, grid-less "floating light" design.
- **Header formatting.** Module/section headers (`.label-caps`, `h2`, `.module-header`) should have tracked-out uppercase styling and a `1px` bottom border in `var(--color-dimmed-charcoal)` (`#666666`) to serve as structural anchors.
- **Card containers & notifications.** Notification cards use a `93%` opaque black fill with `16px` of internal padding and a `1rem` (`var(--radius-alert)`) border radius.
- **Backdrop blur.** System modals or alerts use `.backdrop-blur-active` to blur background widgets (blur `2px`, brightness `50%`).

## Core Modifications

When modifying `mirrordash_core/`:

- **`module_loader.py`:** Any new function defined inside `start_modules()` loop — check for closure bugs. Use factories.
- **`ws_manager.py`:** Frame cache (`latest_messages`) only stores messages with both `"module"` and `"html"` keys. `clear_cache()` is called on `stop_modules()`.
- **`app.py`:** Public routes go directly on `app`. Admin routes go in `mirrordash_core/api/` sub-routers (`admin_auth.py`, `admin_system.py`, etc.) and `mirrordash_core/api/backup.py` with the `require_api_key` dependency.
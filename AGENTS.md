# AGENTS.md — MirrorDash AI Agent Guide

This file provides AI coding agents with everything they need to work effectively on the MirrorDash codebase. Read this before making any changes.

> [!IMPORTANT]
> **Remember to always follow the design rules.** Adherence to the visual design rules, system constraints, and aesthetic/contrast guidelines is critical. See `DESIGN.md` and `static/style.css` for details.

---

## Project Overview

MirrorDash is a modular, ambient information display system designed for Raspberry Pi running in Kiosk mode. The backend is a FastAPI/Python server. The frontend is pure HTML, CSS, and JavaScript served to Chromium in full-screen kiosk mode.

**Key principle:** The mirror is a passive, glanceable display — not an interactive application.

---

## Repository Layout

```
/
├── mirrordash_core/              # Core FastAPI backend
│   ├── app.py              # FastAPI app, all routes, WebSocket endpoint
│   ├── main.py             # Uvicorn server entry point
│   ├── module_loader.py    # Entry point discovery, plugin lifecycle, auto-injection
│   ├── ws_manager.py       # WebSocket ConnectionManager with frame cache
│   ├── config.py           # Config file loading and ROOT_DIR path
│   ├── event_bus.py        # Async pub/sub inter-module event bus
│   ├── system.py           # System controls (remount, brightness, rotation)
│   ├── display_power.py    # Screen power manager daemon (schedule/PIR/button)
│   └── api/
│       ├── admin.py        # Admin REST API (requires X-API-Key header)
│       └── backup.py       # Backup/restore REST API
├── modules/                # Each module is its own pip-installable Python package
│   └── mirrordash-clock/         # Ticking clock/date widget with Babel localization
├── static/                 # Static files served at /static/*
│   ├── index.html          # Mirror display (the kiosk page)
│   ├── design.html         # Design System Explorer (served at /design)
│   ├── admin_js/           # Admin dashboard split JavaScript files
│   ├── style.css           # Global Ethereal Design System CSS
│   └── admin.css           # Styles specific to the admin dashboard
├── templates/              # Jinja2 templates (rendered server-side)
│   ├── admin.html          # Main admin dashboard layout wrapper
│   └── admin_*.html        # Modular sub-panels for admin dashboard tabs
├── tests/                  # Pytest unit & integration test suite
├── config.json             # Runtime configuration (dev mode; relocates to ~/.mirrordash/data/config.json in package mode)
├── DESIGN.md               # Design system specification — always keep in sync with style.css
├── ARCHITECTURE.md         # Record of all architectural decisions
├── USER_GUIDE.md           # End-user setup and administration guide
├── CHANGELOG.md            # Semantic version log of releases
├── ISSUES.md               # Tracking known issues and work items
└── AGENTS.md               # This file
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.14, FastAPI, Uvicorn |
| Package Management | `uv` (not pip directly) |
| Frontend | Vanilla HTML5, CSS Grid, Vanilla JS (no frameworks) |
| Templating | Jinja2 (server-side, rendered per module) |
| Real-time | WebSockets (one persistent connection per browser client) |
| Module System | Python `importlib.metadata` entry points (`mirrordash.modules` group) |
| Deployment | Raspberry Pi OS Trixie (Debian 13), OverlayFS read-only rootfs, Wayland (labwc), Chromium kiosk |

---

## Running Locally

```bash
source .venv/bin/activate
python mirrordash_core/main.py
```

**URLs:**
- `http://localhost:8000/` — Mirror display
- `http://localhost:8000/admin` — Admin dashboard
- `http://localhost:8000/design` — Design System Explorer
- `http://localhost:8000/health` — Health check JSON
- `http://localhost:8000/api/active-modules` — Active modules metadata (public)

---

## Critical Coding Rules

### Python

1. **No late-binding closures in loops.** Any nested function (`def`) defined inside a `for` loop that closes over a loop variable MUST use a factory function to capture the value immediately. This is a known bug pattern in this codebase.
   ```python
   # WRONG — all instances share the last value of `plugin_instance`
   def translate(key): return plugin_instance.translations.get(key)

   # CORRECT — each call captures its own bound instance
   def make_translate(bound):
       def translate(key, default=None): return bound.translations.get(key, default)
       return translate
   plugin_instance.translate = make_translate(plugin_instance)
   ```

2. **Never write files to arbitrary OS paths.** The OS root is read-only in production (OverlayFS). All persistent configuration and data MUST be written to `~/.mirrordash/data/` (which is mapped to a persistent partition). Use `self.data_dir` for persistent module files and `self.cache_dir` for temporary RAM-based files. Writing to `/etc`, `/var`, or other root paths will result in data loss upon reboot.

3. **Strict Sudo Allow-List.** Debian Trixie disables passwordless sudo by default. All `sudo` subprocess calls made by the application are strictly regulated via an explicit allow-list in `/etc/sudoers.d/mirrordash` (documented in `GOLDEN_IMAGE.md`). Never add a new `sudo` command to the codebase without also updating the golden image documentation to allow it.

4. **All HTTP network requests inside modules must be non-blocking.** Use `asyncio.to_thread(sync_func)` to wrap synchronous I/O (like `urllib.request`) or use an async HTTP library.

5. **Modules must handle `asyncio.CancelledError` cleanly.** Always re-raise `CancelledError` after cleanup to allow graceful shutdown.
   ```python
   except asyncio.CancelledError:
       logger.info("Module stopped.")
       raise
   ```

6. **Config fallback order:** Module-specific config → global config (`config.get("globals", {})`) → hardcoded default.

### System & Display

7. **Wayland First.** The system uses Wayland (`labwc`) as the display server on Debian Trixie. Always prioritize Wayland-compatible commands (e.g., `wlr-randr`) over legacy X11 commands (`xset`, `xrandr`) for display management logic.

### Frontend

8. **No frameworks.** The mirror frontend is intentionally framework-free. Do not introduce React, Vue, Alpine, HTMX, or similar.

9. **No `document.write()` or `eval()`.**

10. **All JSON received over WebSocket must be parsed with try/catch.** See the existing `socket.onmessage` handler in `index.html`.

11. **WebSocket reconnection uses exponential backoff.** Do not replace with simple `setInterval`. The existing pattern in `index.html` is correct.

12. **Vector Iconography (Lucide).** Use Lucide outline icons via the `data-lucide` markup attribute (e.g., `<i data-lucide="sun"></i>`) instead of colored or multi-color emojis.

13. **Icon Re-Rendering.** When injecting HTML dynamically (e.g., updating modules via WebSockets or JS), you must trigger `lucide.createIcons()` after the DOM updates so the vector glyphs are rendered on the client side.

14. **Responsive Fluidity & Translation Safety.** All module templates/layouts must be designed to be as responsive and flexible as possible. Avoid hardcoded fixed-width columns (e.g. in lists or forecast rows) because localized strings in other languages (such as Swedish or German) can be significantly longer than their English counterparts. Use flexbox or CSS Grid with flexible sizing (`flex: 1`, `min-width`, `max-content`) and text truncation utilities (`text-overflow: ellipsis`) to handle arbitrary string lengths gracefully.

### Git
 
15. **Always commit with `--no-gpg-sign`** — GPG signing is disabled on this machine.
    ```bash
    git commit --no-gpg-sign -m "feat: ..."
    ```
 
16. **Follow conventional commits:** `feat:`, `fix:`, `refactor:`, `style:`, `docs:`, `chore:`.

### Testing

17. **Smart & Targeted Test Execution.**
    - **Required for Logic Changes**: Unit/integration tests in the `tests/` directory are mandatory for any backend/frontend code modifications, logic updates, or bug fixes.
    - **Bypass for Non-Logic Changes**: You MUST NOT run backend tests if modifications are strictly limited to documentation (e.g., `.md` files), inline code comments, pure styling (CSS), static assets, or templates with no execution logic.
    - **Targeted Test Runs**: During development, run only the relevant test file or specific test cases (e.g., `.venv/bin/pytest tests/test_event_bus.py` or `.venv/bin/pytest -k <test_name>`) instead of running the entire suite. Only run the full suite (`.venv/bin/pytest`) as a final validation step before completion.


### General

18. **Architectural Patterns & Refactoring.** Always follow well-known code architectural patterns and perform necessary refactoring to keep the codebase clean, modular, and well-formatted.

19. **Context Size Warnings.** Monitor and inform the user if the model context starts getting excessively large, so they are aware of potential token exhaustion or truncation limits.

20. **Remember to always follow the design rules.** Adherence to the visual design rules, system constraints, and accessibility requirements (such as high-contrast button readability) defined in `DESIGN.md`, `AGENTS.md`, and CSS files is mandatory when styling or modifying layouts/components.

21. **Document all new features immediately.** Any new features, configurations, or design system classes must be documented across all relevant documentation files (`ARCHITECTURE.md`, `DESIGN.md`, `MODULE_GUIDE.md`, `AGENTS.md`, and `CHANGELOG.md`) immediately to prevent documentation drift.

22. **Follow global configurations.** Always respect, load, and inherit settings defined under "global configuration" (such as language, timezone, time format, temperature/distance units, and coordinate location) when formatting, styling, or building logic for modules or core dashboard features.

23. **Minimize dependency inflation and prefer robust standard libraries.** Prefer utilizing well-known, popular, and robust packages (e.g. `Babel` for localization, `pytest` for tests) to avoid reinventing the wheel for complex domain tasks. However, remain conservative—do not add dependencies for simple utilities that can be written in a few lines of clean, native code. Make well-considered dependency decisions.

24. **Always use the mirrordash-cli scaffolder when creating new modules.** Never manually scaffold module directories from scratch. Always run the `mirrordash-cli create-module mirrordash-<name> --description "<desc>"` command to ensure a fully compatible packaging, template directory, and entry point layout structure is generated automatically.

25. **Professional Grade IoT & Consumer Simplicity.** The system must operate with professional-grade IoT reliability, designed to run continuously for years without manual intervention, memory leaks, or filesystem corruption. Every user-facing interface, including the captive portal network setup wizard, must be designed with ultimate simplicity in mind, ensuring that non-technical users (such as your parents) can use and configure the device safely, intuitively, and without command-line access.

26. **Disk Space Boundary Monitoring.** The virtual environment `.venv` resides on the root filesystem (ext4) which is capped at 6GB in the golden image. Any operations modifying packages (like installing or upgrading modules) must check free space (via the `/admin/disk-usage` endpoint). Always display a warning visual when remaining root partition free space drops below 500MB to avoid system crashes on OverlayFS.

27. **Deploy Automations & Scripting.** When writing golden image or system administration guides, provide a single setup script (e.g., `scripts/setup_appliance.sh`) in the repository and group manual instructions into chained commands using `&&` to minimize copy-paste errors and user friction.

---

## Architecture Patterns

### Module Lifecycle

1. `module_loader.start_modules()` scans entry points at startup.
2. For each enabled module: loads class, injects config, creates `instances[name]`, auto-injects `render_template`, `translate`, dirs, event bus.
3. Spawns an `asyncio.Task` with auto-restart on crash (`run_with_recovery`).
4. Each module loop calls `await broadcast_func(name, html)`.
5. `broadcast_func` reads the cached config and calls `manager.broadcast()` with a payload containing `position`, `html`, `module`, `carousel_group`, and `carousel_interval`.
6. `ConnectionManager.broadcast()` caches the latest frame in `latest_messages[module]` and sends to all connected clients.
7. On new WebSocket connection, `ConnectionManager.connect()` replays all cached frames instantly.

### API Route Protection

- Routes under `/admin/*` (which includes `/admin/backup/*` endpoints) require the password in the `X-API-Key` HTTP header.
- `GET /api/active-modules` is **intentionally public** — the mirror kiosk fetches it unauthenticated on load to show loading skeletons.
- `GET /health` is also public.

### Design System

The visual system is documented in `DESIGN.md` and implemented in `static/style.css`. These two files must always be kept in sync.

- **Never put module-specific CSS in `static/style.css`.** Module styles belong inside a `<style>` block in the module's own Jinja2 template (e.g., `templates/widget.html`).
- **Keep DESIGN.md design-system-only.** Do not add documentation, parameters, or configurations for specific modules to `DESIGN.md`. It must contain only the overall core design principles, layout grids, colors, typography, and shapes. Specific module documentation belongs in the module's own `README.md` or in `MODULE_GUIDE.md`.
- CSS custom properties (variables) defined in `:root` in `style.css` are available in all module templates.
- The design values to know: background is always `#000000`, primary text `#ffffff`, secondary text `#999999`, dimmed `#666666`.
- **No outer borders on modules.** Modules must never have borders (e.g., `1px #666` or similar) to preserve the clean, grid-less "floating light" design.
- **Header formatting.** Module/section headers (`.label-caps`, `h2`, `.module-header`) should have tracked-out uppercase styling and a `1px` bottom border in `var(--color-dimmed-charcoal)` (`#666666`) to serve as structural anchors.
- **Card containers & notifications.** Notification cards use a `93%` opaque black fill with `16px` of internal padding and a `1rem` (`var(--radius-alert)`) border radius.
- **Backdrop blur.** System modals or alerts use `.backdrop-blur-active` to blur background widgets (blur `2px`, brightness `50%`).

---

## Adding a New Module

To create a new module, run the `mirrordash-cli` tool. 

### Method A: Run without cloning/installing (via `uvx`)
```bash
# Run directly from PyPI
uvx mirrordash-cli create-module mirrordash-my-widget --description "My widget"

# Or run directly from Git
uvx --from git+https://github.com/menturan/mirrordash-sdk.git mirrordash-cli create-module mirrordash-my-widget --description "My widget"
```

### Method B: Run from local checkout
```bash
# 1. Install the CLI tool in editable mode (from the mirrordash-sdk directory)
uv pip install -e /home/menturan/repos/mirrordash-sdk

# 2. Scaffold a new module (run from the core project root)
mirrordash-cli create-module mirrordash-my-widget --description "My widget"
```

### Next Steps:
```bash
# Install the module in editable mode
uv pip install -e ./modules/mirrordash-my-widget

# 4. Enable in config.json
# Add entry under "modules" with "position", "enabled", "interval"

# 5. Restart server
```

A valid module must:
- Have a class registered under `[project.entry-points."mirrordash.modules"]` in `pyproject.toml`.
- Implement `__init__(self, config)` and `async def run_loop(self, broadcast_func)`.
- Call `await broadcast_func(self.name, html_string)` in a loop.
- Encapsulate all module-specific CSS inside its own template `<style>` block.

---

## Modifying Core Behaviour

When modifying `mirrordash_core/`:

- **`module_loader.py`:** Any new function defined inside `start_modules()` loop — check for closure bugs. Use factories.
- **`ws_manager.py`:** Frame cache (`latest_messages`) only stores messages with both `"module"` and `"html"` keys. `clear_cache()` is called on `stop_modules()`.
- **`app.py`:** Public routes go directly on `app`. Admin routes go in `mirrordash_core/api/admin.py` and `mirrordash_core/api/backup.py` with the `require_api_key` dependency.

---

## Design System Explorer

The file `static/design.html` (served at `/design`) is a live component kitchen-sink for module developers. When adding new CSS components to `style.css`:

1. Add the CSS classes to `static/style.css`.
2. Document them in `DESIGN.md` under the relevant section.
3. Add a live rendered example with a copyable code snippet in `static/design.html`.

---

## Documentation Files to Keep Updated

| File | Update when... |
|------|---------------|
| `ARCHITECTURE.md` | A new architectural decision is made or an existing one changes |
| `DESIGN.md` | Any CSS component is added, removed, or renamed in `style.css` |
| `MODULE_GUIDE.md` (in `mirrordash-sdk` repo) | The **module developer API** changes — new injected helpers, lifecycle hooks, config schema format, or storage conventions. **Not** for documenting specific modules. |
| `MODULE_AGENTS.md` (in `mirrordash-sdk` repo) | Any new module-specific coding rules, constraints, or scaffolding conventions are established |
| `modules/<name>/README.md` | A specific module's config keys, providers, API key instructions, or features change |
| `USER_GUIDE.md` | Any end-user features, settings tabs, configuration keys, or layout rules change |
| `AGENTS.md` | New coding rules, patterns, or constraints are established |
| `CHANGELOG.md` | Any notable feature, bug fix, or codebase change is committed |

---

## Common Pitfalls
 
| Pitfall | Correct approach |
|---------|-----------------|
| Writing CSS in `style.css` for a specific module | Put it in the module's `<style>` tag in its template |
| Using `time.sleep()` in an async module | Use `await asyncio.sleep()` |
| Blocking HTTP calls in `run_loop` | Wrap in `asyncio.to_thread()` |
| Defining a nested `def` in a loop without a factory | Use a factory function to capture scope |
| Using `float` or `int` for pixel values in Jinja2 templates | Format explicitly: `{{ value | int }}px` |
| Adding module-specific documentation to `DESIGN.md` | Keep `DESIGN.md` design-system-only; module docs go in the module's own `README.md` |
| Adding module-specific documentation to `MODULE_GUIDE.md` | `MODULE_GUIDE.md` (in `mirrordash-sdk` repo) covers the module developer API only (helpers, lifecycle, schema format); module-specific config, providers, and API key instructions go in the module's own `README.md` |
| Committing without `--no-gpg-sign` | Always use `git commit --no-gpg-sign` |
| Using colored emojis in widgets or notifications | Use Lucide outline icons (`data-lucide="icon-name"`) with `1.5px` stroke width. |
| Expecting icons to render automatically in dynamic templates | Invoke `lucide.createIcons()` after dynamically injecting markup. |
| Putting borders around modules/widgets | Keep background transparent and avoid borders to respect the grid-less HUD aesthetic. |
| Committing new features/fixes without tests, or running the full test suite for minor/non-logic changes | Write/run tests under `tests/` for logic changes, run targeted test files (`.venv/bin/pytest tests/test_x.py`) during development, and bypass test runs entirely for documentation, comments, CSS, or markup-only templates. |
| Allowing technical debt or architectural drift | Follow standard design patterns and refactor code to maintain clarity and structure. |
| Ignoring large context size until limits are breached | Monitor token counts and alert the user when the model context size starts getting too large. |
| Writing washed-out, low-contrast, or hard-to-read buttons and UI text | Ensure proper high-contrast ratios and strictly adhere to design-system variables and visual rules. |
| Leaving new features or config options undocumented | Always update `ARCHITECTURE.md`, `DESIGN.md`, `MODULE_GUIDE.md` (in `mirrordash-sdk` repo), `AGENTS.md`, and `CHANGELOG.md` when introducing new features. |
| Ignoring user's preferred global settings (e.g., time_format, language, units) | Always check and respect configuration settings from the "globals" block instead of assuming browser locale or hardcoding defaults. |
| Reinventing complex standard logic or unnecessarily bloating package dependencies | Look for well-known, popular, and robust libraries for complex tasks, but avoid importing external packages for simple utility code that can be natively written. |
| Creating a new module manually from scratch | Always use the standalone `mirrordash-cli create-module` scaffolder command to generate a fully compliant module structure. |
| Hardcoding fixed widths for columns or lists in module templates | Design layouts to be fully responsive. Other languages (like Swedish or German) can have words that are much longer than English. Use flexbox/grid and truncation (`text-overflow: ellipsis`) instead. |
| Leaving root partition boundaries unmonitored during package installation | Expose and show root partition disk usage metrics, warning users if free space drops below 500MB on the 6GB partition. |
| Writing instructions with dozens of manual copy-paste commands | Provide automated scripts (like `scripts/setup_appliance.sh`) and chain manual command segments using `&&`. |




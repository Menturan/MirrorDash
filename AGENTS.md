# AGENTS.md — MirrorDash AI Agent Guide

This file provides AI coding agents with everything they need to work effectively on the MirrorDash codebase. Read this before making any changes.

> [!IMPORTANT]
> **Remember to always follow the design rules.** Adherence to the visual design rules, system constraints, and aesthetic/contrast guidelines is critical. See `DESIGN.md` and `mirrordash_core/static/style.css` for details.

## Table of Contents

- [Project Overview](#project-overview)
- [Repository Layout](#repository-layout)
- [Tech Stack](#tech-stack)
- [Running Locally](#running-locally)
- [Critical Coding Rules](#critical-coding-rules)
- [Architecture Patterns](#architecture-patterns)
- [Adding a New Module](#adding-a-new-module)
- [Modifying Core Behaviour](#modifying-core-behaviour)
- [Design System Explorer](#design-system-explorer)
- [Documentation Files to Keep Updated](#documentation-files-to-keep-updated)

---

## Project Overview

MirrorDash is a modular, ambient information display system designed for Raspberry Pi running in Kiosk mode. The backend is a FastAPI/Python server. The frontend is pure HTML, CSS, and JavaScript served to Cog (WebKit) in full-screen kiosk mode.

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
│   ├── api/
│   │   ├── admin.py        # Admin router aggregator & public API re-exporter
│   │   ├── admin_shared.py # Shared Jinja2Templates and auth helpers
│   │   ├── admin_auth.py   # Auth routes (setup, status)
│   │   ├── admin_config.py # Config REST and HTMX panel endpoints
│   │   ├── admin_system.py # System REST API endpoints & A/B updates
│   │   ├── admin_system_panels.py # System HTMX panel rendering endpoints
│   │   ├── admin_modules.py# Module scanning & REST API endpoints
│   │   ├── admin_modules_panels.py # Modules HTMX panel rendering endpoints
│   │   ├── admin_backup.py # HTMX-specific backup panel routes
│   │   ├── admin_logs.py   # Logs REST and HTMX log viewer routes
│   │   └── backup.py       # Backup/restore REST API
│   ├── static/             # Static files served at /static/*
│   │   ├── index.html      # Mirror display (the kiosk page)
│   │   ├── design.html     # Design System Explorer (served at /design)
│   │   ├── admin_js/       # Admin dashboard split JavaScript files
│   │   ├── js/             # Kiosk frontend ES modules
│   │   │   └── kiosk/      # WebSocket, module rendering, Web Components
│   │   ├── style.css       # Global Ethereal Design System CSS
│   │   └── admin.css       # Styles specific to the admin dashboard
│   └── templates/          # Jinja2 templates (rendered server-side)
│       ├── admin.html      # Main admin dashboard layout wrapper
│       └── admin_*.html    # Modular sub-panels for admin dashboard tabs
├── modules/                # Each module is its own pip-installable Python package
│   └── mirrordash-clock/         # Ticking clock/date widget with Babel localization
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
| Frontend | Vanilla HTML5, CSS Grid, Vanilla JS (frameworks require justification) |
| Templating | Jinja2 (server-side, rendered per module) |
| Real-time | WebSockets (one persistent connection per browser client) |
| Module System | Python `importlib.metadata` entry points (`mirrordash.modules` group) |
| Deployment | Raspberry Pi OS Trixie (Debian 13), OverlayFS read-only rootfs, Wayland (labwc), Cog kiosk |

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

### General

0. **Always consider the development environment.** Local dev machines run on amd64 with a regular terminal — not on the production Pi. Code must work in both contexts: subprocess calls may prompt for `sudo` passwords (no TTY available in background processes), CDNs are blocked or cached differently offline, services like NetworkManager or systemd may not be running locally. Avoid hardcoding production-only paths or assumptions.

0a. **Be critical and proactive.** Do not blindly execute tasks as described if there is a cleaner, simpler, or more architecturally sound approach. If you think there is a better way of doing things (e.g., serving static files instead of building complex DOM-polling overlays), ALWAYS suggest it and guide the user towards the better solution.

0b. **Strict DevOps Build Philosophy.** When writing or modifying build and appliance scripts (e.g., `build_image.sh`, `setup_appliance.sh`), you MUST use stable, failsafe, declarative DevOps standards. 
- **Never** use "hobbyist" hacks: No shell polling loops (`sleep` or `while` checks), no string-scraping HTML for URLs (`grep | cut`), and no arbitrary terminal autologins or `.bash_profile` injections.
- **Always** use deterministic solutions: Native D-Bus event waiting (e.g., `nm-online`, `nmcli device wait`), native systemd services (`graphical.target`, `PAMName=login`), and canonical API flags (e.g., `curl -w '%{url_effective}'`). 
Build scripts must be deterministic, free of race-conditions, and mathematically proven to execute correctly without relying on timing or visual DOM layouts.

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

8. **Latest OS Assumption.** Always assume the Golden Image is created from the absolute latest version of Raspberry Pi OS Lite (64-bit) (based on Debian Trixie/Debian 13 or newer). Configure all scripting, system packages, cmdline.txt adjustments, and network commands accordingly.

### Frontend

9. **Conservative framework adoption.** The mirror frontend uses vanilla HTML, CSS, and JavaScript by default. Introducing frameworks (React, Vue, Alpine, HTMX, etc.) should be avoided unless there is a compelling justification that vanilla JS cannot solve. If a framework is deemed necessary, consult with the maintainers first and document the rationale in `ARCHITECTURE.md`.

10. **No `document.write()` or `eval()`.**

11. **All JSON received over WebSocket must be parsed with try/catch.** See the existing `socket.onmessage` handler in `index.html`.

12. **WebSocket reconnection uses exponential backoff.** Do not replace with simple `setInterval`. The existing pattern in `index.html` is correct.

13. **Vector Iconography (Lucide).** Use Lucide outline icons via the `data-lucide` markup attribute (e.g., `<i data-lucide="sun"></i>`) instead of colored or multi-color emojis.

14. **Icon Re-Rendering.** When injecting HTML dynamically (e.g., updating modules via WebSockets or JS), you must trigger `lucide.createIcons()` after the DOM updates so the vector glyphs are rendered on the client side.

15. **Responsive Fluidity & Translation Safety.** All module templates/layouts must be designed to be as responsive and flexible as possible. Avoid hardcoded fixed-width columns (e.g. in lists or forecast rows) because localized strings in other languages (such as Swedish or German) can be significantly longer than their English counterparts. Use flexbox or CSS Grid with flexible sizing (`flex: 1`, `min-width`, `max-content`) and text truncation utilities (`text-overflow: ellipsis`) to handle arbitrary string lengths gracefully.

### Git
 
16. **Always commit with `--no-gpg-sign`** — GPG signing is disabled on this machine.
    ```bash
    git commit --no-gpg-sign -m "feat: ..."
    ```
 
17. **Follow conventional commits with specific scopes for changelog generation:** `feat(<scope>):`, `fix(<scope>):`, `refactor(<scope>):`, `style(<scope>):`, `docs(<scope>):`, `chore(<scope>):`. Always use one of the following scopes:
    - **Core App**: `api`, `core`, `ui`, `design`, `kiosk`, `frontend`, `auth`, `config` (e.g., `feat(api): add updates panel`).
    - **System OS (Appliance)**: `scripts`, `os`, `golden-image`, `appliance` (e.g., `fix(scripts): resolve logind-seatd race`).
    - Defaults/Fallbacks: Commits without scopes default to `Core App` (unless keywords like "scripts" or "appliance" are present in the description).

17a. **Do not commit downloaded developer tools.** When downloading binaries or helper tools during development (e.g., `shellcheck`), always download them to a temporary directory outside the git repository (e.g., `/tmp` or the agent's scratch space) to prevent them from being accidentally tracked or committed.

### Testing

18. **Smart & Targeted Test Execution.**
    - **Required for Logic Changes**: Unit/integration tests in the `tests/` directory are mandatory for any backend/frontend code modifications, logic updates, or bug fixes.
    - **Bypass for Non-Logic Changes**: You MUST NOT run backend tests if modifications are strictly limited to documentation (e.g., `.md` files), inline code comments, pure styling (CSS), static assets, or templates with no execution logic.
    - **Targeted Test Runs**: During development, run only the relevant test file or specific test cases (e.g., `.venv/bin/pytest tests/test_event_bus.py` or `.venv/bin/pytest -k <test_name>`) instead of running the entire suite. Only run the full suite (`.venv/bin/pytest`) as a final validation step before completion.


### General

19. **Architectural Patterns & Refactoring.** Always follow well-known code architectural patterns and perform necessary refactoring to keep the codebase clean, modular, and well-formatted.

20. **Context Size Warnings.** Monitor and inform the user if the model context starts getting excessively large, so they are aware of potential token exhaustion or truncation limits.

21. **Remember to always follow the design rules.** Adherence to the visual design rules, system constraints, and accessibility requirements (such as high-contrast button readability) defined in `DESIGN.md`, `AGENTS.md`, and CSS files is mandatory when styling or modifying layouts/components.

22. **Document all new features immediately.** Any new features, configurations, or design system classes must be documented across all relevant documentation files (`ARCHITECTURE.md`, `DESIGN.md`, `MODULE_GUIDE.md`, `AGENTS.md`, and `CHANGELOG.md`) immediately to prevent documentation drift.

23. **Follow global configurations.** Always respect, load, and inherit settings defined under "global configuration" (such as language, timezone, time format, temperature/distance units, and coordinate location) when formatting, styling, or building logic for modules or core dashboard features.

24. **Minimize dependency inflation and prefer robust standard libraries.** Prefer utilizing well-known, popular, and robust packages (e.g. `Babel` for localization, `pytest` for tests) to avoid reinventing the wheel for complex domain tasks. However, remain conservative—do not add dependencies for simple utilities that can be written in a few lines of clean, native code. Make well-considered dependency decisions.

25. **Always use the mirrordash-cli scaffolder when creating new modules.** Never manually scaffold module directories from scratch. Always run the `mirrordash-cli create-module mirrordash-<name> --description "<desc>"` command to ensure a fully compatible packaging, template directory, and entry point layout structure is generated automatically.

26. **Professional Grade IoT & Consumer Simplicity.** The system must operate with professional-grade IoT reliability, designed to run continuously for years without manual intervention, memory leaks, or filesystem corruption. Every user-facing interface, including the captive portal network setup wizard, must be designed with ultimate simplicity in mind, ensuring that non-technical users (such as your parents) can use and configure the device safely, intuitively, and without command-line access.

27. **Disk Space Boundary Monitoring.** Active virtual environments (`venv_a`, `venv_b`) and all community modules reside on the persistent `/storage` partition. Any operations modifying packages (like installing or upgrading modules) must check free space (via the `/admin/disk-usage` endpoint, which monitors `/storage`). Always display a warning visual when remaining persistent storage space drops below 500MB to avoid module installation failures.

28. **Deploy Automations & Scripting.** When writing golden image or system administration guides, provide a single setup script (e.g., `scripts/setup_appliance.sh`) and chain manual command segments using `&&` to minimize copy-paste errors and user friction.

29. **Golden Image is a Production IoT Environment.** `GOLDEN_IMAGE.md` and all associated setup scripts must be authored with the same rigour as a professional, high-grade IoT production system. No source code on the device, no editable installs, no debug flags, no leftover dev credentials. Assume zero human intervention after deployment. Treat every instruction as executed by a non-expert.

30. **Documentation ↔ Script Synchronization.** Documentation files and their corresponding automation scripts are **two representations of the same truth**. When modifying one, you must immediately update the other. This applies universally: `DESIGN.md` ↔ `style.css`, `USER_GUIDE.md` ↔ admin UI code, `ARCHITECTURE.md` ↔ actual code patterns, module `README.md` ↔ module source.

31. **Dual-Artifact Release Model.** This project produces two independent artifacts from the same repository:
   - **Core App** (`mirrordash` Python package) → published to PyPI automatically via GitHub Actions OIDC Trusted Publishing when a `vX.Y.Z` tag is pushed. Version is defined in `pyproject.toml`.
   - **System OS Image** (`mirrordash-os-vX.Y.Z.img.gz`) → built manually via `scripts/build_image.sh` and published as a GitHub Release asset. Tagged separately as `vX.Y.Z-osN`. The version must strictly target the active Core App version (`X.Y.Z` from `pyproject.toml`) and only increment the build suffix (`-osN`) to maintain dual-artifact alignment.
   
   These artifacts are released on different schedules. The Core App can ship without the System OS image, and vice versa. When updating `CHANGELOG.md`, entries under a versioned `[X.Y.Z]` section must only contain changes that ship in that specific artifact. System OS appliance changes must remain under `[Unreleased]` until the golden image is tested and released. Never mix System OS entries into a Core App version block.

32. **Maintain Table of Contents (TOC) in Markdown Files.** Always add and update a Table of Contents (TOC) at the top of `.md` files if there are more than 3 level-2 (`##`) headings (or major sections).

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

The visual system is documented in `DESIGN.md` and implemented in `mirrordash_core/static/style.css`. These two files must always be kept in sync.

- **Never put module-specific CSS in `mirrordash_core/static/style.css`.** Module styles belong inside a `<style>` block in the module's own Jinja2 template (e.g., `mirrordash_core/templates/widget.html`).
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
# 3. Install the module in editable mode
uv pip install -e ./modules/mirrordash-my-widget

# 4. Enable in config.json — add entry under "modules" with "position", "enabled", "interval"

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
- **`app.py`:** Public routes go directly on `app`. Admin routes go in `mirrordash_core/api/` sub-routers (`admin_auth.py`, `admin_system.py`, etc.) and `mirrordash_core/api/backup.py` with the `require_api_key` dependency.

---

## Design System Explorer

The file `mirrordash_core/static/design.html` (served at `/design`) is a live component kitchen-sink for module developers. When adding new CSS components to `style.css`:

1. Add the CSS classes to `mirrordash_core/static/style.css`.
2. Document them in `DESIGN.md` under the relevant section.
3. Add a live rendered example with a copyable code snippet in `mirrordash_core/static/design.html`.

---

## Documentation Files to Keep Updated

| File | Update when... |
|------|---------------|
| `README.md` | Any high-level project goals, setup instructions, or repository layout changes |
| `ARCHITECTURE.md` | A new architectural decision is made or an existing one changes |
| `DESIGN.md` | Any CSS component is added, removed, or renamed in `style.css` |
| `MODULE_GUIDE.md` (in `mirrordash-sdk` repo) | The **module developer API** changes — new injected helpers, lifecycle hooks, config schema format, or storage conventions. **Not** for documenting specific modules. |
| `MODULE_AGENTS.md` (in `mirrordash-sdk` repo) | Any new module-specific coding rules, constraints, or scaffolding conventions are established |
| `modules/<name>/README.md` | A specific module's config keys, providers, API key instructions, or features change |
| `USER_GUIDE.md` | Any end-user features, settings tabs, configuration keys, or layout rules change |
| `AGENTS.md` | New coding rules, patterns, or constraints are established |
| `CHANGELOG.md` | Any notable feature, bug fix, or codebase change is committed |
| `RELEASING.md` | The release workflow, OIDC configurations, or pre-release checklists change |

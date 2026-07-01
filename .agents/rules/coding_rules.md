# Coding Rules

## Table of Contents

- [Python](#python)
- [Frontend](#frontend)
- [Git](#git)

## Python

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

## Frontend

7. **Conservative framework adoption.** The mirror frontend uses vanilla HTML, CSS, and JavaScript by default. Introducing frameworks (React, Vue, Alpine, HTMX, etc.) should be avoided unless there is a compelling justification that vanilla JS cannot solve. If a framework is deemed necessary, consult with the maintainers first and document the rationale in `ARCHITECTURE.md`.

8. **No browser alert popups.** Never use native browser dialogs like `alert()`, `confirm()`, or `prompt()` in the admin dashboard user interface. Use custom, sleek inline styling or custom overlays consistent with the Global Ethereal Design System instead.

9. **All JSON received over WebSocket must be parsed with try/catch.** See the existing `socket.onmessage` handler in `index.html`.

10. **Vector Iconography (Lucide).** Use Lucide outline icons via the `data-lucide` markup attribute (e.g., `<i data-lucide="sun"></i>`) instead of colored or multi-color emojis.

11. **Icon Re-Rendering.** When injecting HTML dynamically (e.g., updating modules via WebSockets or JS), you must trigger `lucide.createIcons()` after the DOM updates so the vector glyphs are rendered on the client side.

12. **Responsive Fluidity & Translation Safety.** All module templates/layouts must be designed to be as responsive and flexible as possible. Avoid hardcoded fixed-width columns (e.g. in lists or forecast rows) because localized strings in other languages (such as Swedish or German) can be significantly longer than their English counterparts. Use flexbox or CSS Grid with flexible sizing (`flex: 1`, `min-width`, `max-content`) and text truncation utilities (`text-overflow: ellipsis`) to handle arbitrary string lengths gracefully.

## Git

13. **Follow conventional commits with specific scopes for changelog generation:** `feat(<scope>):`, `fix(<scope>):`, `refactor(<scope>):`, `style(<scope>):`, `docs(<scope>):`, `chore(<scope>):`. Always use one of the following scopes:
    - **Core App**: `api`, `core`, `ui`, `design`, `kiosk`, `frontend`, `auth`, `config` (e.g., `feat(api): add updates panel`).
    - **System OS (Appliance)**: `scripts`, `os`, `golden-image`, `appliance` (e.g., `fix(scripts): resolve logind-seatd race`).
    - Defaults/Fallbacks: Commits without scopes default to `Core App` (unless keywords like "scripts" or "appliance" are present in the description).
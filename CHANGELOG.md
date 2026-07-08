# Changelog

All notable changes to the MirrorDash project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
## [Unreleased]

### Home Assistant Module
- Support dynamic entity grouping, allowing users to bundle sensors into custom sections and mix-and-match layout styles (`"detailed"` and `"compact"`) in a single widget.
- Implement case-insensitive nested telemetry extraction for `battery`, `humidity`, `linkquality`, and `voltage`.
- Fix Lucide icons visibility in compact layout and battery rendering in detailed layout.

### Calendar Module
- Consolidate multi-day all-day events into a single entry on their first active day in the lookahead window (formatted as "Now - [End Date]" or "[Start Date] - [End Date]"), resolving dashboard clutter from daily repetitions.
- Add Swedish and English translations for the "Now" label.

### Clock Module
- Move clock ticking loop, date/time formatting, and timezone translation from backend Python (FastAPI/Babel/ZoneInfo) to frontend browser JS using native standard `Intl.DateTimeFormat` APIs, eliminating 1-second WebSocket push traffic and resolving Babel locale warning log spam.
- Move clock module out of MirrorDash core into the standalone `mirrordash-modules` folder.

### Core App
- Add asynchronous, click-through system and module update alerts to the admin dashboard overview tab.
- Add system memory (RAM), NTP sync, connection diagnostics, uptime, and under-voltage power telemetry to the admin dashboard overview.
- Fix admin dashboard initial load mismatch by adding a Dashboard tab to the sidebar navigation menu.
- Re-architect the active modules interface as an interactive community-style App Store.
- Implement slide-over modal configuration drawers with localized CSS transit rules.
- Add active and configured tag indicators and client-side category pill-based filtering.
- Rebuild the administration control panel using a unified, responsive glassmorphic layout.
- Add an interactive 3x3 Screen Layout Matrix and System Analytics telemetry metrics dashboard.
- Update layout elements, buttons, and navigation menus to support fluid mobile layout responsiveness.
- Support running multiple instances of the same module on a single mirror screen (e.g. multiple clocks/calendars).
- Implement config file format migration to add a `"module"` type specifier for each module instance configuration block.
- Isolate and separate persistent data and cache directory storage paths per module instance.
- Enforce GitHub Releases requirement for community module installations, scans, and updates, blocking branch/commit installations.
- Load community modules list asynchronously via HTMX to prevent UI freezing during tab switching.
- Add `show_header` configuration option to all modules (Calendar, Home Assistant, News) to control title visibility.
- Move clock and calendar module tests out of core repository into their respective module repositories.
- Add `z_index` and `opacity` per-module config properties
- Split AGENTS.md into modular `.agents/rules/` files for token efficiency
- Match correct final image artifact in github workflow release step (aa9604d)
- Append Wayland, NetworkManager, and kiosk systemd architectural rules (2b3cb06)
- Revert "docs(agents): append Wayland, NetworkManager, and kiosk systemd architectural rules"

This reverts commit 2b3cb0639acdabdcd7d0026a5e24e33467cd653c. (88b2009)
- Add Strict DevOps Build Philosophy rule 0b (572d6ec)
- Harden A/B venv update and backup restore by explicitly targeting python binary (132f398)
- Update CHANGELOG.md for changes since 0.3.0 (399dc9e)
- Condense CHANGELOG.md Unreleased System OS section (b84776b)
- Bump version to 0.3.1 (ba5d2f7)
- Implement git-cliff configuration and update commit guidelines (263f337)
- Regenerate CHANGELOG.md using git-cliff (8641cdd)
- Organize CHANGELOG for 0.3.1-os1 OS image release (4225ba7)
- Update CHANGELOG.md for plymouth and seatd fixes (7293e8a)
- Update CHANGELOG.md for wlr-randr retry and seatd dependency commit (2397b45)
- Update CHANGELOG.md for seatd container build fixes (8ecba90)
- Update CHANGELOG.md for redundant systemctl purge commit (1849b85)
- Update CHANGELOG.md (f9fbf57)
- Update CHANGELOG.md (5ae8383)
- Update CHANGELOG.md (24e6bbd)
- Update CHANGELOG.md (07e29ba)
- Update CHANGELOG.md (6870064)
- Update CHANGELOG.md (6a0ab4e)
- Update CHANGELOG.md (b4fe668)
- Untrack modules/mirrordash-clock and ignore module subdirectories (e365fe4)
- Resolve Wayland startup race conditions (extend wlr-randr retries, add seatd Wants dependency) (860c1dd)
- Update CHANGELOG.md (59143b4)
- Resolve display power startup race condition (19056c8)
- Append display power startup race fix (288fc60)
- Prevent cog-kiosk fatal startup rate-limit crash (51ecc85)
- Append cog-kiosk rate limit fix (f010862)
- Add GitHub scanning for community module discovery (90f3201)
- Add cron job to purge browser cache and prevent OOM crash (ee5b1ed)
- Append cache purge memory leak fix (7411a0a)
- Use install_name for module installation and update tests (0d3c947)
- Bump version to 0.3.2 (07b9d92)
- Organize CHANGELOG for 0.3.2-os1 OS image release (13ada17)
- Remove shadowed local os import that crashed system password loader (fde5ca8)
- Increase hotspot detection timeout to prevent race condition showing black screen (daef02e)
- Remove broken headless nmcli --ask prompt that caused Wi-Fi captive portal setup to silently fail (3ad1c0b)
- Auto-resolve local modules during installation to support bundled modules without PyPI (2c6c38a)
- Add manual refresh button and timestamp to community modules panel to allow instant fetching of newly published GitHub repos (44cceb4)
- Resolve backend IndentationError crashing config drawer, and add HTMX loading indicators to module install buttons (0d56645)
- Target persistent storage and update module registry URLs (49811de)
- Correct manual module scan endpoint route and disk usage label (3c32a22)
- Add global offline indicator with custom slashed globe svg (db65b62)
- Secure wifi connection password transmission (9df14fa)
- Revert "fix(core): secure wifi connection password transmission"

This reverts commit 9df14fa803ea5ade7af91f880fc3cf27fb6c0cfd. (1f58eb3)
- Align wifi connection test with headless nmcli command line argument usage (1afad5e)
- Bump version to 0.3.3 (b80d0fe)
- Organize CHANGELOG for 0.3.3-os1 OS image release (abe3f41)
- Add glassmorphic restart overlay and fix config drawer toggle (de77bf6)
- Add Playwright integration tests and unify dev dependencies (0141cd0)
- Stabilize and complete playwright visual test suite (ce6257b)
- Implement professional wildcard HTTP redirects for captive portal (3164cf9)
- Update CHANGELOG for visual tests and captive portal changes (7cb5f1d)
- Automatically generate CHANGELOG using git-cliff (818e524)
- Final automated regeneration of CHANGELOG.md (623246a)
- Bump version to 0.3.4 (de826e3)
- Globally hide mouse cursor in CSS and symlink common compositor cursors (733f299)
### System OS (Appliance)
- Update build_image.sh (fd191f9)
- Update build_image.sh (669758d)
- Update setup_appliance.sh (c85b9f6)
- Update build_image.sh (188e583)
- Update setup_appliance.sh (1d43f64)
- Update build_image.sh (edecc07)
- Update setup_appliance.sh (9d8d94e)
- Update setup_appliance.sh (f6ed5a5)
- Implement DevOps standard setup for Golden Image (e9e8cf5)
- Patch firstboot and pishrink to prevent partition resizing conflicts (8185071)
- Implement first-boot storage hydration using base_venv clone (804938c)
- Resolve initramfs build, hydration race condition, and data loss bugs (afe252d)
- Replace brittle firstboot patch with robust function override to prevent bash syntax errors (08d758e)
- Implement atomic hydration and robust cmdline parameter patching (7d405a0)
- Align build pipeline with strict devops standards and remove fragile quirks (b1fa08d)
- Add missing nginx dependency to apt-get installation list (61f6f7f)
- Remove systemctl daemon-reexec during build since systemd is not PID 1 in the container (900871f)
- Force MODULES=most for initramfs to prevent chroot mkinitramfs hardware profiling panic (d0ec1b9)
- Ignore apt-get purge errors when packages are not installed (2624462)
- Add error suppression to log truncation step for robustness (81a5268)
- Remove invalid --skip-login flag from agetty to restore auto-login functionality (29c86d2)
- Purge legacy wayland flags and align display configs with labwc kiosk standards (aaee9b6)
- Resolve black screen labwc permissions and unblock wifi rfkill on first boot (86c6e10)
- Resolve wlroots crash on headless input and add missing captive portal dependencies (4d2ab59)
- Resolve logind-seatd race condition causing black screen and add networkmanager delays for captive portal (4882a40)
- Migrate to native systemd graphical service and nm-online for robust appliance boot (b036a37)
- Resolve remaining url scraping, wifi polling, and initramfs nspawn crashes (c3cd942)
- Convert captive portal to background task and bake rfkill unblock into image (d150289)
- Migrate frontend to systemd service and eliminate plymouth race condition (10ad825)
- Resolve systemd privilege escalation and guarantee physical radio scan (2fb578b)
- Add fstab safety trap, sync browser restarts, and robustify firstboot patch (e6fc8db)
- Add atomic no-target-directory flag to fallback rollback moves (9a714a9)
- Add atomic download continuation to build image (935e656)
- Remove redundant sleep from wifi hardware scan (2a4288b)
- Critical first-boot failure — tmpfiles venv directory blocks hydration symlink (2155d18)
- Eliminate hydration/tmpfiles race for parent directory (00c5ad6)
- Align download path with CI cache — cache was never hitting (8518045)
- Harden hydration atomicity and make firstboot resize patch idempotent (7b86cb1)
- Exclude SIGINT from triggering rollback in launch.sh (0aa29a0)
- Sync GOLDEN_IMAGE.md with hardened build scripts (95a5c18)
- Resolve release_changelog.py regex lookahead and section insertion bugs (ac48eed)
- Fix build_image grouping and update cliff.toml mapping rules (8b313e3)
- Resolve plymouth-kiosk deadlock and seatd permissions (7ba3312)
- Add seatd dependency to labwc-kiosk and wlr-randr retries in backend (2219a42)
- Resolve seatd systemctl container build failure and group safety (91f1c02)
- Purge redundant package systemctl enable calls to prevent container build failures (10e88a7)
- Guarantee offline service enablement on Trixie chroot (5118736)
- Configure seatd socket to video group to resolve first-boot reset (afd8acc)
- Prevent emergency mode lockouts by adding fstab nofail and stripping resize parameter (a843ae1)
- Implement MBR-compatible dynamic partitioning service on first boot (dfaf32b)
- Harden repart and hydration services against udev and mount timing races (673c4ee)
- Mask getty@tty1.service to prevent TTY1 preemption and SIGHUP crashes (29279c6)
- Mask autologin@tty1.service to prevent terminal preemption under auto-login configs (3702565)
- Add git to appliance dependencies for uv module resolution (6c223d3)
- Resolve OverlayFS masking of partition resizer on first boot (e2aa080)
- Correct truncation of PARTUUID during storage partition initialization (0812ce7)
- Correct parted argument syntax to use -a flag instead of invalid align command (dcc0fc6)
- Remove strict systemd RequiresMountsFor dependency that blocks Uvicorn when storage partition is dynamically created (f5f5d7a)
- Replace NM symlink with bind-mount to bypass strict security restrictions on Wi-Fi credentials (863cf4a)
- Resolve seatd path and transparent cursor for labwc (cb6689b)
- Change display server systemd target to multi-user (ae56b54)
- Export XCURSOR_THEME=empty to browser kiosk service (3fb77da)
- Prevent recursive chown from corrupting Wi-Fi credentials (1ebb19b)
- Integrate git-cliff into release_core and release_os scripts (2e42ba8)
- Add command detection and fallback to npx for git-cliff (d3c3842)
## [0.3.0-os2] - 2026-06-23

### Core App
- Mock SSH status check in display power test to avoid password prompts (eefb314)
- Emphasize /design live explorer in main README (7584e14)
- Align design explorer and tokens with Ethereal specs (c1796e1)
- Add zero-css layout utilities and documentation (1b3a382)
- Update build-os-image.yml (b7b795a)
## [0.3.0-os1] - 2026-06-22

### Core App
- Clarify interactive release wizards and version tracking requirements (9f8282d)
- Organize CHANGELOG for 0.3.0-os1 OS image release (824c1d4)
## [0.3.0] - 2026-06-22

### Core App
- Optimize and fix github actions OS image builds (1703348)
- Correct setup prompt element selectors for WiFi hotspot mode (fb1eb29)
- Change framework rule from forbidden to conservative adoption (c113396)
- Update tech stack to reflect conservative framework policy (c3e4e7b)
- Note relaxed frontend framework policy in changelog (848475b)
- Split index.html into modular ES files with Web Component (e4830a9)
- Update repository layout to reflect js/kiosk directory (f6a757f)
- Correct CSS structure in setup-prompt Web Component (0ee41ac)
- Add integration tests for kiosk JS modular split (40b7546)
- Resolve setup-prompt layout, setup offline Inter font, and update kiosk docs to Cog (860475f)
- Wrap design tokens in style tag and resolve WebKit setup-prompt layout bugs (23178bb)
- Clearer instructions for setting up wifi (5af9951)
- Simplify setup prompts into static HTML files served by backend (0b2a611)
- Add rule 0a to AGENTS.md instructing agents to be critical and proactive (6201fa2)
- Add form generator helper and local htmx library (2ff2262)
- Implement backend HTMX panel endpoints (072a94b)
- Update admin.html for HTMX tabs and auth (837e066)
- Migrate all dashboard panels to HTMX (6a30906)
- Preserve active page tab across reloads using url hash (97a2f61)
- Only return clock module as community/discoverable module (7ab7b50)
- Dynamically scan PyPI simple index in background for mirrordash-* community modules (9076a19)
- Split monolithic admin.py into modular domain sub-routers (f4b3337)
- Split modules and system API routers into REST and panel endpoints (3d7fb87)
- Update CHANGELOG.md with changes since last release (6573c5f)
- Bump version to 0.3.0 (055955e)
- Changelog fix (b897966)
## [0.2.4-os1] - 2026-06-16

### Core App
- Update GOLDEN_IMAGE.md to reflect native ARM build (no QEMU) (6aad790)
- Restructure GOLDEN_IMAGE.md with clear Track A (automated) vs Track B (manual reference) split (6174b95)
- Restructure RELEASING.md for two distinct release tracks with CHANGELOG guidance (4ee0388)
- Restructure CHANGELOG.md - Unreleased at bottom, remove duplicate Core App subsection (e325fa8)
- Mock subprocess in test_wifi_scan_no_cache_returns_empty (206ab72)
- Organize CHANGELOG for 0.2.4-os1 OS image release (76baba4)
- Handle [Unreleased] at EOF in release_changelog.py (7e41da1)
- Restructure CHANGELOG and release_changelog.py for dual-artifact model (d36be85)
- Vendor Font Awesome locally to eliminate CDN SRI mismatch (769ec52)
- Add timeout to is_wifi_hotspot_active and get_ssh_status to prevent hanging (43a34ff)
- Add rule 0 to consider dev environment in all implementations (e5447a3)
- Implement Shadow DOM isolation for all modules (551ca4b)
- Add GitLab CI for OS image builds and fix WiFi hotspot detection (befa33a)
- Gitlab-build-fix (0dbfd00)
- Gitlab-build-fix 2 (25d4f13)
- Github-os-build-fix (0c574f8)
- Github-os-build-fix 2 (6be006f)
## [0.2.4] - 2026-06-15

### Core App
- Implement and harden OS image builder and extraction tools (6632462)
- Update core application settings, styling, and tests (64a8758)
- Update documentation, release guidelines, and changelogs (b12a9ad)
- Bump version to 0.2.3 (401542c)
- Generate sha256 checksum file for final compressed image (f0c8332)
- Retain alsa-utils in package installations (1c7249b)
- Optimize QEMU build speed and fix pigz initramfs kernel warning (1b4c411)
- Simplify initramfs pigz acceleration using native fallback (0266b3d)
- Document release distinction and deployment procedures in RELEASING.md (c20ac54)
- Add rule 31 to AGENTS.md and generate Table of Contents headers (41c6b40)
- Clarify two deployment types in RELEASING.md and fix references (c128acc)
- Bump CI Python runtime to 3.14 (b33d8e6)
- Update CHANGELOG for captive portal and WiFi persistence fixes (487ec7a)
- Note WiFi credential exclusion on backup page (421751b)
- Make loading.html offline-capable and boot-status-aware (82f6d8f)
- Bump version to 0.2.4 and update CHANGELOG (47fcb5b)
- Move System OS changelog entries back to Unreleased for 0.2.4 (43b02b6)
- Add Rule 32 (dual-artifact release model) and renumber TOC to 33 (49d2b8c)
- Trim AGENTS.md — remove redundant Common Pitfalls table, update TOC (68fd1f2)
- Trim AGENTS.md — remove redundant Common Pitfalls table, reduce from 355 to 314 lines (48320da)
## [0.2.2] - 2026-06-12

### Core App
- V0.2.2 - fix setup script, add python-multipart, and restructure static/templates packaging (275c79a)
## [0.2.1] - 2026-06-12

### Core App
- Add job environment to publish.yml for PyPI OIDC (5c6f6c8)
- Document recent bugfixes under v0.2.0 in CHANGELOG.md (5d3a26d)
- Release version 0.2.1 changelog (aa47e11)
- Add RELEASING.md detailing the deployment and release process (edbbf10)
- Clarify release steps and add OIDC linkage guide in RELEASING.md (b8f6e13)
- Remove one-time linkage instructions from RELEASING.md (f9fc52f)
## [0.2.0] - 2026-06-12

### Core App
- Initial commit (70f14d5)
- Show first-run setup prompt on mirror display when no admin password is set (c7ba6a7)
- Drop :8000 from captive portal fallback URLs in docs and tests (340fabf)
- Bump core application version to 0.2.0 (d834b91)
- Add GitHub Actions workflow for PyPI publishing and README badges (13f062e)

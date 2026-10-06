# Changelog

All notable changes to the MirrorDash project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
## [0.4.0] - 2026-10-06

### Core App
- Simplify and clean up over-engineered components (25ffa35)
- Simplify and clean up over-engineered components (d740ef4)
- Filter, enforce and check updates for GitHub modules using releases (2e156ee)
- Remove lucide.min.js.map reference to prevent 404 warning log spam (c877aa4)
- Load community modules asynchronously to prevent UI freeze (0c849c2)
- Add Asynchronous Non-Blocking Panel Rendering rule (9ac83a2)
- Update news and home assistant widget mock expectations for show_header option (be5c29e)
- Move clock module out of MirrorDash core into mirrordash-modules folder and remove deprecated test files (0c0f9a6)
- Update CHANGELOG.md for unreleased module relocation, releases check, and show_header options (431d8f0)
- Move news, weather, and home assistant tests to their respective module repositories (b5712db)
- Relocate weather architectural decisions to the weather module repository and renumber core architectural decisions (bd0d7e0)
- Relocate calendar troubleshooting section from user guide to module repository (b049b2c)
- Clean up obsolete module-specific layout classes from core style.css (1efb555)
- Pre-install and enable clock module by default on image flashing (82121a2)
- Install mirrordash-clock via Git repository URL since it is not published to PyPI (cf6c33c)
- Append design rules reminder to AGENTS.md (1110f31)
- Document Home Assistant grouping and layout updates in CHANGELOG (643db58)
- Support hot-reloading with MIRRORDASH_DEV environment variable (eee30fb)
- Render array item schema properties with enums as select dropdowns (b46f356)
- Document enum sub-field selects inside Dynamic Object List in design.html (db6b266)
- Enrich Dynamic Object List details and provide full entities array schema example (015f80d)
- Make Dynamic Object List preview interactive in /design playground (74938ba)
- Support multiple instances of a module on a single screen (d8522b6)
- Implement custom module icons, multi-instance settings, and design token styling (be2809b)
## [0.3.4-os1] - 2026-07-01

### Core App
- Globally hide mouse cursor in CSS and symlink common compositor cursors (733f299)
- Update CHANGELOG for cursor fix (9a31184)
- Prompt for new password when admin auth is missing or corrupt (17c84d5)
- Separate first-boot setup from change-password — setup is strictly unauthenticated first-boot only (93a0af7)
- Add screen PIN-based recovery for corrupt admin configuration (644ecf0)
- Resolve startup crash and empty module config drawer loading (1d023ff)
- Resolve infinite authentication failure reload loops and clear legacy tokens (bf45af1)
- Completely remove legacy mymm_api_key references (bbf6d96)
- Remove all legacy mymm.modules entry points and config path references (3569398)
- Replace native browser alert dialogs with premium inline alerts (5c68b52)
- Document password reset procedure in USER_GUIDE.md (c73313e)
- Add forgot password flow showing recovery PIN on physical screen (42f102b)
- Document password recovery philosophy in AGENTS.md (0e7efac)
- Add rule 0c to prevent implementation on questions in AGENTS.md (8ac4a1d)
- Remove password recovery philosophy section from AGENTS.md (b49c0a6)
- Add rule banning native browser alert dialogs (7e584ad)
- Replace all native alert and confirm dialogs with custom confirmation overlay modals (01a1a5f)
- Constrain layout grid rows and columns to prevent height overflow (93fb008)
- Pin MIRRORDASH_CONFIG_PATH to repo root in conftest to protect local dev config (e525958)
- Replace rigid grid with floating anchor layout, add per-module max_width/max_height (7d18770)
- Split AGENTS.md into modular rules files (ff8502a)
- Add z_index and opacity per-module config properties (70f3f86)
- Standardize 8 core-owned fields; inject into admin form, cast on save, apply in kiosk (5eb9848)
- Support screen padding (safe margin) as a global setting (2ea0369)
- Change global safe_margin to individual top/bottom/left/right dropdown settings (70f9a4b)
- Change safe_margin to a nested object rendered as an accordion in the Admin UI with numeric px inputs (e568cfc)
- Extend form generator with new input controls and design docs (2501169)
- Add tests for news builtin feeds and weather hourly parsing (e851efb)
- Add uv.lock to ensure reproducible dependency resolution (b25e2e5)
## [0.3.4] - 2026-06-29

### Core App
- Add glassmorphic restart overlay and fix config drawer toggle (de77bf6)
- Add Playwright integration tests and unify dev dependencies (0141cd0)
- Stabilize and complete playwright visual test suite (ce6257b)
- Implement professional wildcard HTTP redirects for captive portal (3164cf9)
- Update CHANGELOG for visual tests and captive portal changes (7cb5f1d)
- Automatically generate CHANGELOG using git-cliff (818e524)
- Final automated regeneration of CHANGELOG.md (623246a)
- Bump version to 0.3.4 (de826e3)
## [0.3.3-os1] - 2026-06-28

### Core App
- Organize CHANGELOG for 0.3.3-os1 OS image release (abe3f41)
## [0.3.3] - 2026-06-28

### Core App
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
## [0.3.2-os1] - 2026-06-28

### Core App
- Organize CHANGELOG for 0.3.2-os1 OS image release (13ada17)
## [0.3.2] - 2026-06-28

### Core App
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
## [0.3.1-os1] - 2026-06-25

### Core App
- Implement git-cliff configuration and update commit guidelines (263f337)
- Regenerate CHANGELOG.md using git-cliff (8641cdd)
## [0.3.1] - 2026-06-25

### Core App
- Match correct final image artifact in github workflow release step (aa9604d)
- Append Wayland, NetworkManager, and kiosk systemd architectural rules (2b3cb06)
- Revert "docs(agents): append Wayland, NetworkManager, and kiosk systemd architectural rules"

This reverts commit 2b3cb0639acdabdcd7d0026a5e24e33467cd653c. (88b2009)
- Add Strict DevOps Build Philosophy rule 0b (572d6ec)
- Harden A/B venv update and backup restore by explicitly targeting python binary (132f398)
- Update CHANGELOG.md for changes since 0.3.0 (399dc9e)
- Condense CHANGELOG.md Unreleased System OS section (b84776b)
- Bump version to 0.3.1 (ba5d2f7)
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

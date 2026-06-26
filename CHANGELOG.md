# Changelog

All notable changes to the MirrorDash project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
## [Unreleased]

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
- Implement git-cliff configuration and update commit guidelines (263f337)
- Regenerate CHANGELOG.md using git-cliff (8641cdd)
- Organize CHANGELOG for 0.3.1-os1 OS image release (4225ba7)
- Update CHANGELOG.md for plymouth and seatd fixes (7293e8a)
- Update CHANGELOG.md for wlr-randr retry and seatd dependency commit (2397b45)
- Update CHANGELOG.md for seatd container build fixes (8ecba90)
- Update CHANGELOG.md for redundant systemctl purge commit (1849b85)
- Update CHANGELOG.md (f9fbf57)
- Update CHANGELOG.md (5ae8383)

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
## [0.3.0-os2] - 2026-06-23

### Core App

- Mock SSH status check in display power test to avoid password prompts (eefb314)
- Emphasize /design live explorer in main README (7584e14)
- Align design explorer and tokens with Ethereal specs (c1796e1)
- Add zero-css layout utilities and documentation (1b3a382)
- Update build-os-image.yml (b7b795a)

### System OS (Appliance)

- Ensure initramfs is deployed to FAT32 boot partition (099f33d)
- Update build_image.sh (685f952)
- Update setup_appliance.sh (659be3e)
- Update finalize_appliance.sh (7f0faa3)
- Update build_image.sh (679c482)
## [0.3.0-os1] - 2026-06-22

### Core App

- Clarify interactive release wizards and version tracking requirements (9f8282d)
- Organize CHANGELOG for 0.3.0-os1 OS image release (824c1d4)

### System OS (Appliance)

- Enforce latest core app version tracking in release_os.py (9467187)
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

### System OS (Appliance)

- Fix autologin.conf whitespace bug and auto_initramfs build conflict (14e64ff)
- Add guided version wizards to release scripts (a7ced88)
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

### System OS (Appliance)

- Drop QEMU from build_image.sh, add GitHub Actions ARM64 workflow (5a84caa)
- Add Python release automation scripts (release_core.py, release_os.py, release_changelog.py) (1cb3f41)
- Reference release automation scripts in RELEASING.md workflow steps (f02d691)
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

### System OS (Appliance)

- Optimize appliance setup, finalizer, and silent kiosk boot flow (9f06a83)
- Optimize package setup and parallel initramfs compression in setup_appliance.sh (3fa67d7)
- Add execution time tracking to all steps in setup_appliance.sh (2f3a036)
- Add overall execution time trackers to build and setup scripts (ade05dd)
- Strip system locales and purge unused packages in setup_appliance.sh (8198541)
- Added optimizations for build_image.sh (9cc9131)
- Make captive portal fully offline-capable and persist WiFi credentials (0025f3f)
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

### System OS (Appliance)

- A/B update system, OverlayFS hardening, AGENTS.md IoT rules, and doc-code sync audit (996c829)
- AGENTS.md rule 30 (doc-code sync), GOLDEN_IMAGE.md cleanup, setup_appliance.sh production hardening (46645af)
- Advertise mirrordash.local via avahi mDNS (no IP address needed) (c06ff72)
- Add nginx reverse proxy to drop :8000 from URLs (ae736c9)

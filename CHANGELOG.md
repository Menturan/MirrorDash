# Changelog

All notable changes to the MirrorDash project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Core App
- Hardened the A/B virtual environment update endpoints and backup restoration system by explicitly targeting the target python binary path, avoiding active venv symlink/environment dependencies.
- Resolved Python runtime name collision warnings (`UnboundLocalError` on local `Path` imports).

### System OS (Appliance)
- Migrated Wayland (`labwc`) and the kiosk browser (`cog`) to native systemd services, resolving logind-seatd race conditions.
- Implemented asynchronous, non-blocking captive portal checks using NetworkManager (`nm-online`) to eliminate boot delays.
- Refactored first-boot storage hydration (`mirrordash-storage-init.service`) from a golden `base_venv` to prevent environment corruption.
- Hardened script stability with atomic rollback moves (`mv -T`), failsafe build `fstab` restoration traps, and a robust `firstboot` override.
- Optimized the build by using canonical image redirect tracking, resolving Plymouth DRM resource contention, and pruning unneeded packages/firmware.
- Synchronized the manual golden image guide (`GOLDEN_IMAGE.md`) with the automated scripts.

## [0.3.0-os2] - 2026-06-23

### Core App
- Added zero-CSS layout utilities and documentation, and aligned the Design System Explorer and tokens with Ethereal specifications.
- Mocked SSH status check in display power tests to avoid password prompts during test suites.
- Emphasized `/design` live explorer in the main README.

### System OS (Appliance)
- Ensured initramfs (containing the boot splash/Plymouth configs) is deployed to the FAT32 boot partition correctly on Debian Trixie.
- General updates to `build_image.sh`, `setup_appliance.sh`, `finalize_appliance.sh`, and GitHub Actions workflow configuration.

## [0.3.0-os1] - 2026-06-23

### Core App

### System OS (Appliance)
- Added Getty auto-login configuration fix for auto-launching kiosk on system boot.
- Fixed various automated system image build issues during offline QEMU runs.
- Migrated default kiosk browser engine from Chromium to Cog (WebKit) on Wayland for a lighter, kiosk-optimized display footprint.
- Re-enabled automatic initramfs rebuild configurations in production image pipelines to ensure boot reliability.

## [0.3.0] - 2026-06-23

### Core App
- Added a form generator utility and integrated local `htmx.min.js` to ensure the dashboard is fully offline-capable.
- Added dynamic background scanning of PyPI simple index for community modules starting with `mirrordash-`.
- Added integration tests for kiosk JS modular structure.
- Created guided version bumping wizards in `release_core.py` and `release_os.py` for automated SemVer/OS release management.
- Fixed layout, reset-style text color visibility, and backdrop-filter issues in kiosk prompts on WebKit/Cog browser.
- Migrated all admin dashboard panels to HTMX for a dynamic, single-page application experience.
- Moved Lucide library from CDN to local `static/js/lucide.min.js` for offline reliability.
- Preserved active page tabs in the admin panel across page reloads using URL hash tracking.
- Refactored `index.html` into modular ES files under `static/js/kiosk/`. Split into `design-tokens.js`, `setup-prompt.js` (Web Component), and `core.js`.
- Relaxed frontend framework rule from "forbidden" to "conservative adoption" in AGENTS.md. Frameworks require justification but are no longer categorically banned.
- Self-hosted the Inter variable font for offline kiosk reliability, replacing the Google Fonts CDN dependency.
- Simplified setup prompts on the kiosk screen by serving static HTML prompt pages (`wifi_prompt.html` and `admin_prompt.html`) directly from the backend, replacing the complex dynamic Web Component and polling logic.
- Split `admin_modules.py` and `admin_system.py` into separate JSON REST API endpoints and HTMX panel-rendering endpoints (`admin_modules_panels.py` and `admin_system_panels.py` respectively) to reduce monolithic file complexity.
- Split monolithic `admin.py` into modular domain-specific sub-routers (`admin_auth.py`, `admin_config.py`, `admin_system.py`, etc.).

### System OS (Appliance)

## [0.2.4-os1] - 2026-06-15

### System OS (Appliance)
- AGENTS.md documentation updates (TOC rules, build pipeline clarifications).
- Boot fallback launcher with crash detection and automatic rollback to venv_old or safe mode.
- CI Python version bump to 3.14 in publish.yml.
- Golden image pipeline updates (RELEASING.md workflows, checksum verification, local asset integration).
- Master branch shortcuts for Setup-Clone and Rebuild-Clone selection rules.
- Plymouth cursor customization and invisible cursor theme for kiosk displays.
- QEMU build infrastructure (native pigz, initramfs compression fixes, dependency verification).
- Setup appliance improvements (apt optimization, storage migration, NM persistence, cleanup steps, timers).
- System configuration state restoration (timezone, SSH toggle, password hash) under OverlayFS.

### Core App

## [0.2.4] - 2026-06-15

### Core App
- Rewrote wifi_setup.html as a fully self-contained offline page, fixing blank-screen rendering in captive portal AP mode.
- Pre-AP scan cache in mirrordash-wifi-check.sh before entering hotspot mode.
- scan_wifi_networks() falls back to pre-AP cached scan when nmcli fails under active AP mode.
- connect_wifi() tears down MirrorDash-Setup AP profile before connecting client network.
- Added ensure_nm_wifi_persistence() helper for NetworkManager profiles on OverlayFS devices.
- Made loading.html fully offline-capable with system font stack.
- Added boot status awareness to loading.html for rollback/safe mode messages.
- Added tests for WiFi scan cache fallback and AP teardown.
- Noted WiFi credential exclusion on admin backup page.

### System OS (Appliance)

## [0.2.3] - 2026-06-14

### Core App
- Added local high-contrast loading.html that polls FastAPI health check on startup.

### System OS (Appliance)

## [0.2.2] - 2026-06-12

### Core App
- Added missing python-multipart dependency to prevent FastAPI runtime crash.
- Relocated static/ and templates/ inside mirrordash_core package for PyPI wheel compatibility.
- Refactored app.py path resolution for production boot stability.

### System OS (Appliance)

## [0.2.1] - 2026-06-12

### Core App
- Fixed PyPI Trusted Publishing OIDC token authentication.
- Resolved port 8000 hardcoding for nginx reverse proxy.
- Fixed VALID_POSITIONS to match 9 valid CSS grid regions.

### System OS (Appliance)

## [0.2.0] - 2026-06-12

### Core App
- A/B virtual environment update architecture with atomic symlink activation.
- Glassmorphic status alert banners in Admin dashboard.
- Ambient HUD notification cards for rollback/Safe Mode.
- Dedicated /admin/rebuild-venv API endpoint.
- Unit tests for chpasswd and rebuild-venv logic.

### System OS (Appliance)

## [0.1.0] - 2026-05-30

### Core App
- Initial release with modular architecture, module system, visual config editor, and admin dashboard.

### System OS (Appliance)


[Unreleased]: https://github.com/Menturan/MirrorDash/compare/v0.3.0-os2...HEAD
[0.3.0-os2]: https://github.com/Menturan/MirrorDash/compare/v0.3.0-os1...v0.3.0-os2
[0.3.0-os1]: https://github.com/Menturan/MirrorDash/compare/v0.3.0...v0.3.0-os1
[0.3.0]: https://github.com/Menturan/MirrorDash/compare/v0.2.4...v0.3.0
[0.2.4-os1]: https://github.com/Menturan/MirrorDash/compare/v0.2.4...0.2.4-os1
[0.2.4]: https://github.com/Menturan/MirrorDash/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/Menturan/MirrorDash/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/Menturan/MirrorDash/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Menturan/MirrorDash/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Menturan/MirrorDash/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Menturan/MirrorDash/releases/tag/v0.1.0

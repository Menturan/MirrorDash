# Changelog

All notable changes to the MirrorDash project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

## [Unreleased]

### Core App
- Relaxed frontend framework rule from "forbidden" to "conservative adoption" in AGENTS.md. Frameworks require justification but are no longer categorically banned.

### System OS (Appliance)

[Unreleased]: https://github.com/Menturan/MirrorDash/compare/v0.2.4-os1...HEAD
[0.2.4-os1]: https://github.com/Menturan/MirrorDash/compare/v0.2.4...0.2.4-os1
[0.2.4]: https://github.com/Menturan/MirrorDash/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/Menturan/MirrorDash/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/Menturan/MirrorDash/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Menturan/MirrorDash/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Menturan/MirrorDash/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Menturan/MirrorDash/releases/tag/v0.1.0

# Changelog

All notable changes to the MirrorDash project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.2] - 2026-06-12

### Fixed
- Added missing `python-multipart` dependency to `pyproject.toml` to prevent FastAPI runtime crash on startup when handling form data / file uploads.
- Relocated the `static/` and `templates/` resource directories inside the `mirrordash_core` package to prevent PyPI wheel installations from omitting essential frontend assets.
- Refactored `app.py` path resolution logic to dynamically load assets relative to the package directory using local module resolution, fixing `RuntimeError` directory missing boot crashes in production.
- Corrected Plymouth splash screen image URLs and layouts across setup scripts, golden image guidelines, and agent/design documentation files.

## [0.2.1] - 2026-06-12

### Fixed
- Fixed appliance setup step 7 (`installing_app`) by using a robust heredoc execution pattern instead of fragile `sudo -i` argument concatenation with nested quotes, resolving a bug where virtual environments were not created during setup.
- Fixed PyPI Trusted Publishing OIDC token authentication by explicitly configuring the `pypi` deployment environment on the GitHub Actions job.
- Resolved port `8000` hardcoding in captive portal setup instructions (`USER_GUIDE.md`, `ARCHITECTURE.md`, and test suites) to support the new `nginx` port 80 reverse proxy.
- Fixed `VALID_POSITIONS` in `admin.py` to match the 9 valid CSS grid regions, removing obsolete positions (`top_bar`, `upper_third`, `lower_third`, `bottom_bar`).
- Corrected duplicate step numbers in `setup_appliance.sh` after introducing `nginx` setup.

## [0.2.0] - 2026-06-12

### Added
- A/B virtual environment update architecture to stage updates in a separate clone (`venv_next`) before atomic symlink activation, preventing system bricks on update failures.
- Boot fallback launcher (`launch.sh`) with startup crash detection and automatic rollback to the previous stable copy (`venv_old`) or fallback to the read-only Golden Copy (`base_venv` in Safe Mode).
- Programmatic boot-time restoration of system configuration states (timezone, SSH toggle state, and system password hash) under OverlayFS.
- Glassmorphic status alert banners in the Admin dashboard with interactive "Rebuild Active Environment" trigger.
- Ambient HUD notification cards in the kiosk mirror interface during system rollback or Safe Mode states.
- Dedicated `/admin/rebuild-venv` API endpoint to rebuild and reinstall the core application and modules from scratch.
- Unit tests verifying chpasswd execution, rebuild-venv logic, and A/B update validation.

### Changed
- Reordered appliance setup script execution steps to mount and format the persistent partition before setting up the virtual environments.
- Updated golden image guide commands, chapters, and persistence grid to reflect the A/B virtual environment symlink mappings and settings restoration.

## [0.1.0] - 2026-05-30

### Features
- Auto-inject render_template helper for custom modules
- Automate jinja2 configuration in module scaffolding
- Support filtering module logs by specific module in UI and API
- Add module loop templates logging, clock module logging, and admin API logging
- Add new Log tab displaying system, module, and host OS logs
- Add mirrordash-cli module generator to automate custom module packaging
- Automate naming normalization and support synchronous run_loops safely
- Automatically pre-create and inject writeable data_dir parameter for modules
- Support standalone JSON config schema files and migrate clock module schema
- Move module updates to separate page-level Updates tab and resolve visual editor hangs
- Initial commit with modular architecture, optimized remounts, and visual config editor

### Bug Fixes
- Resolve onload DOM element selection ordering error by placing modal above script
- Resolve JavaScript error from non-existent .title() call and add robust rendering error boundaries

### Refactoring
- Migrate clock module layout rendering to Jinja2 template

### Documentation
- Document auto-injected render_template helper and overrides
- Document mirrordash-cli automated scaffolding in module guide
- Relocate developer gotchas into their respective chapters and remove section 7
- Add section on common gotchas and best practices for module developers

### Chores
- Add jinja2 to dependencies in pyproject.toml

[Unreleased]: https://github.com/Menturan/MirrorDash/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/Menturan/MirrorDash/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Menturan/MirrorDash/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Menturan/MirrorDash/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Menturan/MirrorDash/releases/tag/v0.1.0

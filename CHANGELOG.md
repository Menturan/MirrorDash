# Changelog

All notable changes to the MirrorDash project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### System OS (Appliance)
- Optimized APT package installation in `setup_appliance.sh` to run under `eatmydata` (bypassing emulated fsync overhead), use multi-threaded `pigz` for initramfs compression (preserving maximum gzip density while utilizing all host cores), exclude package documentation files (saving crucial rootfs disk space), and divert `update-initramfs` and `mandb` triggers to run only once at the end of the installation process.
- Added standard SHA256 integrity verification for base Raspberry Pi OS Lite downloads in `build_image.sh`.
- Added support for local development asset integration in `setup_appliance.sh` (copies `launch.sh`, `loading.html`, `splash.png`, `shutdown.png`, and `finalize_appliance.sh` from the `/opt/MirrorDash` repository folder if it exists, rather than always downloading from GitHub), ensuring local changes are correctly packaged into new Golden Images.
- Added explicit `python3` installation to `setup_appliance.sh` package list to ensure cursor generation scripts run reliably.
- Added an automated system cleanup step in the setup script and documented the corresponding commands in the golden image finalization guide to prune caches, logs, and temp files before enabling OverlayFS.
- Boot fallback launcher (`launch.sh`) with startup crash detection and automatic rollback to the previous stable copy (`venv_old`) or fallback to the read-only Golden Copy (`base_venv` in Safe Mode).
- Programmatic boot-time restoration of system configuration states (timezone, SSH toggle state, and system password hash) under OverlayFS.
- Fixed a fatal crash in `build_image.sh` where it attempted to modify and execute a non-existent `mirrordash-finalize.sh` under `/opt/MirrorDash/scripts/` during the finalize step. It now modifies the correct installed script `/usr/local/bin/mirrordash-finalize.sh` and runs it.
- Fixed infinite directory recursion copying bug in `build_image.sh` by replacing `cp -r` with `find` to copy the repository into the chroot while excluding `build_workspace` and hidden directories (e.g. `.git`, `.venv`).
- Fixed loop device and mount point resource leaks by moving the `cleanup_and_unmount` EXIT trap to the start of `build_image.sh` execution.
- Hardened `build_image.sh` host file deletions and system directory unmountings by verifying that `MOUNT_DIR` is set, exists, and is not `/` before performing cleanup operations.
- Fixed a possible PiShrink hang on overwrite confirmation by deleting any existing `mirrordash-final.img.gz` before shrinking.
- Fixed directory state leakage in `build_image.sh` by wrapping the download and decompression pipeline in a subshell.
- Fixed potential corrupt base image decompression bugs by decompressing to a temporary `.tmp` file and renaming on completion.
- Eliminated redundant code duplication by replacing the copy of `finalize_appliance.sh` inside `setup_appliance.sh` with a copy/download setup step.
- Fixed a potential directory creation issue in the `raspi-config` wrapper by ensuring `/etc/initramfs-tools/scripts` exists before writing the overlayfs configuration inside the chroot.
- Fixed touchscreen cursor visibility on boot by creating a local transparent cursor theme (`invisible`) and configuring `labwc` to use it via its environment configuration file, ensuring the pointer is never rendered on kiosk displays without modifying system icon packages.
- Silenced system status text rendering at the bottom of the boot splash screen by commenting out message callback registrations in the Plymouth `pix.script` theme configuration.
- Silenced the tty1 autologin console messages and shell login banners (MOTD, last login) at the end of the boot sequence by adding agetty silencing flags and touching the `.hushlogin` file.
- Fixed partition expansion logic in `setup_appliance.sh` and `GOLDEN_IMAGE.md` to dynamically detect the boot disk and construct partition paths, resolving an issue where USB/SSD or NVMe booted devices would fail due to hardcoded `/dev/mmcblk0` references.
- Fixed a bug in `setup_appliance.sh` where custom Plymouth splash setup was skipped on fresh installations because the default theme package pre-installs a generic `splash.png`. Introduced a sentinel file (`.mirrordash_configured`) to track custom theme configuration.
- Corrected Plymouth splash screen image URLs and layouts across setup scripts, golden image guidelines, and agent/design documentation files.
- Fixed appliance setup step 7 (`installing_app`) by using a robust heredoc execution pattern instead of fragile `sudo -i` argument concatenation with nested quotes, resolving a bug where virtual environments were not created during setup.
- Corrected duplicate step numbers in `setup_appliance.sh` after introducing `nginx` setup.
- Reordered appliance setup script execution steps to mount and format the persistent partition before setting up the virtual environments.
- Updated golden image guide commands, chapters, and persistence grid to reflect the A/B virtual environment symlink mappings and settings restoration.

## [0.2.3] - 2026-06-14

### Core App
- Added a local, high-contrast HTML loading screen (`loading.html`) styled after the Ethereal design system that loads instantly on startup and polls the FastAPI health check to redirect the browser when the app is online.

## [0.2.2] - 2026-06-12

### Core App
- Added missing `python-multipart` dependency to `pyproject.toml` to prevent FastAPI runtime crash on startup when handling form data / file uploads.
- Relocated the `static/` and `templates/` resource directories inside the `mirrordash_core` package to prevent PyPI wheel installations from omitting essential frontend assets.
- Refactored `app.py` path resolution logic to dynamically load assets relative to the package directory using local module resolution, fixing `RuntimeError` directory missing boot crashes in production.

## [0.2.1] - 2026-06-12

### Core App
- Fixed PyPI Trusted Publishing OIDC token authentication by explicitly configuring the `pypi` deployment environment on the GitHub Actions job.
- Resolved port `8000` hardcoding in captive portal setup instructions (`USER_GUIDE.md`, `ARCHITECTURE.md`, and test suites) to support the new `nginx` port 80 reverse proxy.
- Fixed `VALID_POSITIONS` in `admin.py` to match the 9 valid CSS grid regions, removing obsolete positions (`top_bar`, `upper_third`, `lower_third`, `bottom_bar`).

## [0.2.0] - 2026-06-12

### Core App
- A/B virtual environment update architecture to stage updates in a separate clone (`venv_next`) before atomic symlink activation, preventing system bricks on update failures.
- Glassmorphic status alert banners in the Admin dashboard with interactive "Rebuild Active Environment" trigger.
- Ambient HUD notification cards in the kiosk mirror interface during system rollback or Safe Mode states.
- Dedicated `/admin/rebuild-venv` API endpoint to rebuild and reinstall the core application and modules from scratch.
- Unit tests verifying chpasswd execution, rebuild-venv logic, and A/B update validation.

## [0.1.0] - 2026-05-30

### Core App
- Auto-inject render_template helper for custom modules.
- Automate jinja2 configuration in module scaffolding.
- Support filtering module logs by specific module in UI and API.
- Add module loop templates logging, clock module logging, and admin API logging.
- Add new Log tab displaying system, module, and host OS logs.
- Add mirrordash-cli module generator to automate custom module packaging.
- Automate naming normalization and support synchronous run_loops safely.
- Automatically pre-create and inject writeable data_dir parameter for modules.
- Support standalone JSON config schema files and migrate clock module schema.
- Move module updates to separate page-level Updates tab and resolve visual editor hangs.
- Initial commit with modular architecture, optimized remounts, and visual config editor.
- Resolve onload DOM element selection ordering error by placing modal above script.
- Resolve JavaScript error from non-existent .title() call and add robust rendering error boundaries.
- Migrate clock module layout rendering to Jinja2 template.
- Document auto-injected render_template helper and overrides.
- Document mirrordash-cli automated scaffolding in module guide.
- Relocate developer gotchas into their respective chapters.
- Add section on common gotchas and best practices for module developers.
- Add jinja2 to dependencies in pyproject.toml.

[Unreleased]: https://github.com/Menturan/MirrorDash/compare/v0.2.3...HEAD
[0.2.3]: https://github.com/Menturan/MirrorDash/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/Menturan/MirrorDash/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Menturan/MirrorDash/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Menturan/MirrorDash/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Menturan/MirrorDash/releases/tag/v0.1.0

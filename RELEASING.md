# MirrorDash Release & Deployment Process

This document outlines the release, testing, and deployment workflows for MirrorDash.

> [!IMPORTANT]
> **Core App Release vs. System OS Release: The Crucial Distinction**
>
> | Property | Core Application (`mirrordash`) | System OS Image (`mirrordash-os`) |
> | :--- | :--- | :--- |
> | **What it is** | The Python application running the FastAPI server and Admin dashboard. | The underlying operating system configuration, drivers, Wayland compositor, and system packages. |
> | **When to release** | Whenever new features, layout changes, module upgrades, or Python bug fixes are merged. | Only when system packages (e.g. `nginx`, `plymouth`, `labwc`), hardware configurations, or network fallback scripts are modified. |
> | **Release target** | Published to PyPI via automated GitHub Actions. | Built on GitHub Actions `ubuntu-24.04-arm64` runners (real ARM hardware, no emulation) and attached directly to the GitHub Release. |
> | **Deployment method** | **Non-Destructive**: Triggered via the Admin Dashboard's "Updates" tab (uses A/B `venv` partition updates). | **Destructive**: Requires flashing the SD card. Backup settings first, flash, configure Wi-Fi, and restore settings. |
> | **Risk level** | **Low**: Handled by the A/B virtual environment update system with automatic rollback to `venv_old` or Safe Mode. | **High**: Overwrites all card data. System must be re-provisioned via the Wi-Fi Captive Portal on first boot. |
> | **Tag format** | `vX.Y.Z` (e.g. `v0.2.4`) | `vX.Y.Z-osN` (e.g. `v0.2.4-os1`) |

## Table of Contents

- [Release Guidelines](#release-guidelines)
- [Two Release Tracks](#two-release-tracks)
- [Track 1: Core App Release](#track-1-core-app-release)
- [Track 2: System OS Image Release](#track-2-system-os-image-release)
- [Architecture & Infrastructure Behind Releases](#architecture--infrastructure-behind-releases)
- [Client Update & Deployment Procedures](#client-update--deployment-procedures)
- [Manual Reference (Legacy)](#manual-reference-legacy)

---

## Release Guidelines

- **Only Release Stable Code**: Ensure all tests pass successfully before releasing.
- **Strict SemVer**: Follow [Semantic Versioning](https://semver.org/). Bumps are:
  - `patch` (e.g. `0.2.1` -> `0.2.2`) for backward-compatible bug fixes.
  - `minor` (e.g. `0.2.x` -> `0.3.0`) for new, backward-compatible features.
  - `major` (e.g. `0.x.x` -> `1.0.0`) for API-breaking changes.
- **Do Not Manually Tag Locally**: Let the GitHub Release interface create the git tag. This ensures that the GitHub Release, git tag, and PyPI package version are always perfectly aligned.
- **Changelog Automation & Commit Scopes**: We use `git-cliff` (configured in `cliff.toml`) to automatically group and generate the changelog. To ensure commits are correctly sorted, you must follow the Conventional Commits specification and specify one of the following scopes:
  - **Core App**: `api`, `core`, `ui`, `design`, `kiosk`, `frontend`, `auth`, `config` (e.g., `feat(api): add new modules endpoint`).
  - **System OS (Appliance)**: `scripts`, `os`, `golden-image`, `appliance` (e.g., `fix(scripts): resolve logind race condition`).
  - Commits without conventional scopes will fallback to `Core App` (unless keywords like "scripts" or "appliance" are detected in the description).

To run `git-cliff` manually to update or re-generate `CHANGELOG.md`:
```bash
git-cliff -o CHANGELOG.md
```

---

## Two Release Tracks

MirrorDash produces **two independent artifacts** from the same repository. They have separate lifecycles, versioning, and deployment methods.

| | **Track 1: Core App** | **Track 2: System OS Image** |
|:---|:---|:---|
| **Artifact** | `mirrordash` Python package (PyPI) | `mirrordash-os-vX.Y.Z.img.gz` (GitHub Release asset) |
| **Trigger** | Push tag `vX.Y.Z` | Push tag `vX.Y.Z-osN` |
| **Build** | GitHub Actions `publish.yml` (x86_64, build sdist/wheel) | GitHub Actions `build-os-image.yml` (ARM64, native build) |
| **CHANGELOG** | Move `[Unreleased]` Core App entries → `[X.Y.Z]` section | Move `[Unreleased]` System OS entries → new `[X.Y.Z-osN]` section (see Track 2 below) |
| **When to use** | Every release with code changes | Only when OS-level changes (packages, scripts, boot config) need a new golden image |

> [!IMPORTANT]
> **CHANGELOG discipline**: `[X.Y.Z]` sections contain **only** Core App changes. System OS appliance changes **must remain under `[Unreleased]`** until the golden image is tested and released with its own `vX.Y.Z-osN` tag. Never mix System OS entries into a Core App version block.

---

## Track 1: Core App Release

For Python package changes: new features, bug fixes, module updates, admin dashboard changes, API changes.

### 1. Pre-Release (Automated)

Use the release helper script to run tests, bump the version, reorganize the CHANGELOG, and push:

```bash
python3 scripts/release_core.py [version]
```

> [!TIP]
> **Interactive Wizard**: If you run `python3 scripts/release_core.py` with no version argument, it starts an interactive CLI wizard that parses the current version from `pyproject.toml` and guides you to select a **bugfix (patch)**, **minor**, **major**, or **prerelease** bump (with custom or standard `alpha`/`beta`/`rc` suffix selection). If the current version is already a prerelease (e.g. `0.2.5-rc1`), it offers to automatically increment it to the next prerelease build (`0.2.5-rc2`).

The script will:
1. Validate the SemVer version argument
2. Ensure you are on `master` and pull latest
3. Run the full test suite (`.venv/bin/pytest`) — halts on failure
4. Bump `version` in `pyproject.toml`
5. Reorganize `CHANGELOG.md` (Core App entries → `[0.2.5]`, System OS stays under `[Unreleased]`)
6. Commit and push to `origin/master`

> [!NOTE]
> The script does **not** write CHANGELOG entries — someone must have already added them under `[Unreleased]` before running it. It only reorganizes existing entries into the correct versioned section.

### 2. Create the GitHub Release

1. Navigate to **Releases** → **Draft a new release**.
2. Choose a tag `v0.2.5` → **Create new tag on publish**.
3. Title matches the tag (e.g. `v0.2.5`).
4. Click **Publish release**.

### 3. Verification

The **Publish to PyPI** workflow runs automatically:
1. Go to the **Actions** tab and monitor the workflow.
2. Verify the package appears on [PyPI](https://pypi.org/project/mirrordash/).

If you need to perform these steps manually instead of using the script, see the [manual reference](#manual-reference-track-1-core-app).

---

## Track 2: System OS Image Release

For OS-level changes: new system packages, Plymouth themes, labwc config, network fallback scripts, boot parameters, initramfs changes, `setup_appliance.sh` modifications.

> [!IMPORTANT]
> **Prerequisite**: The Core App `vX.Y.Z` release should already exist. The OS image tracks the Core App version (e.g. `v0.2.4-os1` is the first OS image for Core App `v0.2.4`).

### 1. Pre-Release (Automated)

Use the release helper script to run tests, reorganize the CHANGELOG, and push:

```bash
python3 scripts/release_os.py [version]
```

> [!TIP]
> **Interactive Wizard**: If you run `python3 scripts/release_os.py` with no version argument, it starts an interactive CLI wizard. To adhere to the dual-artifact model, the script automatically reads the active Core App version from `pyproject.toml` and scans `CHANGELOG.md` to recommend the next OS build number for that specific core version (e.g. `X.Y.Z-os1` or `X.Y.Z-os(N+1)`). You can select the recommended version or enter a custom build suffix for the active core base.

The script will:
1. Validate the `X.Y.Z-osN` version argument
2. Ensure you are on `master` and pull latest
3. Run the full test suite (`.venv/bin/pytest`) — halts on failure
4. Reorganize `CHANGELOG.md` (System OS entries → `[0.2.4-os1]`, Core App stays in `[0.2.4]`)
5. Commit and push to `origin/master`

> [!NOTE]
> The script does **not** write CHANGELOG entries — someone must have already added them under `[Unreleased]` before running it. It only moves existing entries to the correct versioned section.

### 2. Create the GitHub Release

1. Navigate to **Releases** → **Draft a new release**.
2. Choose a tag `v0.2.4-os1` (first OS image for this version) → **Create new tag on publish**.
3. Title matches the tag (e.g. `v0.2.4-os1`).
4. Click **Publish release**.

### 3. Automated Build & Upload

The **Build OS Image** workflow triggers automatically on `ubuntu-24.04-arm64`:

1. **Free disk space** (`EisBear/free-disk-space-ubuntu-runners@v1`)
2. **Checkout** repository
3. **Install deps**: `parted`, `xz-utils`, `e2fsprogs`, `pigz`, `wget`, `curl`, `pishrink.sh`
4. **Run `scripts/build_image.sh`** — native ARM, produces `build_workspace/mirrordash-os-vX.Y.Z.img.gz` + `.sha256`
5. **Upload** both files as GitHub Release assets

> [!TIP]
> Monitor the workflow in the **Actions** tab. Build time is typically 15–30 minutes.

### 4. Verification & Testing Checklist

Download the image from the GitHub Release and test on real hardware:

1. **Boot Splash & Plymouth**: Custom splash appears, no systemd status messages or login prompts.
2. **Invisible Mouse Cursor**: Cursor remains hidden on the kiosk display.
3. **Failsafe Captive Portal**:
   - Boot without Ethernet or saved WiFi → `MirrorDash Setup` AP activates within 30 seconds.
   - Connect, visit `http://10.42.0.1/wifi-setup`, enter credentials, submit.
   - System remounts, saves profiles, and reboots.
4. **Dashboard & Mirror Load**: `index.html` loads, skeletons appear, WebSocket connects, widgets render.
5. **Admin Access**: Dashboard reachable at `http://mirrordash.local/admin`, requires API key.

### 5. Rebuilding a Failed Image

If the workflow fails (runner issues, network timeouts):
1. Delete the failed GitHub Release (or just the tag).
2. Recreate the tag with the same name — a new workflow run starts automatically.
3. No need to bump the `-osN` suffix unless you want to track multiple attempts.

---

## Architecture & Infrastructure Behind Releases

### GitHub Actions (OIDC)
Our release workflow uses the official PyPA action `pypa/gh-action-pypi-publish@release/v1` combined with GitHub's OIDC (OpenID Connect) provider.

Inside [.github/workflows/publish.yml](file:///home/menturan/repos/mymagicmirror/.github/workflows/publish.yml), we request specific token write permissions:
```yaml
permissions:
  id-token: write
```
This is configured to match the registered **Trusted Publisher** on the PyPI dashboard under the `pypi` environment. This secures our publishing pipeline against credential leaks.

### OS Image Build Infrastructure
The OS image is built on `ubuntu-24.04-arm64` GitHub Actions runners — real ARM hardware with no emulation. The runner:

- Has native `aarch64` CPU, so `update-initramfs`, `raspi-config`, and all ARM binaries execute directly
- Uses `EisBear/free-disk-space-ubuntu-runners@v1` to clear pre-installed toolchains (dotnet, swift, android, haskell) before the build
- Installs only the minimal required packages via `apt`
- Downloads `pishrink.sh` at runtime

This eliminates the QEMU-related initramfs corruption, white-screen boot issues, and pigz warnings that plagued the previous x86_64 + QEMU build pipeline.

---

## Manual Reference (Legacy)

If you need to perform release steps without the automation scripts (e.g. debugging, offline environment), the manual procedures below mirror what the scripts do.

### Track 1: Core App (Manual)

1. **Pre-Release Checklist**:
   - Ensure you are on `master` and up to date
   - Run `.venv/bin/pytest` — all tests must pass
   - Bump `version` in `pyproject.toml`
   - Move Core App entries under `[Unreleased]` → new `## [X.Y.Z] - YYYY-MM-DD` section
   - Leave System OS entries under `[Unreleased]`
   - Update comparison links at the bottom of CHANGELOG.md

2. **Commit and Push**:
   ```bash
   git add pyproject.toml CHANGELOG.md
   git commit --no-gpg-sign -m "chore: bump version to X.Y.Z"
   git push origin master
   ```

3. Create the GitHub Release (tag `vX.Y.Z`) and verify PyPI publish.

### Track 2: System OS Image (Manual)

1. **Update CHANGELOG.md**:
   - Move System OS entries from `[Unreleased]` → new `## [X.Y.Z-osN] - YYYY-MM-DD` section
   - Core App entries stay in `[X.Y.Z]`

2. **Commit and Push**:
   ```bash
   git add CHANGELOG.md
   git commit --no-gpg-sign -m "chore: organize CHANGELOG for vX.Y.Z-osN OS image release"
   git push origin master
   ```

3. Create the GitHub Release (tag `vX.Y.Z-os1`) — the automated ARM build workflow will handle the rest.

---

## Client Update & Deployment Procedures

Once a new release is available, follow these instructions to apply it to a running MirrorDash kiosk.

### 1. Deploying a Core App Update (Non-Destructive)

To upgrade the core application on active devices:

#### Method A: Online Dashboard Update (Recommended)
1. Open the **Admin Dashboard** (`http://mirrordash.local/admin`).
2. Go to the **Updates** tab.
3. Click **Update Core** to trigger the update. The system will download the new package from PyPI, stage it in the offline virtual environment (`venv_next`), commit the atomic A/B swap, and automatically restart.

#### Method B: SSH Command Line Update (Failsafe/Manual)
1. Access the device over SSH.
2. Manually invoke `uv` to update the active virtual environment:
   ```bash
   sudo -u pi HOME=/home/pi /home/pi/.local/bin/uv pip install --python /storage/mirrordash/venv --upgrade mirrordash
   ```
3. Restart the background service:
   ```bash
   sudo reboot
   ```

### 2. Deploying a System OS Update (Destructive)

To update the OS configuration on active devices, you must flash the new image. Since this overwrites all SD card contents, follow this backup-and-restore protocol:

1. **Back up Configuration**:
   - Navigate to the **Backup** tab in the existing Admin Dashboard.
   - Click **Create Backup** to download the `mirrordash_backup.zip` file. This contains all layouts, timezones, Wi-Fi credentials, and settings.
2. **Flash the SD Card**:
   - Flash the new `mirrordash-os-vX.Y.Z.img.gz` to the SD card using **Raspberry Pi Imager** or **BalenaEtcher**.
3. **Provision Wi-Fi (Captive Portal)**:
   - Insert the card and power on the Pi. The system will enter **Failsafe Captive Portal** mode within 30 seconds.
   - Connect to the **`MirrorDash-Setup`** hotspot using password **`mirrordash`**.
   - Navigate to `http://10.42.0.1/`, select your home network SSID, enter your password, and click **Connect & Reboot**.
4. **Restore Configuration**:
   - Once the mirror restarts, open the **Admin Dashboard** (`http://mirrordash.local/admin`).
   - Go to the **Backup** tab, upload the backup `.zip` file, and restore it. The system will automatically restore your configuration and reboot to resume normal operation.

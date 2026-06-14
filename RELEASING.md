# MirrorDash Release & Deployment Process

This document outlines the release, testing, and deployment workflows for MirrorDash.

> [!IMPORTANT]
> **Core App Release vs. System OS Release: The Crucial Distinction**
>
> | Property | Core Application (`mirrordash`) | System OS Image (`mirrordash-os`) |
> | :--- | :--- | :--- |
> | **What it is** | The Python application running the FastAPI server and Admin dashboard. | The underlying operating system configuration, drivers, Wayland compositor, and system packages. |
> | **When to release** | Whenever new features, layout changes, module upgrades, or Python bug fixes are merged. | Only when system packages (e.g. `nginx`, `plymouth`, `labwc`), hardware configurations, or network fallback scripts are modified. |
> | **Release target** | Published to PyPI via automated GitHub Actions. | Compiled locally in QEMU and uploaded directly to GitHub Releases as a compressed `.img.gz` asset. |
> | **Deployment method** | **Non-Destructive**: Triggered via the Admin Dashboard's "Updates" tab (uses A/B `venv` partition updates). | **Destructive**: Requires flashing the SD card. Backup settings first, flash, configure Wi-Fi, and restore settings. |
> | **Risk level** | **Low**: Handled by the A/B virtual environment update system with automatic rollback to `venv_old` or Safe Mode. | **High**: Overwrites all card data. System must be re-provisioned via the Wi-Fi Captive Portal on first boot. |

## Table of Contents

- [Release Guidelines](#release-guidelines)
- [Step-by-Step Release Flow](#step-by-step-release-flow)
- [Architecture & Infrastructure Behind Releases](#architecture--infrastructure-behind-releases)
- [OS Image Release & Testing (Manual)](#os-image-release--testing-manual)
- [Client Update & Deployment Procedures](#client-update--deployment-procedures)

## Release Guidelines

- **Only Release Stable Code**: Ensure all tests pass successfully before releasing.
- **Strict SemVer**: Follow [Semantic Versioning](https://semver.org/). Bumps are:
  - `patch` (e.g. `0.2.1` -> `0.2.2`) for backward-compatible bug fixes.
  - `minor` (e.g. `0.2.x` -> `0.3.0`) for new, backward-compatible features.
  - `major` (e.g. `0.x.x` -> `1.0.0`) for API-breaking changes.
- **Do Not Manually Tag Locally**: Let the GitHub Release interface create the git tag. This ensures that the GitHub Release, git tag, and PyPI package version are always perfectly aligned.
- **Release Tag Naming Conventions**:
  * **Core App Releases**: Tagged as `vX.Y.Z` (e.g., `v0.2.4`). Triggers the GitHub Action to build and publish to PyPI.
  * **OS Image Releases**: Tagged as `vX.Y.Z-osN` (e.g., `v0.2.4-os1`). Bypasses PyPI publishing, letting you attach the built `.img.gz` asset directly to the GitHub release page.

| Tag | Target | Triggers PyPI? | Release Asset |
| :--- | :--- | :--- | :--- |
| `v0.2.4` | Core Python Application | Yes | Python package on PyPI |
| `v0.2.4-os1` | First OS Image for `0.2.4` | No | `mirrordash-final.img.gz` |
| `v0.2.4-os2` | Second OS Image for `0.2.4` | No | Updated `mirrordash-final.img.gz` |

---

## Step-by-Step Release Flow

### 1. Pre-Release Checklist
1. Ensure you are on the `master` branch and have pulled the latest changes:
   ```bash
   git checkout master
   git pull origin master
   ```
2. Run the test suite to verify everything is passing:
   ```bash
   .venv/bin/pytest
   ```
3. Update the version number in [pyproject.toml](file:///home/menturan/repos/mymagicmirror/pyproject.toml):
   ```toml
   [project]
   version = "X.Y.Z" # Replace X.Y.Z with your new version (e.g. 0.2.1)
   ```
4. Update the [CHANGELOG.md](file:///home/menturan/repos/mymagicmirror/CHANGELOG.md):
   * Move the changes under `[Unreleased]` to a new version section matching your release version (e.g., `## [0.2.1] - 2026-06-12`). Use `YYYY-MM-DD` format for the date.
   * Update the comparison links at the bottom of the file (e.g. add `[0.2.1]` and update `[Unreleased]`).

### 2. Commit and Push
Commit the version bump and changelog update, then push to GitHub:
```bash
git add pyproject.toml CHANGELOG.md
git commit --no-gpg-sign -m "chore: bump version to X.Y.Z"
git push origin master
```

### 3. Create the GitHub Release
1. Navigate to the `MirrorDash` repository on GitHub.
2. On the right-hand sidebar under **Releases**, click **Draft a new release**.
3. Click **Choose a tag**, type the new version prefixed with a `v` (e.g., `v0.2.1`), and click **Create new tag on publish**.
4. Set the **Release title** to match the tag (e.g., `v0.2.1`).
5. Click **Generate release notes**. This automatically compiles the list of merged pull requests, commits, and contributors.
6. Click **Publish release**.

### 4. Verification
Once the release is published, the GitHub Actions runner will boot automatically:
1. Go to the **Actions** tab on your GitHub repository.
2. Locate and monitor the running **Publish to PyPI** workflow.
3. Once completed, verify that the new package version has been published successfully on [PyPI](https://pypi.org/project/mirrordash/).

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

---

## OS Image Release & Testing (Manual)

Before automating the OS image compilation and hosting, perform manual builds and sanity-testing using the workflow below.

### 1. Build the OS Image
Run the automated image builder script on a Linux workstation with root privileges:
```bash
sudo bash scripts/build_image.sh
```
This generates a compressed production image file: `build_workspace/mirrordash-final.img.gz`.

### 2. Flashing the Image
You can write the compressed image directly to an SD card (or USB drive) without extracting it first:
* **Raspberry Pi Imager**:
  1. Click **Choose OS**.
  2. Scroll to the bottom and select **Use custom**.
  3. Select your `mirrordash-final.img.gz` file.
  4. Select your target storage drive and click **Next**.
* **BalenaEtcher**:
  1. Select **Flash from file** and choose `mirrordash-final.img.gz`.
  2. Select your target storage drive and click **Flash!**.

### 3. Verification & Testing Checklist
To thoroughly test the image before distribution:
1. **Boot Splash & Plymouth**: Insert the SD card into a Raspberry Pi and power it on. Ensure that the customized Plymouth boot splash screen appears and that no systemd status messages, log lines, or login prompts flicker onto the screen.
2. **Invisible Mouse Cursor**: Once the Wayland desktop (`labwc`) starts, connect a USB mouse and move it around. Verify that the cursor remains completely invisible on the kiosk display.
3. **Failsafe Captive Portal**:
   * Power on the Pi in an environment *without* an active/configured Ethernet connection or saved WiFi network.
   * Verify that within 30 seconds, the device activates the `MirrorDash Setup` fallback Access Point.
   * Connect to the AP from a phone or computer, visit the captive portal page (`http://10.42.0.1/wifi-setup`), enter local WiFi credentials, and submit.
   * Verify that the system remounts successfully, saves the profiles, and reboots.
4. **Dashboard & Mirror Load**: Verify that the mirror loads the kiosk web page (`index.html`) correctly, displays the initial loading skeletons, transitions into active widgets when WebSocket communication is established, and runs stably.
5. **Admin Access**: Ensure the admin dashboard (e.g. `http://mirrordash.local/admin` or `http://<IP>/admin`) is accessible and requires the correct API key.

### 4. Direct Distribution
To share the compiled OS image:
1. Generate a SHA256 checksum:
   ```bash
   sha256sum mirrordash-final.img.gz > mirrordash-final.img.gz.sha256
   ```
2. Upload `mirrordash-final.img.gz` and `mirrordash-final.img.gz.sha256` directly as assets in your GitHub Release page.

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
   sudo -u pi HOME=/home/pi /home/pi/.local/bin/uv pip install --python /storage/mirrordash/venv_a --upgrade mirrordash
   ```
3. Restart the background service:
   ```bash
   sudo reboot
   ```

### 2. Deploying a System OS Update (Destructive)

To update the OS configuration on active devices, you must flash the new image. Since this overwrites all SD card contents, follow this backup-and-restore protocol:

1. **Back up Configuration**:
   * Navigate to the **Backup** tab in the existing Admin Dashboard.
   * Click **Create Backup** to download the `mirrordash_backup.zip` file. This contains all layouts, timezones, Wi-Fi credentials, and settings.
2. **Flash the SD Card**:
   * Flash the new `mirrordash-final.img.gz` to the SD card using **Raspberry Pi Imager** or **BalenaEtcher**.
3. **Provision Wi-Fi (Captive Portal)**:
   * Insert the card and power on the Pi. The system will enter **Failsafe Captive Portal** mode within 30 seconds.
   * Connect to the **`MirrorDash-Setup`** hotspot using password **`mirrordash`**.
   * Navigate to `http://10.42.0.1/`, select your home network SSID, enter your password, and click **Connect & Reboot**.
4. **Restore Configuration**:
   * Once the mirror restarts, open the **Admin Dashboard** (`http://mirrordash.local/admin`).
   * Go to the **Backup** tab, upload the backup `.zip` file, and restore it. The system will automatically restore your configuration and reboot to resume normal operation.


# Releasing MirrorDash

There are two kinds of releases:

| | App (`mirrordash` on PyPI) | OS image (SD card) |
| :--- | :--- | :--- |
| **What** | The Python app: server, mirror page, admin page. | Raspberry Pi OS with MirrorDash set up: system packages, kiosk, Wi-Fi fallback, read-only filesystem. |
| **When** | Any change mirror owners would notice in the app. | Only when something outside the app changes (`scripts/setup_appliance.sh`, `scripts/build_image.sh`, system configuration). |
| **Tag** | `vX.Y.Z` (test versions `vX.Y.ZrcN`) | `vX.Y.Z-osN`: the image contains app `X.Y.Z` |
| **Reaches mirrors** | **Settings → Check for Updates** in the admin page (A/B venv with automatic rollback). | Flashing the SD card. |

Every real release is made from something you tested first, all with one script:

```bash
python3 scripts/release.py            # asks what to do
python3 scripts/release.py --dry-run  # shows every change and command without making it
```

| Choice | What it does |
| :--- | :--- |
| **1. Test version of the app** | Sets the version to `X.Y.ZrcN`, pushes and publishes it on GitHub and PyPI as a pre-release. Only mirrors with **Settings → Test versions** turned on are offered it. The CHANGELOG is left alone. |
| **2. Release the app** | Sets the version to `X.Y.Z`, moves the `[Unreleased]` entries into `[X.Y.Z]` (the `### OS image` part stays), pushes and publishes `vX.Y.Z` for every mirror, with the CHANGELOG section as release notes. |
| **3. Build a test OS image** | Runs the *Build OS Image* workflow on master (about 30 minutes) and downloads the image to `build_workspace/test-image/`. |
| **4. Release the tested image** | Moves the CHANGELOG entries into `[X.Y.Z-osN]`, pushes and publishes `vX.Y.Z-osN` with **the file you tested**. Nothing is rebuilt. |

Before it changes anything, the script stops if you're not on a clean `master` equal to `origin/master`, the tag already exists, `gh` isn't logged in, or the tests fail. It shows what it is about to do and asks first.

## Before your first release

- Install and log in to the GitHub CLI: `sudo apt install gh && gh auth login`.
- The dev venv: `uv venv && uv pip install -e ".[dev]"` (the script runs `.venv/bin/pytest`).
- PyPI publishes through trusted publishing (OIDC): `.github/workflows/publish.yml` runs in the `pypi` environment, which is registered as a Trusted Publisher for `mirrordash` on pypi.org. No tokens are stored anywhere. The workflow refuses to publish when the tag isn't `v` + the version in `pyproject.toml`.

## Writing the CHANGELOG

`CHANGELOG.md` is for mirror owners, not developers. Add an entry under `## [Unreleased]` in the same commit as any change a user would notice, in plain language: what changed for them, not how. Use `### Added`, `### Changed` and `### Fixed`; put changes that only reach users through a new SD card image under `### OS image`. Leave out internal work (refactors, tests, docs, CI). The script refuses to release when there is nothing under `[Unreleased]`. To see what changed since the last release: `git log --oneline vX.Y.Z..HEAD`.

Versions follow [SemVer](https://semver.org/): `patch` for fixes, `minor` for new features, `major` for breaking changes.

## Releasing the app

1. Choose **1** for a test version. On your test mirror, turn on **Settings → Test versions**, click **Check for Updates** and install it.
2. Use it. If something is wrong, fix it on master and choose **1** again (`rc2`, …).
3. Choose **2**: it suggests the version without `rc`. Every mirror is now offered the update.

## Releasing an OS image

The app version in the image is whatever master has, so release the app first (choose **2**) if it changed.

1. Choose **3**, then flash `build_workspace/test-image/<run>/mirrordash-os-vX.Y.Z.img.xz` with Raspberry Pi Imager and go through the checklist:
   1. **Boot**: the MirrorDash splash shows, no system messages or login prompt, no mouse cursor.
   2. **Wi-Fi setup**: with no network, the `MirrorDash-Setup` hotspot appears within 30 seconds and the mirror shows its password and a QR code. Scanning the code joins the phone, which opens the setup page (otherwise go to `http://mirrordash.setup/wifi-setup`); pick a network, and the mirror connects and restarts. Unplug the network again: after the restart the same password is shown.
   3. **Mirror**: the page loads, placeholders turn into modules, the clock ticks.
   4. **Admin**: `http://mirrordash.local/admin` asks for a password to be set, then opens.
2. Choose **4** to release that same image. If master changed since the build, it lists what the image doesn't contain and asks before going on.

If the build fails (runner or network trouble), just choose **3** again.

## Updating mirrors

**App** (keeps everything): **Settings → Check for Updates** in the admin page. The new version is installed into the spare venv and the mirror restarts; if it doesn't come up within 10 seconds it boots the previous version again. Over SSH, as a fallback:

```bash
sudo -u pi HOME=/home/pi /home/pi/.local/bin/uv pip install --python /storage/mirrordash/venv --upgrade mirrordash
sudo reboot
```

**OS image** (erases the card):

1. **Backup → Create backup** in the admin page and download the `.mirror` file. It doesn't contain the admin password or Wi-Fi passwords.
2. Flash the new image.
3. Connect the mirror to Wi-Fi through the `MirrorDash-Setup` hotspot: scan the QR code on the mirror, as in the checklist above.
4. Set an admin password, then **Backup → Restore** with the `.mirror` file.

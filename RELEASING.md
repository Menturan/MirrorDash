# Releasing MirrorDash

There are two kinds of releases:

| | App | OS image |
| :--- | :--- | :--- |
| **What** | The Python app (`mirrordash` on PyPI): server, mirror page, admin page. | A Raspberry Pi OS SD-card image with MirrorDash set up: system packages, kiosk, Wi-Fi setup, read-only system. |
| **Version** | `vX.Y.Z` (test versions `vX.Y.ZrcN`) | `vX.Y.Z-osN`: the image with app `X.Y.Z` |
| **Reaches mirrors** | **Settings → Check for Updates** in the admin page. Everything is kept. | Flashing the SD card. |

Both are made with one script, `scripts/release.py`, and every real release is something you tested first.

## Which release do I need?

| You changed… | Do this |
| :--- | :--- |
| The app (anything in `mirrordash_core/`) | [Release the app](#release-the-app): choose **1**, test it on a mirror, then **2**. |
| The OS (`scripts/setup_appliance.sh`, `scripts/build_image.sh`, system settings) | [Release an OS image](#release-an-os-image): choose **3**, test it on an SD card, then **4**. |
| Both | First the app (**1** → **2**), then the image (**3** → **4**). |

The SDK (`mirrordash-sdk`) and modules are released from their own repositories: the SDK with its own
`scripts/release.py`, modules with a GitHub Release (see the SDK's MODULE_GUIDE).

## Before your first release (once)

1. Install the GitHub CLI (`sudo apt install gh`, or see https://cli.github.com) and log in: `gh auth login`.
2. Set up the development environment in the repository: `uv sync --extra dev`. The script runs the tests
   with it.
3. Try the script without changing anything: `python3 scripts/release.py --dry-run`. It shows every
   change and command instead of making it.

Publishing to PyPI needs no password: GitHub Actions does it (trusted publishing, already set up).

## Release the app

**1. Make a test version.** Run the script and choose **1**:

```
$ python3 scripts/release.py
What do you want to do?
  1. Test version of the app (only test mirrors get it)
  2. Release the app for everyone
  3. Build a test OS image to flash
  4. Release the tested OS image
Choice: 1

Current version: 0.4.0. Test version:
  1. 0.4.1rc1
  2. 0.5.0rc1
  3. 1.0.0rc1
  4. Other version
Choice: 2
  Running the tests...

This sets the version to 0.5.0rc1, pushes to master and publishes v0.5.0rc1 on GitHub and PyPI as a test version (only mirrors with Test versions on get it).
Go ahead? [y/N] y
```

Pick `X.Y.Z+1` (`0.4.1rc1`) for fixes and `X.Y+1.0` (`0.5.0rc1`) for new features. Before it changes
anything, the script checks that you're on `master`, that it's committed and the same as GitHub, and
that the tests pass. Then it pushes, publishes, and waits for GitHub Actions to put the version on PyPI
(a few minutes). It ends with `v0.5.0rc1 is on PyPI.`

**2. Test it.** On your test mirror's admin page: **Settings → Test versions** on, then **Check for
Updates**, and install. Only mirrors with Test versions on are offered it. If something is wrong, fix it
on master and choose **1** again: it suggests the next test version (`0.5.0rc2`).

**3. Release it for everyone.** Run the script again and choose **2**. It suggests the version without
`rc` (`0.5.0`), moves the `[Unreleased]` entries in `CHANGELOG.md` into a `[0.5.0]` section (the
`### OS image` part stays for the next image), and publishes it with that section as release notes.
Every mirror is now offered the update.

## Release an OS image

The image contains the app version on master, so release the app first if it changed.

**1. Build a test image.** Run the script and choose **3**. GitHub Actions runs the tests and builds the
image (about 30 minutes); the script waits and downloads it to
`build_workspace/test-image/<run>/mirrordash-os-vX.Y.Z.img.xz`. The image is about 750 MB: the script
shows how far it has come, the speed and the time left, and asks to try again if the download breaks
(without building again).

**2. Test it.** Flash it with Raspberry Pi Imager and go through this checklist:

1. **Boot**: the MirrorDash start screen shows, no system messages or login prompt, no mouse cursor.
2. **Wi-Fi setup**: with no network, the `MirrorDash-Setup` hotspot appears within about a minute and
   the mirror shows its password and a QR code. Scanning the code joins the phone, which opens the setup
   page (otherwise go to `http://mirrordash.setup/wifi-setup`); pick a network, and the mirror connects
   and restarts. Unplug the network again: after the restart the same password is shown.
3. **Mirror**: the page loads, the "Loading" placeholders turn into modules, the clock ticks.
4. **Admin**: `http://mirrordash.local/admin` asks for a password to be set, then opens.

**3. Release that same image.** Run the script and choose **4**. It publishes `vX.Y.Z-osN` with the
file you tested; nothing is rebuilt. If master changed since the build, it lists what the image doesn't
contain and asks before going on.

## Writing the CHANGELOG

`CHANGELOG.md` is for mirror owners, not developers. Add an entry under `## [Unreleased]` in the same
commit as any change a user would notice, in plain language: what changed for them, not how.

- Use `### Added`, `### Changed` and `### Fixed`.
- Put changes that only reach users through a new SD card image under `### OS image`.
- Leave out internal work (refactors, tests, docs, CI).

The script refuses to release when there is nothing under `[Unreleased]`. To see what changed since the
last release: `git log --oneline vX.Y.Z..HEAD`.

## Updating mirrors

**App** (keeps everything): **Settings → Check for Updates** in the admin page. The new version is
installed next to the old one and the mirror restarts. If the new version crashes within its first 10
seconds, the mirror goes back to the old one by itself. Over SSH, as a fallback:

```bash
sudo -u pi HOME=/home/pi /home/pi/.local/bin/uv pip install --python /storage/mirrordash/venv --upgrade mirrordash
sudo reboot
```

**OS image** (erases the card):

1. **Backup → Create backup** in the admin page and download the `.mirror` file. It doesn't contain the
   admin password or Wi-Fi passwords.
2. Flash the new image.
3. Connect the mirror to Wi-Fi through the `MirrorDash-Setup` hotspot: scan the QR code on the mirror,
   as in the checklist above.
4. Set an admin password, then **Backup → Restore** with the `.mirror` file.

---

## Reference

### How it fits together

Everything is committed straight to `master`: there are no branches, pull requests or Dependabot (it
works through pull requests). GitHub Actions runs the tests on every push instead.

```
git push (master)  ──► Tests
release.py 1 or 2  ──► tag vX.Y.Z / vX.Y.ZrcN + GitHub release ──► Publish to PyPI: Tests → build → publish ──► mirrors update
release.py 3       ──► Build OS Image: Tests → build ──► workflow artifact (kept 14 days) ──► you flash and test it
release.py 4       ──► that same artifact becomes the vX.Y.Z-osN GitHub release (nothing is rebuilt)
```

| Workflow (`.github/workflows/`) | Started by | What it does | Result |
| :--- | :--- | :--- | :--- |
| `test.yml` (*Tests*) | Every push to `master`; also called by the two below. | `uv sync --frozen`, `pytest`, and a syntax and `shellcheck` check of `scripts/*.sh`. | Green or red in the Actions tab. |
| `publish.yml` (*Publish to PyPI*) | A published GitHub release whose tag starts with `v` and has no `-os`. | Tests, checks the tag is `v` + the `pyproject.toml` version, builds, then publishes from a separate job. | `mirrordash` on PyPI. |
| `build-os-image.yml` (*Build OS Image*) | `release.py` option 3 (or *Run workflow* in the Actions tab). | Tests, then `scripts/build_image.sh` on an ARM runner (about 30 min). One build at a time. | Artifact `mirrordash-os-image`: `.img.xz` + `.sha256`. |

No secrets are stored in GitHub. PyPI trusts the `publish` job in the `pypi` environment (trusted
publishing, OIDC); renaming `publish.yml` or the environment breaks that until it is changed on pypi.org
too.

### What is pinned, and how to bump it

Pinned so that the same commit always builds the same image and runs the same tools. Bump them on
purpose, one at a time, and build a test image (option 3) afterwards.

| What | Where | Next value |
| :--- | :--- | :--- |
| Raspberry Pi OS base image and its SHA-256 | `TARGET_URL`, `TARGET_SHA256` in `scripts/build_image.sh` | `curl -sLI -o /dev/null -w '%{url_effective}' https://downloads.raspberrypi.org/raspios_lite_arm64_latest`; the checksum is that URL + `.sha256`. |
| Python dependencies (app and image) | `uv.lock` | `uv lock --upgrade`, then run the tests. The image installs these exact versions. |
| Clock module in the image | `CLOCK_REF` in `scripts/setup_appliance.sh` | A tag in `Menturan/mirrordash-clock` (`git ls-remote --tags https://github.com/Menturan/mirrordash-clock.git`). |
| uv in the image | `UV_VERSION` in `scripts/setup_appliance.sh` | The uv release you use locally. |
| PiShrink | Commit in the URL in `build-os-image.yml` | The latest commit in `Drewsif/PiShrink`. |
| Actions from outside GitHub | Commit SHA after `uses:`, version in the comment | `git ls-remote --tags https://github.com/<owner>/<action>.git`, take the commit of the newest tag. GitHub's own `actions/*` use version tags. |

### When something fails

- **The script stopped**: read its last line, `Stopped: …`. It says what's wrong and what to do (for
  example "switch to master first", "the tests failed"). If it stopped before asking `Go ahead?`,
  nothing was changed; fix it and run the script again.
- **Tests are red after a push**: open the run in the Actions tab (or `gh run view --log-failed`), fix
  it on `master`, push. Nothing reaches mirrors from a push alone.
- **Publish to PyPI failed**: the tag and the GitHub release already exist, but PyPI doesn't have the
  version. Fix the cause and use *Re-run jobs* on that run; don't make a new tag. If the code itself was
  wrong, release the next version instead.
- **Build OS Image failed**: usually the runner or a download. Choose 3 again. A *checksum* error means
  the base image download was broken or changed; it is deleted, so another try downloads it again.

---
name: release
description: Make a MirrorDash release - a test or real version of the app, a test or real OS image (scripts/release.py), a module (mirrordash-sdk release) or the SDK itself. Use when asked to release, publish, tag a version, or make a test version or image.
---

# Make a release

`RELEASING.md` is the full reference; this is how to drive it. Releases reach real mirrors: always say
exactly what will be released (kind, version, what's in it) and get the user's yes before the real run.
Do a `--dry-run` first. Never go from a test version to the real release (1 → 2, 3 → 4) until the user
says they tested it and it's good.

## Which release?

| Changed | Release |
| :--- | :--- |
| `mirrordash_core/` | App: **1** (test version), user tests, then **2** |
| OS (`scripts/configure_image.sh`, `scripts/build_image.sh`, `scripts/image/rootfs/`) | App first if it changed, then OS: **3**, user tests, then **4** |
| A module in `../mirrordash-modules/<module>` | Module release (below) |
| `../mirrordash-sdk` | SDK release (below) |

Not sure what changed: `git log --oneline $(git describe --tags --abbrev=0)..HEAD`.

## App and OS image: `scripts/release.py`

Run it from the main checkout on `master`, not from a worktree: it stops unless `master` is clean and equal to
`origin/master`. Before a release:

- `## [Unreleased]` in `CHANGELOG.md` has plain-language entries for what users notice (`### OS image` for
  image-only changes); the script refuses an empty one. Write the missing ones, commit and push first.
- `gh auth status` works, and `.venv` exists (`uv sync --extra dev --inexact`).

The script is interactive; feed the answers on stdin, one per line:

1. The menu: `1` test app, `2` release app, `3` OS test version, `4` release the tested OS.
2. For 1 and 2, the version menu (numbers as printed; read them in the dry run):
   - test (1): `[next rc of the current rc,]` patch rc1, minor rc1, major rc1, other.
   - release (2): from an rc only that version without rc; otherwise patch, minor, major, other.
   Patch for fixes, minor for new features.
3. `y` for `Go ahead?`.
4. Option 3 only, at the end: `Download the image too?`, `n` unless the user wants to flash a card (`y` downloads
   only the image and its checksum, from the release, with `download_image()`).

```bash
printf '1\n2\ny\n' | python3 scripts/release.py --dry-run   # check the printed version and commands
printf '1\n2\ny\n' | python3 scripts/release.py             # after the user's yes
```

The real run waits for GitHub Actions: PyPI for 1 and 2 (a few minutes), the image build for 3 (about 30
minutes). Run it in the background and report when it ends. It ends with `vX.Y.Z is on PyPI.` (1, 2),
`... is published as a test version` (3) or `... is released` (4); anything else ends in `Stopped: <reason>`,
which says what to fix (see "When something fails" in `RELEASING.md`).

Afterwards tell the user how to test it: on the test mirror **Settings → Test versions** on, then
**Check for Updates** and install (for 3, the system update, or flash the downloaded image and go through the
checklist in `RELEASING.md`). A bad OS test version: `gh release delete vX.Y.Z-osN` (keep the tag), fix, 3 again.

## A module

```bash
SDK=$(dirname "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")")/mirrordash-sdk
cd ../mirrordash-modules/<module>   # from the main checkout; clean, committed, pushed
git log --oneline $(git describe --tags --abbrev=0)..HEAD           # what's in it
uv run --project "$SDK" mirrordash-sdk release X.Y.Z --dry-run
echo y | uv run --project "$SDK" mirrordash-sdk release X.Y.Z       # after the user's yes
```

It validates, runs the module's tests, sets the version, tags `vX.Y.Z`, pushes and makes the GitHub Release
(notes generated from the commits). Mirrors offer it on the module's card. A new module starts at `1.0.0`.

For `mirrordash-clock`: the OS image pins it in `CLOCK_REF` in `scripts/configure_image.sh`; offer to move it
to the new tag (it reaches mirrors with the next OS image).

## The SDK

From `../mirrordash-sdk` on a clean, pushed `master`:

```bash
python3 scripts/release.py X.Y.Z --dry-run
echo y | python3 scripts/release.py X.Y.Z   # after the user's yes; GitHub Actions publishes it to PyPI
```

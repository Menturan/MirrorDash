#!/usr/bin/env python3
"""MirrorDash release wizard: every real release builds on something you tested first.

    python3 scripts/release.py            # asks what to do
    python3 scripts/release.py --dry-run  # shows every change and git/gh command instead of running it

  1. Test version of the app  -> X.Y.ZrcN on PyPI as a pre-release; only mirrors with "Test versions" on get it
  2. Release the app          -> CHANGELOG + vX.Y.Z on GitHub and PyPI, for every mirror
  3. Build a test OS image    -> CI builds it from master, downloaded to build_workspace/test-image/ to flash
  4. Release the tested image -> CHANGELOG + vX.Y.Z-osN with that same file (nothing is rebuilt)

Needs the GitHub CLI, logged in (`gh auth login`), and the dev venv (.venv, `uv sync --extra dev`) for the tests.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mirrordash_core.config import version_key  # noqa: E402  (stdlib-only module)

TEST_IMAGE_DIR = ROOT / "build_workspace" / "test-image"
DRY = "--dry-run" in sys.argv


def die(msg):
    print(f"\n  Stopped: {msg}")
    sys.exit(1)


def run(*cmd, change=False, check=True):
    """Run a command in the repo and return its output. Commands that change something only print in a dry run."""
    if change and DRY:
        print(f"  [dry run] {' '.join(cmd)}")
        return ""
    if change:
        print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=ROOT, capture_output=not change, text=True)
    if check and result.returncode != 0:
        die(f"`{' '.join(cmd)}` failed.\n{result.stderr or ''}".rstrip())
    return (result.stdout or "").strip()


def ask(prompt):
    try:
        return input(prompt).strip()
    except (KeyboardInterrupt, EOFError):
        die("aborted.")


def choose(title, options):
    print(f"\n{title}")
    for i, label in enumerate(options, 1):
        print(f"  {i}. {label}")
    while True:
        answer = ask("Choice: ")
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1


def confirm(summary):
    print(f"\n{summary}")
    if ask("Go ahead? [y/N] ").lower() != "y":
        die("nothing was changed.")


# --- Versions and CHANGELOG ---------------------------------------------------------------

def current_version():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def set_version(version):
    path = ROOT / "pyproject.toml"
    if DRY:
        print(f"  [dry run] set version = \"{version}\" in pyproject.toml")
        return
    content, count = re.subn(r'^version = ".*"', f'version = "{version}"', path.read_text(), count=1, flags=re.M)
    if not count:
        die("no version line in pyproject.toml.")
    path.write_text(content)


def release_changelog(version, intro="", keep_os_image=False, only_os_image=False, path="CHANGELOG.md"):
    """Move the hand-written [Unreleased] entries into a new [version] section.

    keep_os_image leaves the "### OS image" subsection under [Unreleased], since an app
    release doesn't change the OS image. only_os_image is the other way round, for an image
    release: only the OS image entries move; app entries wait for the next app release.
    Exits if there is nothing to release.
    """
    with open(path) as f:
        content = f.read()
    head, marker, rest = content.partition("## [Unreleased]\n")
    if not marker:
        print(f"  ERROR: No '## [Unreleased]' section in {path}")
        sys.exit(1)
    if re.search(rf"^## \[{re.escape(version)}\]", content, re.MULTILINE):
        print(f"  ERROR: {path} already has a [{version}] section")
        sys.exit(1)

    next_section = re.search(r"^## \[", rest, re.MULTILINE)
    body, tail = (rest[:next_section.start()], rest[next_section.start():]) if next_section else (rest, "")
    kept = ""
    if keep_os_image and "### OS image" in body:
        body, kept = body.split("### OS image", 1)
        kept = "### OS image" + kept
    elif only_os_image:
        kept, _, body = body.partition("### OS image")
    if not body.strip():
        print(f"  ERROR: Nothing to release under [Unreleased] in {path}.")
        print("  Describe the changes there first, in plain language for mirror owners.")
        sys.exit(1)

    section = f"## [{version}] - {date.today().isoformat()}\n\n" + (f"{intro}\n\n" if intro else "") + body.strip()
    unreleased = marker + "\n" + (kept.strip() + "\n\n" if kept.strip() else "")
    with open(path, "w") as f:
        f.write(head + unreleased + section + "\n\n" + tail)
    print(f"  Moved [Unreleased] entries to [{version}] in {path}")


def move_changelog(version, **kwargs):
    if DRY:
        print(f"  [dry run] move CHANGELOG [Unreleased] entries to [{version}]")
        return
    release_changelog(version, path=str(ROOT / "CHANGELOG.md"), **kwargs)


def changelog_section(version):
    """The text of a released CHANGELOG section, used as the GitHub release notes."""
    text = (ROOT / "CHANGELOG.md").read_text()
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else "(see CHANGELOG.md)"


def unreleased_notes():
    text = (ROOT / "CHANGELOG.md").read_text()
    m = re.search(r"^## \[Unreleased\]\n(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def pick_version(prompt_title, candidates, pattern, current):
    """Offer suggested versions plus a custom one; refuse anything not newer than the current version."""
    index = choose(prompt_title, [*candidates, "Other version"])
    version = candidates[index] if index < len(candidates) else ask("Version: ")
    if not re.fullmatch(pattern, version):
        die(f"{version} isn't a valid version here.")
    if version_key(version) <= version_key(current):
        die(f"{version} isn't newer than {current}.")
    return version


def next_os_version(core_version):
    """The next free vX.Y.Z-osN: after every released one (CHANGELOG) and every test version (tags), so
    a test version that wasn't good is never reused."""
    builds = re.findall(rf"^## \[{re.escape(core_version)}-os(\d+)\]", (ROOT / "CHANGELOG.md").read_text(), re.M)
    tags = run("git", "tag", "--list", f"v{core_version}-os*").split()
    tagged = [m[1] for t in tags if (m := re.fullmatch(rf"v{re.escape(core_version)}-os(\d+)", t))]
    return f"{core_version}-os{max(map(int, [*builds, *tagged]), default=0) + 1}"


def os_image_notes():
    """The ### OS image entries under [Unreleased]: what a test version of the OS brings."""
    notes = unreleased_notes()
    return notes.split("### OS image", 1)[1].split("\n### ", 1)[0].strip() if "### OS image" in notes else ""


# --- Safety checks --------------------------------------------------------------------------

def check_ready(tag, tests=True, upgrade=False):
    """Stop unless this is a clean master equal to origin, gh works and the tag is free.
    upgrade: move uv.lock to the newest packages first, so the tests run on them."""
    if not shutil.which("gh"):
        die("the GitHub CLI isn't installed: `sudo apt install gh`, then `gh auth login`.")
    if subprocess.run(["gh", "auth", "status"], cwd=ROOT, capture_output=True).returncode != 0:
        die("the GitHub CLI isn't logged in: run `gh auth login`.")
    if run("git", "branch", "--show-current") != "master":
        die("switch to master first.")
    if run("git", "status", "--porcelain", "--untracked-files=no"):
        die("there are uncommitted changes; commit or stash them first.")
    run("git", "fetch", "--quiet", "--tags", "origin")
    if run("git", "rev-parse", "HEAD") != run("git", "rev-parse", "origin/master"):
        die("master isn't the same as origin/master; pull or push first.")
    if tag and run("git", "rev-parse", "--quiet", "--verify", f"refs/tags/{tag}", check=False):
        die(f"the tag {tag} already exists.")
    if upgrade:
        run("uv", "lock", "--upgrade", change=True)
        run("uv", "sync", "--extra", "dev", "--inexact", change=True)
    if tests:
        print("\n  Running the tests...")
        # python -m pytest, not .venv/bin/pytest: the scripts' shebangs keep the path the venv
        # was made at, so they break when the repo moves.
        python = ROOT / ".venv" / "bin" / "python"
        if not python.exists():
            die("no dev venv (.venv); make one with `uv sync --extra dev`.")
        if subprocess.run([str(python), "-m", "pytest", "-q"], cwd=ROOT).returncode != 0:
            die("the tests failed (if pytest is missing or the repo moved, run `uv sync --extra dev`).")


def commit_and_push(message, *paths):
    run("git", "add", *paths, change=True)
    run("git", "commit", "--no-gpg-sign", "-m", message, change=True)
    run("git", "push", "origin", "master", change=True)


def find_run(workflow, sha, since):
    """The id of the workflow run for this commit started after `since`, waiting up to a minute for it to show up."""
    for _ in range(12):
        runs = json.loads(run("gh", "run", "list", "--workflow", workflow, "--commit", sha,
                              "--json", "databaseId,createdAt", "--limit", "5") or "[]")
        fresh = [r["databaseId"] for r in runs if r["createdAt"] >= since]
        if fresh:
            return str(fresh[0])
        time.sleep(5)
    die(f"no {workflow} run showed up; check the Actions tab on GitHub.")


def watch(workflow, sha, since):
    if DRY:
        print(f"  [dry run] wait for the {workflow} run on {sha[:7]}")
        return None
    run_id = find_run(workflow, sha, since)
    if subprocess.run(["gh", "run", "watch", run_id, "--exit-status"], cwd=ROOT).returncode != 0:
        die(f"the {workflow} run failed: gh run view {run_id} --log-failed")
    return run_id


def now():
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Downloading the test image -------------------------------------------------------------

def _clock(seconds):
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def fetch(url, path):
    """Download url to path: with aria2c when it's installed (16 connections, resumes a broken
    download on the next try), otherwise with download() below."""
    path = Path(path)
    if shutil.which("aria2c"):
        cmd = ["aria2c", "-x16", "-s16", "-k1M", "-c", "--file-allocation=none", "--console-log-level=warn",
               "--summary-interval=0", "--download-result=hide", "-d", str(path.parent), "-o", path.name, url]
        if subprocess.run(cmd).returncode != 0:
            raise OSError("aria2c failed")
    else:
        download(url, path)


def download(url, path, parts=16, chunk=1 << 20):
    """Download url to path, showing size, percent, speed and time left on one line.

    With several parts at once (HTTP Range) when the server allows it: GitHub's artifact storage is
    often slower per connection than the line. Otherwise one stream.
    ponytail: an interrupted download starts over; resuming would need to remember the finished ranges.
    """
    path = Path(path)
    probe = urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=0-0"}), timeout=60)
    ranged = probe.status == 206
    if ranged:
        total = int(probe.headers["Content-Range"].rsplit("/", 1)[1])
        probe.close()
    else:
        total = int(probe.headers.get("Content-Length") or 0)
    done, errors, lock, started = [0], [], threading.Lock(), time.monotonic()

    def copy(response, f):
        while data := response.read(chunk):
            f.write(data)
            with lock:
                done[0] += len(data)

    def fetch(start, end):
        try:
            request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
            with urllib.request.urlopen(request, timeout=60) as response, open(path, "r+b") as f:
                f.seek(start)
                copy(response, f)
        except Exception as e:  # reported after the other parts have finished
            errors.append(e)

    if ranged:
        with open(path, "wb") as f:
            f.truncate(total)
        step = -(-total // parts)
        workers = [threading.Thread(target=fetch, args=(i, min(i + step, total) - 1), daemon=True)
                   for i in range(0, total, step)]
    else:
        def single():
            try:
                with probe, open(path, "wb") as f:
                    copy(probe, f)
            except Exception as e:
                errors.append(e)
        workers = [threading.Thread(target=single, daemon=True)]
    for worker in workers:
        worker.start()

    def line():
        elapsed = max(time.monotonic() - started, 1e-6)
        speed = done[0] / elapsed
        text = f"  {done[0] / 1e6:,.0f} MB"
        if total:
            left = (total - done[0]) / speed if speed else 0
            text += f" / {total / 1e6:,.0f} MB   {100 * done[0] / total:3.0f} %"
        text += f"   {speed / 1e6:5.1f} MB/s"
        if total:
            text += f"   {_clock(left)} left"
        return text

    while any(w.is_alive() for w in workers):
        print(f"\r{line()}   ", end="", flush=True)
        time.sleep(0.5)
    elapsed = time.monotonic() - started
    print(f"\r  {done[0] / 1e6:,.0f} MB in {_clock(elapsed)} ({done[0] / 1e6 / max(elapsed, 1e-6):.1f} MB/s)"
          + " " * 30)
    if errors:
        raise errors[0]
    if total and done[0] != total:
        raise OSError(f"got {done[0]} of {total} bytes")


def download_image(tag, target):
    """Download a test version's image and its checksum from its GitHub release into target. Not the Actions
    artifact: that holds the OS update bundle too (twice the size), behind an address that ran out mid-download.
    The release's address stays the same, so a retry (or aria2c's resume) picks up where it broke."""
    assets = json.loads(run("gh", "release", "view", tag, "--json", "assets"))["assets"]
    wanted = sorted((a for a in assets if a["name"].endswith((".img.xz", ".img.xz.sha256"))), key=lambda a: a["size"])
    if len(wanted) != 2:
        die(f"the release {tag} has no image and checksum to download.")
    target.mkdir(parents=True, exist_ok=True)
    for asset in wanted:  # the checksum first: it's tiny
        print(f"\n  Downloading {asset['name']} ({asset['size'] / 1e6:,.0f} MB):")
        while True:  # a broken download only needs another try, not another 30-minute build
            try:
                fetch(asset["url"], target / asset["name"])
                break
            except Exception as e:
                if ask(f"\n  The download failed ({e}). Try again? [Y/n] ").lower() == "n":
                    die(f"no image downloaded. It stays on the release: gh release download {tag} -p '*.img.xz*'")


# --- The four steps -------------------------------------------------------------------------

def release_app(test):
    current = current_version()
    major, minor, patch = (int(x) for x in re.match(r"(\d+)\.(\d+)\.(\d+)", current).groups())
    rc = re.fullmatch(r"\d+\.\d+\.\d+rc(\d+)", current)
    bumps = [f"{major}.{minor}.{patch + 1}", f"{major}.{minor + 1}.0", f"{major + 1}.0.0"]
    if test:
        candidates = ([f"{major}.{minor}.{patch}rc{int(rc[1]) + 1}"] if rc else []) + [f"{b}rc1" for b in bumps]
        version = pick_version(f"Current version: {current}. Test version:", candidates, r"\d+\.\d+\.\d+rc\d+", current)
    else:
        candidates = [f"{major}.{minor}.{patch}"] if rc else bumps
        version = pick_version(f"Current version: {current}. Release:", candidates, r"\d+\.\d+\.\d+", current)
    tag = f"v{version}"

    check_ready(tag, upgrade=test)
    confirm(f"This sets the version to {version}, " + ("" if test else "moves the CHANGELOG entries, ")
            + f"pushes to master and publishes {tag} on GitHub and PyPI"
            + (" as a test version (only mirrors with Test versions on get it)." if test else " for every mirror."))

    # ponytail: the release commit (version + CHANGELOG only) lands on top of the tested code, so the release
    # has the tested code but not the tested sha. If an exact sha is ever needed, tag before testing instead.
    set_version(version)
    if not test:
        move_changelog(version, keep_os_image=True)
    commit_and_push(f"chore: {'test version' if test else 'release'} {tag}", "pyproject.toml", "CHANGELOG.md", "uv.lock")
    sha = run("git", "rev-parse", "HEAD")
    notes = (f"Test version of MirrorDash {version}, for mirrors with Test versions turned on.\n\n"
             + unreleased_notes().split("### OS image")[0].strip()) if test else changelog_section(version)
    started = now()
    run("gh", "release", "create", tag, "--target", sha, "--title", tag, "--notes", notes,
        *(["--prerelease"] if test else []), change=True)
    watch("publish.yml", sha, started)
    print(f"\n  {tag} is on PyPI.")
    if test:
        print("  On your test mirror: turn on Settings → Test versions, then Check for Updates.")
        print("  When it's good, run this again and choose 2 to release it for everyone.")


def build_test_image():
    """Step 3: CI builds the OS image and its update bundle and publishes them as a test version, a
    GitHub pre-release vX.Y.Z-osN. Mirrors with Test versions on are offered it under Settings → Check
    for Updates, like a test version of the app; nothing is downloaded to this computer unless you want
    to flash a card. Step 4 then turns that same pre-release into the release."""
    core = current_version()
    if "rc" in core:
        die(f"master is at test version {core}; release the app first (2): an OS image carries a released app.")
    check_ready(None, tests=False)  # first: it fetches the tags next_os_version counts
    version = next_os_version(core)
    tag = f"v{version}"
    check_ready(tag, tests=False)
    sha = run("git", "rev-parse", "HEAD")
    confirm(f"This builds the OS image and update bundle from master ({sha[:7]}, MirrorDash {core}) on GitHub "
            f"(about 30 minutes) and publishes them as the test version {tag}: only mirrors with Test versions "
            "on are offered it.")
    started = now()
    notes = (f"Test version of the MirrorDash OS {version}, for mirrors with Test versions turned on.\n\n"
             + os_image_notes())
    run("gh", "workflow", "run", "build-os-image.yml", "--ref", "master", "-f", f"tag={tag}", "-f", f"notes={notes}",
        change=True)
    run_id = watch("build-os-image.yml", sha, started)
    if DRY:
        return
    TEST_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    (TEST_IMAGE_DIR / "run.json").write_text(json.dumps(
        {"run_id": run_id, "sha": sha, "core_version": core, "tag": tag}, indent=2))
    print(f"\n  {tag} is published as a test version (a pre-release on GitHub).")
    print("  On a mirror with Test versions on: Settings → Check for Updates → Install System Update.")
    if ask("  Download the image too, to flash a card? [y/N] ").lower() == "y":
        target = TEST_IMAGE_DIR / run_id
        download_image(tag, target)
        image = next(target.glob("*.img.xz"))
        if subprocess.run(["sha256sum", "--check", "--quiet", f"{image.name}.sha256"], cwd=target).returncode != 0:
            die("the downloaded image doesn't match its checksum; download it again from the release.")
        print(f"  Image: {image} (checksum matches)")
    print("  When it's good, run this again and choose 4. If it isn't: gh release delete " + tag + " (keep the tag,")
    print("  so its number isn't used again), fix it, and choose 3 again.")


def release_image():
    """Step 4: the tested pre-release becomes the release for every mirror. Same files: nothing is rebuilt
    or uploaded again, so what mirrors get is exactly what was tested."""
    try:
        built = json.loads((TEST_IMAGE_DIR / "run.json").read_text())
    except FileNotFoundError:
        die("there's no OS test version yet; choose 3 first.")
    tag = built.get("tag") or die("the last build is from before OS test versions; choose 3 to build one.")
    core = current_version()
    if built["core_version"] != core or "rc" in core:
        die(f"{tag} has MirrorDash {built['core_version']} but master is at {core}; "
            "release the app first (2), then build a new OS test version (3).")
    check_ready(None)
    info = json.loads(run("gh", "release", "view", tag, "--json", "isPrerelease,assets"))
    if not info["isPrerelease"]:
        die(f"{tag} is already released.")
    names = [a["name"] for a in info["assets"]]
    if not any(n.endswith(".img.xz") for n in names) or not any(n.endswith(".raucb") for n in names):
        die(f"{tag} lacks its image or update bundle ({', '.join(names) or 'no files'}); choose 3 again.")
    changed = [f for f in run("git", "diff", "--name-only", built["sha"], "HEAD").splitlines() if f != "CHANGELOG.md"]
    if changed:
        print(f"\n  Master has changed since {tag} was built ({built['sha'][:7]}); it doesn't contain:")
        print("".join(f"    {f}\n" for f in changed[:15]), end="")
    confirm(f"This releases {tag} for every mirror: moves the CHANGELOG entries, pushes to master and turns the "
            f"test version you tried into the release. Nothing is rebuilt.")
    version = tag[1:]
    move_changelog(version, intro=f"OS image with MirrorDash {core}.", only_os_image=True)
    commit_and_push(f"chore: release {tag}", "CHANGELOG.md")
    notes = changelog_section(version) + f"\n\nImage built from {built['sha'][:7]} (Actions run {built['run_id']})."
    run("gh", "release", "edit", tag, "--prerelease=false", "--latest", "--title", tag, "--notes", notes, change=True)
    print(f"\n  {tag} is released: every mirror is offered it under Settings → Check for Updates.")


def main():
    if DRY:
        print("Dry run: nothing is changed, pushed or published.")
    steps = [("Test version of the app (only test mirrors get it)", lambda: release_app(test=True)),
             ("Release the app for everyone", lambda: release_app(test=False)),
             ("Test version of the OS (only test mirrors get it)", build_test_image),
             ("Release the tested OS for everyone", release_image)]
    steps[choose("What do you want to do?", [label for label, _ in steps])][1]()


if __name__ == "__main__":
    main()

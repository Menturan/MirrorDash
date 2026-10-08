#!/usr/bin/env python3
"""MirrorDash release wizard: every real release builds on something you tested first.

    python3 scripts/release.py            # asks what to do
    python3 scripts/release.py --dry-run  # shows every change and git/gh command instead of running it

  1. Test version of the app  -> X.Y.ZrcN on PyPI as a pre-release; only mirrors with "Test versions" on get it
  2. Release the app          -> CHANGELOG + vX.Y.Z on GitHub and PyPI, for every mirror
  3. Build a test OS image    -> CI builds it from master, downloaded to build_workspace/test-image/ to flash
  4. Release the tested image -> CHANGELOG + vX.Y.Z-osN with that same file (nothing is rebuilt)

Needs the GitHub CLI, logged in (`gh auth login`), and the dev venv (.venv) for the tests.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import tomllib
from datetime import date, datetime, timezone
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


def release_changelog(version, intro="", keep_os_image=False, path="CHANGELOG.md"):
    """Move the hand-written [Unreleased] entries into a new [version] section.

    keep_os_image leaves the "### OS image" subsection under [Unreleased], since an app
    release doesn't change the OS image. Exits if there is nothing to release.
    """
    with open(path, "r") as f:
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
    builds = re.findall(rf"^## \[{re.escape(core_version)}-os(\d+)\]", (ROOT / "CHANGELOG.md").read_text(), re.M)
    return f"{core_version}-os{max(map(int, builds), default=0) + 1}"


# --- Safety checks --------------------------------------------------------------------------

def check_ready(tag, tests=True):
    """Stop unless this is a clean master equal to origin, gh works and the tag is free."""
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
    if tests:
        print("\n  Running the tests...")
        if subprocess.run([str(ROOT / ".venv" / "bin" / "pytest"), "-q"], cwd=ROOT).returncode != 0:
            die("the tests failed.")


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
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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

    check_ready(tag)
    confirm(f"This sets the version to {version}, " + ("" if test else "moves the CHANGELOG entries, ")
            + f"pushes to master and publishes {tag} on GitHub and PyPI"
            + (" as a test version (only mirrors with Test versions on get it)." if test else " for every mirror."))

    # ponytail: the release commit (version + CHANGELOG only) lands on top of the tested code, so the release
    # has the tested code but not the tested sha. If an exact sha is ever needed, tag before testing instead.
    set_version(version)
    if not test:
        move_changelog(version, keep_os_image=True)
    commit_and_push(f"chore: {'test version' if test else 'release'} {tag}", "pyproject.toml", "CHANGELOG.md")
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
    check_ready(tag=None, tests=False)
    core = current_version()
    if "rc" in core:
        print(f"  Note: master is at test version {core}; the image will contain it.")
    sha = run("git", "rev-parse", "HEAD")
    confirm(f"This builds an OS image from master ({sha[:7]}, MirrorDash {core}) on GitHub. It takes about 30 minutes.")
    started = now()
    run("gh", "workflow", "run", "build-os-image.yml", "--ref", "master", change=True)
    run_id = watch("build-os-image.yml", sha, started)
    if DRY:
        return
    target = TEST_IMAGE_DIR / run_id
    shutil.rmtree(TEST_IMAGE_DIR, ignore_errors=True)
    run("gh", "run", "download", run_id, "--name", "mirrordash-os-image", "--dir", str(target), change=True)
    image = next(target.glob("*.img.xz"))
    if subprocess.run(["sha256sum", "--check", "--quiet", f"{image.name}.sha256"], cwd=target).returncode != 0:
        die(f"the downloaded image doesn't match its checksum; download it again: gh run download {run_id}")
    (TEST_IMAGE_DIR / "run.json").write_text(json.dumps(
        {"run_id": run_id, "sha": sha, "core_version": core, "image": str(image)}, indent=2))
    print(f"\n  Test image: {image}")
    print("  Flash it, go through the hardware checklist in RELEASING.md, then run this again and choose 4.")


def release_image():
    try:
        built = json.loads((TEST_IMAGE_DIR / "run.json").read_text())
    except FileNotFoundError:
        die("there's no tested image yet; choose 3 first.")
    image = Path(built["image"])
    if not image.exists():
        die(f"the tested image {image} is gone; choose 3 to build a new one.")
    core = current_version()
    if built["core_version"] != core or "rc" in core:
        die(f"the image has MirrorDash {built['core_version']} but master is at {core}; "
            "release the app first (2), then build a new test image (3).")
    version = next_os_version(core)
    tag = f"v{version}"

    check_ready(tag)
    changed = [f for f in run("git", "diff", "--name-only", built["sha"], "HEAD").splitlines() if f != "CHANGELOG.md"]
    if changed:
        print(f"\n  Master has changed since the image was built ({built['sha'][:7]}); the image doesn't contain:")
        print("".join(f"    {f}\n" for f in changed[:15]), end="")
    confirm(f"This releases {tag}: moves the CHANGELOG entries, pushes to master and publishes the tested image "
            f"{image.name} on GitHub. Nothing is rebuilt.")

    move_changelog(version, intro=f"OS image with MirrorDash {core}.")
    commit_and_push(f"chore: release {tag}", "CHANGELOG.md")
    notes = changelog_section(version) + f"\n\nImage built from {built['sha'][:7]} (Actions run {built['run_id']})."
    run("gh", "release", "create", tag, str(image), f"{image}.sha256", "--target", run("git", "rev-parse", "HEAD"),
        "--title", tag, "--notes", notes, change=True)
    print(f"\n  {tag} is published with the image you tested.")


def main():
    if DRY:
        print("Dry run: nothing is changed, pushed or published.")
    steps = [("Test version of the app (only test mirrors get it)", lambda: release_app(test=True)),
             ("Release the app for everyone", lambda: release_app(test=False)),
             ("Build a test OS image to flash", build_test_image),
             ("Release the tested OS image", release_image)]
    steps[choose("What do you want to do?", [label for label, _ in steps])][1]()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Pre-release automation for the MirrorDash Core App (PyPI package).

Usage:
    python3 scripts/release_core.py 0.2.5

What it does:
    1. Validates the version argument (SemVer: X.Y.Z)
    2. Ensures you are on the master branch and up to date
    3. Runs the full test suite — halts on failure
    4. Bumps version in pyproject.toml
    5. Reorganizes CHANGELOG.md (Core App entries → [X.Y.Z], System OS stays under [Unreleased])
    6. Commits and pushes to origin/master

What it does NOT do (intentional — requires human decision):
    - Create the GitHub Release (you do this in the browser or via `gh`)
    - Write CHANGELOG entries (someone must have already added them under [Unreleased])
"""

from __future__ import annotations

import re
import subprocess
import sys


def run(cmd, check=True):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"ERROR: Command failed: {' '.join(cmd)}")
        print(result.stderr)
        sys.exit(1)
    return result


def validate_version(version):
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        print(f"Error: Version must be SemVer (X.Y.Z), got: {version}")
        sys.exit(1)


def ensure_master():
    branch = run(["git", "branch", "--show-current"]).stdout.strip()
    if branch != "master":
        print(f"  WARNING: Not on master (current: {branch}). Continuing anyway...")
    print("  Pulling latest from origin/master...")
    run(["git", "pull", "origin", "master"])


def run_tests():
    print("\n  Running: .venv/bin/pytest")
    result = subprocess.run([".venv/bin/pytest"], shell=False)
    if result.returncode != 0:
        print("  ERROR: Tests failed.")
        sys.exit(1)
    print("  All tests passed.")


def bump_version(version):
    with open("pyproject.toml", "r") as f:
        content = f.read()
    new_content = re.sub(
        r'^version = ".*"',
        f'version = "{version}"',
        content,
        count=1,
        flags=re.MULTILINE,
    )
    if new_content == content:
        print("  ERROR: Could not find version line in pyproject.toml")
        sys.exit(1)
    with open("pyproject.toml", "w") as f:
        f.write(new_content)
    print(f"  Bumped pyproject.toml to {version}")


def commit_and_push(version):
    run(["git", "add", "pyproject.toml", "CHANGELOG.md"])
    run(["git", "commit", "--no-gpg-sign", "-m", f"chore: bump version to {version}"])
    print("  Committed and pushed to origin/master.")


def main():
    if len(sys.argv) != 2:
        print("Usage: release_core.py <version>")
        print("  version: SemVer string, e.g. 0.2.5")
        print("\nExample:\n  python3 scripts/release_core.py 0.2.5")
        sys.exit(1)

    VERSION = sys.argv[1]
    validate_version(VERSION)

    repos_dir = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    print(f"=== MirrorDash Core App Release Helper: {VERSION} ===")
    print(f"  Repo: {repos_dir}\n")

    print("[1/5] Checking git state...")
    ensure_master()

    print("\n[2/5] Running test suite...")
    run_tests()

    print("\n[3/5] Bumping version...")
    bump_version(VERSION)

    print("\n[4/5] Reorganizing CHANGELOG.md...")
    result = subprocess.run(
        ["python3", "scripts/release_changelog.py", "core", VERSION],
        cwd=repos_dir,
    )
    if result.returncode != 0:
        print("  ERROR: CHANGELOG reorganization failed")
        sys.exit(1)

    print("\n[5/5] Committing and pushing...")
    commit_and_push(VERSION)

    print("\n=== Done ===\n")
    print("Next steps (manual):")
    print("  1. Go to https://github.com/Menturan/MirrorDash/releases/new")
    print(f"  2. Tag: v{VERSION}")
    print(f"  3. Title: v{VERSION}")
    print("  4. Publish release - triggers PyPI publish workflow\n")


if __name__ == "__main__":
    main()

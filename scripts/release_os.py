#!/usr/bin/env python3
"""Pre-release automation for the MirrorDash System OS Image (GitHub Release asset).

Usage:
    python3 scripts/release_os.py 0.2.4-os1

What it does:
    1. Validates the version argument (format: X.Y.Z-osN)
    2. Ensures you are on the master branch and up to date
    3. Runs the full test suite — halts on failure
    4. Reorganizes CHANGELOG.md (System OS entries → [X.Y.Z-osN], Core App stays in [X.Y.Z])
    5. Commits and pushes to origin/master

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
    if not re.match(r"^\d+\.\d+\.\d+-os\d+$", version):
        print(f"Error: Version must be in X.Y.Z-osN format (e.g. 0.2.4-os1), got: {version}")
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


def commit_and_push(version):
    run(["git", "add", "CHANGELOG.md"])
    run(["git", "commit", "--no-gpg-sign", "-m", f"chore: organize CHANGELOG for {version} OS image release"])
    print("  Committed and pushed to origin/master.")


def main():
    if len(sys.argv) != 2:
        print("Usage: release_os.py <version>")
        print("  version: SemVer with -osN suffix, e.g. 0.2.4-os1")
        print("\nExample:\n  python3 scripts/release_os.py 0.2.4-os1")
        sys.exit(1)

    VERSION = sys.argv[1]
    validate_version(VERSION)

    repos_dir = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    print(f"=== MirrorDash System OS Release Helper: {VERSION} ===")
    print(f"  Repo: {repos_dir}\n")

    print("[1/4] Checking git state...")
    ensure_master()

    print("\n[2/4] Running test suite...")
    run_tests()

    print("\n[3/4] Reorganizing CHANGELOG.md...")
    result = subprocess.run(
        ["python3", "scripts/release_changelog.py", "os", VERSION],
        cwd=repos_dir,
    )
    if result.returncode != 0:
        print("  ERROR: CHANGELOG reorganization failed")
        sys.exit(1)

    print("\n[4/4] Committing and pushing...")
    commit_and_push(VERSION)

    print("\n=== Done ===\n")
    print("Next steps (manual):")
    print("  1. Go to https://github.com/Menturan/MirrorDash/releases/new")
    print(f"  2. Tag: v{VERSION}")
    print(f"  3. Title: v{VERSION}")
    print("  4. Publish release - triggers ARM build workflow (~15-30 min)")
    print("  5. Monitor Actions tab and download .img.gz when done\n")


if __name__ == "__main__":
    main()

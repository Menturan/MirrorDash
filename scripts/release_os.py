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
import shutil
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


def get_latest_core_version():
    try:
        with open("pyproject.toml", "r") as f:
            content = f.read()
        match = re.search(r'^version = "(.*?)"', content, re.MULTILINE)
        if match:
            return match.group(1)
    except Exception:
        pass
    print("  ERROR: Could not read core version from pyproject.toml")
    sys.exit(1)


def get_next_os_version(core_version):
    try:
        with open("CHANGELOG.md", "r") as f:
            content = f.read()
        escaped_core = re.escape(core_version)
        matches = re.findall(rf"## \[{escaped_core}-os(\d+)\]", content)
        if matches:
            max_build = max(int(m) for m in matches)
            return f"{core_version}-os{max_build + 1}"
    except Exception:
        pass
    return f"{core_version}-os1"


def prompt_os_version_wizard(core_version):
    next_os_version = get_next_os_version(core_version)
    
    print(f"Latest Core App version: {core_version}")
    print("Select target OS release version:")
    print(f"  1. Recommended -> {next_os_version}")
    print("  2. Custom version")
    
    while True:
        try:
            choice = input("Enter choice (1-2): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(1)
            
        if choice == "1":
            return next_os_version
        elif choice == "2":
            try:
                custom = input(f"Enter custom version (format: {core_version}-osN): ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nAborted.")
                sys.exit(1)
            return custom
        else:
            print("Invalid choice, please select 1 or 2.")


def main():
    if len(sys.argv) == 2:
        VERSION = sys.argv[1]
    elif len(sys.argv) == 1:
        core_version = get_latest_core_version()
        VERSION = prompt_os_version_wizard(core_version)
    else:
        print("Usage: release_os.py [version]")
        print("  version: SemVer with -osN suffix, e.g. 0.2.4-os1")
        print("\nExample:\n  python3 scripts/release_os.py 0.2.4-os1")
        sys.exit(1)

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

    print("\n[3/4] Reorganizing CHANGELOG.md using git-cliff...")
    if shutil.which("git-cliff"):
        git_cliff_cmd = ["git-cliff"]
    elif shutil.which("npx"):
        git_cliff_cmd = ["npx", "--yes", "git-cliff"]
    else:
        print("  ERROR: git-cliff or npx not found in PATH.")
        print("  Please install git-cliff (e.g. via cargo: `cargo install git-cliff` or npm: `npm install -g git-cliff`).")
        sys.exit(1)

    result = subprocess.run(
        git_cliff_cmd + ["-t", f"v{VERSION}", "-o", "CHANGELOG.md"],
        cwd=repos_dir,
    )
    if result.returncode != 0:
        print("  ERROR: git-cliff failed to reorganize CHANGELOG.md")
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

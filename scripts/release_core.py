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
    if not re.match(r"^\d+\.\d+\.\d+(?:-[a-zA-Z0-9.]+)?$", version):
        print(f"Error: Version must be SemVer (X.Y.Z[-prerelease]), got: {version}")
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


def get_current_version():
    with open("pyproject.toml", "r") as f:
        content = f.read()
    match = re.search(r'^version = "(.*?)"', content, re.MULTILINE)
    if not match:
        print("  ERROR: Could not find version line in pyproject.toml")
        sys.exit(1)
    return match.group(1)


def parse_semver(version_str):
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-([a-zA-Z0-9.]+))?$", version_str)
    if not match:
        return None
    major, minor, patch, prerelease = match.groups()
    return {
        "major": int(major),
        "minor": int(minor),
        "patch": int(patch),
        "prerelease": prerelease
    }


def prompt_prerelease(major, minor, patch):
    print("\nSelect target base version for the prerelease:")
    next_patch = f"{major}.{minor}.{patch + 1}"
    next_minor = f"{major}.{minor + 1}.0"
    next_major = f"{major + 1}.0.0"
    print(f"  1. bugfix (patch) -> {next_patch}")
    print(f"  2. minor          -> {next_minor}")
    print(f"  3. major          -> {next_major}")
    
    while True:
        try:
            base_choice = input("Enter choice (1-3): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(1)
        if base_choice == "1":
            base_ver = next_patch
            break
        elif base_choice == "2":
            base_ver = next_minor
            break
        elif base_choice == "3":
            base_ver = next_major
            break
        else:
            print("Invalid choice.")
            
    print("\nSelect prerelease type:")
    print("  1. alpha      (e.g., -a1)")
    print("  2. beta       (e.g., -b1)")
    print("  3. candidate  (e.g., -rc1)")
    print("  4. custom suffix")
    
    while True:
        try:
            type_choice = input("Enter choice (1-4): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(1)
        if type_choice == "1":
            suffix = "a1"
            break
        elif type_choice == "2":
            suffix = "b1"
            break
        elif type_choice == "3":
            suffix = "rc1"
            break
        elif type_choice == "4":
            try:
                suffix = input("Enter custom prerelease suffix (e.g. dev1): ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nAborted.")
                sys.exit(1)
            if suffix:
                break
            print("Suffix cannot be empty.")
        else:
            print("Invalid choice.")
            
    suffix = suffix.lstrip("-.")
    return f"{base_ver}-{suffix}"


def prompt_version_wizard(current_version):
    parsed = parse_semver(current_version)
    if not parsed:
        print(f"Error parsing current version '{current_version}' as SemVer.")
        sys.exit(1)
        
    major, minor, patch, prerelease = parsed["major"], parsed["minor"], parsed["patch"], parsed["prerelease"]
    
    next_patch = f"{major}.{minor}.{patch + 1}"
    next_minor = f"{major}.{minor + 1}.0"
    next_major = f"{major + 1}.0.0"
    
    next_pre_inc = None
    if prerelease:
        pre_match = re.match(r"^([a-zA-Z.-]+)(\d+)$", prerelease)
        if pre_match:
            pre_label, pre_num = pre_match.groups()
            next_pre_inc = f"{major}.{minor}.{patch}-{pre_label}{int(pre_num) + 1}"
        else:
            next_pre_inc = f"{major}.{minor}.{patch}-{prerelease}.1"
            
    print(f"Current version: {current_version}")
    print("Select target release type:")
    if next_pre_inc:
        print(f"  1. increment prerelease        -> {next_pre_inc}")
        print(f"  2. bugfix (patch release)      -> {next_patch}")
        print(f"  3. minor                       -> {next_minor}")
        print(f"  4. major                       -> {next_major}")
        print(f"  5. new prerelease              -> [guided]")
        print("  6. custom version")
    else:
        print(f"  1. bugfix (patch release)      -> {next_patch}")
        print(f"  2. minor                       -> {next_minor}")
        print(f"  3. major                       -> {next_major}")
        print(f"  4. prerelease                  -> [guided]")
        print("  5. custom version")
        
    while True:
        try:
            choice = input("Enter choice: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(1)
            
        if next_pre_inc:
            if choice == "1":
                return next_pre_inc
            elif choice == "2":
                return next_patch
            elif choice == "3":
                return next_minor
            elif choice == "4":
                return next_major
            elif choice == "5":
                return prompt_prerelease(major, minor, patch)
            elif choice == "6":
                try:
                    custom = input("Enter custom version: ").strip()
                except (KeyboardInterrupt, EOFError):
                    print("\nAborted.")
                    sys.exit(1)
                return custom
            else:
                print("Invalid choice.")
        else:
            if choice == "1":
                return next_patch
            elif choice == "2":
                return next_minor
            elif choice == "3":
                return next_major
            elif choice == "4":
                return prompt_prerelease(major, minor, patch)
            elif choice == "5":
                try:
                    custom = input("Enter custom version: ").strip()
                except (KeyboardInterrupt, EOFError):
                    print("\nAborted.")
                    sys.exit(1)
                return custom
            else:
                print("Invalid choice.")


def main():
    if len(sys.argv) == 2:
        VERSION = sys.argv[1]
    elif len(sys.argv) == 1:
        current_version = get_current_version()
        VERSION = prompt_version_wizard(current_version)
    else:
        print("Usage: release_core.py [version]")
        print("  version: SemVer string, e.g. 0.2.5")
        print("\nExample:\n  python3 scripts/release_core.py 0.2.5")
        sys.exit(1)

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

    print("\n[4/5] Reorganizing CHANGELOG.md using git-cliff...")
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

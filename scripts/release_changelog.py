#!/usr/bin/env python3
"""Reorganize CHANGELOG.md for a Core App or System OS release.

Usage:
    python3 scripts/release_changelog.py core 0.2.5
    python3 scripts/release_changelog.py os 0.2.4-os1
"""

import re
import sys
from datetime import date


def parse_unreleased(content: str) -> tuple[list[str], list[str]]:
    """Extract System OS and Core App bullet items from the [Unreleased] section."""
    # Match [Unreleased] block: either followed by another ## [X.Y.Z] section,
    # or followed by comparison links at the bottom, or end of string
    unreleased_match = re.search(
        r"## \[Unreleased\](.*?)(?=\n\[Unreleased\]:|\Z)",
        content,
        re.DOTALL,
    )
    if not unreleased_match:
        return [], []

    block = unreleased_match.group(1)
    system_os: list[str] = []
    core_app: list[str] = []
    current: list[str] | None = None

    for line in block.splitlines():
        stripped = line.strip()
        if stripped == "### System OS (Appliance)":
            current = system_os
        elif stripped == "### Core App":
            current = core_app
        elif stripped.startswith("- ") and current is not None:
            current.append(stripped)

    return system_os, core_app


def update_comparison_links(content: str, new_version: str, prev_version: str) -> str:
    """Update the [Unreleased] comparison link and add the new version link."""
    # Update [Unreleased] to point at the new version
    content = re.sub(
        r"(\[Unreleased\]: https://.*?compare/)(.*?)(\.\.\.HEAD)",
        lambda m: f"{m.group(1)}{new_version}...HEAD",
        content,
        count=1,
    )
    # Add new version comparison link after the [Unreleased] line
    base_url = "https://github.com/Menturan/MirrorDash/compare"
    new_link = f"[{new_version}]: {base_url}/{prev_version}...{new_version}\n"
    # Insert right after the [Unreleased] link line
    content = re.sub(
        r"(\[Unreleased\]: .*?\.\.\.HEAD\n)",
        r"\1" + new_link,
        content,
        count=1,
    )
    return content


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: release_changelog.py <core|os> <version>")
        print("  core 0.2.5    — move Core App entries to [0.2.5]")
        print("  os 0.2.4-os1  — move System OS entries to [0.2.4-os1]")
        sys.exit(1)

    track, version = sys.argv[1], sys.argv[2]
    if track not in ("core", "os"):
        print(f"Error: track must be 'core' or 'os', got '{track}'")
        sys.exit(1)

    changelog_path = "CHANGELOG.md"
    with open(changelog_path, "r") as f:
        content = f.read()

    system_os, core_app = parse_unreleased(content)

    today = date.today().isoformat()

    if track == "core":
        if not core_app:
            print("Error: No Core App entries found under [Unreleased]")
            sys.exit(1)

        # Build the new [X.Y.Z] section with only Core App items
        new_section = f"\n## [{version}] - {today}\n\n### Core App\n"
        new_section += "\n".join(sorted(set(core_app))) + "\n"

        # Rebuild [Unreleased] with only remaining items (System OS)
        remaining = f"## [Unreleased]\n\n### System OS (Appliance)\n"
        if system_os:
            remaining += "\n".join(sorted(set(system_os))) + "\n"

        prev_version_match = re.search(r"\[(\d+\.\d+\.\d+)\]:", content)
        prev_version = prev_version_match.group(1) if prev_version_match else "0.0.0"

    else:  # os
        if not system_os:
            print("Error: No System OS entries found under [Unreleased]")
            sys.exit(1)

        new_section = f"\n## [{version}] - {today}\n\n### System OS (Appliance)\n"
        new_section += "\n".join(sorted(set(system_os))) + "\n"

        remaining = f"## [Unreleased]\n\n### Core App\n"
        if core_app:
            remaining += "\n".join(sorted(set(core_app))) + "\n"

        # For OS releases, find the Core App version to link against
        prev_version_match = re.search(r"## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}", content)
        prev_version = prev_version_match.group(1) if prev_version_match else "0.0.0"

    # Replace the entire [Unreleased] block
    content = re.sub(
        r"## \[Unreleased\].*?(?=\n\[Unreleased\]:|\Z)",
        remaining.rstrip("\n"),
        content,
        flags=re.DOTALL,
    )

    # Insert the new version section after [Unreleased]
    insert_anchor = "## [Unreleased]\n"
    idx = content.find(insert_anchor)
    if idx == -1:
        print("Error: Could not locate [Unreleased] section")
        sys.exit(1)

    idx = content.find("\n", idx + len(insert_anchor)) + 1
    content = content[:idx] + new_section + content[idx:]

    # Update comparison links
    content = update_comparison_links(content, version, prev_version)

    with open(changelog_path, "w") as f:
        f.write(content)

    moved = core_app if track == "core" else system_os
    print(f"Moved {len(moved)} {track} entries to [{version}]")
    print(f"Kept {len(system_os if track == 'core' else core_app)} entries under [Unreleased]")


if __name__ == "__main__":
    main()

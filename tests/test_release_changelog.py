import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from release import release_changelog  # noqa: E402

TODAY = date.today().isoformat()
CHANGELOG = """# Changelog

## [Unreleased]

### Fixed
- App fix.

### OS image
- OS fix.

## [0.4.0] - 2026-10-06

- Old entry.
"""


def test_app_release_keeps_os_image_entries_unreleased(tmp_path):
    p = tmp_path / "CHANGELOG.md"
    p.write_text(CHANGELOG)
    release_changelog("0.4.1", keep_os_image=True, path=p)
    assert p.read_text() == f"""# Changelog

## [Unreleased]

### OS image
- OS fix.

## [0.4.1] - {TODAY}

### Fixed
- App fix.

## [0.4.0] - 2026-10-06

- Old entry.
"""


def test_os_release_moves_only_os_image_entries(tmp_path):
    """App entries not released yet aren't in the image's app version: they wait for the next app release."""
    p = tmp_path / "CHANGELOG.md"
    p.write_text(CHANGELOG)
    release_changelog("0.4.0-os2", intro="OS image with MirrorDash 0.4.0.", only_os_image=True, path=p)
    assert p.read_text() == f"""# Changelog

## [Unreleased]

### Fixed
- App fix.

## [0.4.0-os2] - {TODAY}

OS image with MirrorDash 0.4.0.

- OS fix.

## [0.4.0] - 2026-10-06

- Old entry.
"""


def test_refuses_empty_or_duplicate_release(tmp_path):
    p = tmp_path / "CHANGELOG.md"
    p.write_text("# Changelog\n\n## [Unreleased]\n\n### OS image\n- OS fix.\n\n## [0.4.0] - 2026-10-06\n")
    with pytest.raises(SystemExit):
        release_changelog("0.4.1", keep_os_image=True, path=p)  # only OS entries: nothing for the app
    with pytest.raises(SystemExit):
        release_changelog("0.4.0", path=p)  # already released
    p.write_text("# Changelog\n\n## [Unreleased]\n\n### Fixed\n- App fix.\n\n## [0.4.0] - 2026-10-06\n")
    with pytest.raises(SystemExit):
        release_changelog("0.4.0-os2", only_os_image=True, path=p)  # only app entries: nothing for the image

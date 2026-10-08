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


def test_os_release_moves_everything_with_intro(tmp_path):
    p = tmp_path / "CHANGELOG.md"
    p.write_text(CHANGELOG)
    release_changelog("0.4.0-os2", intro="OS image with MirrorDash 0.4.0.", path=p)
    text = p.read_text()
    assert text.startswith(f"# Changelog\n\n## [Unreleased]\n\n## [0.4.0-os2] - {TODAY}\n\nOS image with MirrorDash 0.4.0.\n\n### Fixed\n")
    assert "### OS image\n- OS fix.\n\n## [0.4.0]" in text


def test_refuses_empty_or_duplicate_release(tmp_path):
    p = tmp_path / "CHANGELOG.md"
    p.write_text("# Changelog\n\n## [Unreleased]\n\n### OS image\n- OS fix.\n\n## [0.4.0] - 2026-10-06\n")
    with pytest.raises(SystemExit):
        release_changelog("0.4.1", keep_os_image=True, path=p)  # only OS entries: nothing for the app
    with pytest.raises(SystemExit):
        release_changelog("0.4.0", path=p)  # already released

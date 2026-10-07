import os
import pytest
from pathlib import Path

# Always resolve test config to the repo root config.json.
# This prevents tests from reading from (or polluting) the developer's local
# ~/.mirrordash/data/config.json — which holds real passwords and settings.
# Any load_config() call that bypasses mocking will safely fall back here.
os.environ.setdefault(
    "MIRRORDASH_CONFIG_PATH",
    str(Path(__file__).parent.parent / "config.json")
)


@pytest.fixture(autouse=True)
def _clear_remote_json_cache():
    """Update checks are cached per URL; start every test without earlier answers."""
    from mirrordash_core.system import network
    network._remote_json_cache.clear()

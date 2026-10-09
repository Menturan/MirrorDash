import os
import shutil
import tempfile

import pytest
from pathlib import Path

# Always resolve test config to a throwaway copy of the repo's config.json.
# This keeps tests away from the developer's ~/.mirrordash/data/config.json (real passwords
# and settings) and from the tracked config.json, which unmocked saves used to rewrite.
_test_config = Path(tempfile.mkdtemp(prefix="mirrordash-test-")) / "config.json"
shutil.copy(Path(__file__).parent.parent / "config.json", _test_config)
os.environ.setdefault("MIRRORDASH_CONFIG_PATH", str(_test_config))


@pytest.fixture(autouse=True)
def _clear_remote_json_cache():
    """Update checks are cached per URL; start every test without earlier answers."""
    from mirrordash_core import fetch
    fetch._remote_json_cache.clear()


@pytest.fixture(autouse=True)
def _never_restart_the_test_run(monkeypatch):
    """run_restart() SIGTERMs its own process; a test that reaches it must not end the run."""
    real_kill = os.kill
    monkeypatch.setattr(os, "kill", lambda pid, sig: None if pid == os.getpid() else real_kill(pid, sig))

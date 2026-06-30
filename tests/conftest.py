import os
from pathlib import Path

# Always resolve test config to the repo root config.json.
# This prevents tests from reading from (or polluting) the developer's local
# ~/.mirrordash/data/config.json — which holds real passwords and settings.
# Any load_config() call that bypasses mocking will safely fall back here.
os.environ.setdefault(
    "MIRRORDASH_CONFIG_PATH",
    str(Path(__file__).parent.parent / "config.json")
)

# Licensed under the PolyForm Noncommercial License 1.0.0.

import json
import logging
import os
import tempfile
import threading
import copy
from pathlib import Path

logger = logging.getLogger("mirrordash.core.config")

# Get project root (parent directory of mirrordash_core)
ROOT_DIR = Path(__file__).parent.parent.resolve()

def get_base_dir() -> Path:
    """Get the active base directory."""
    return Path(os.path.expanduser("~")) / ".mirrordash"

def get_config_path(for_saving: bool = False) -> Path:
    """Resolve the location of config.json.

    1. Check MIRRORDASH_CONFIG_PATH environment variable.
    2. Check ~/.mirrordash/data/config.json.
    3. Check ~/.mirrordash/config.json.
    4. Check ROOT_DIR/config.json (only when loading/not saving).
    5. Fallback to ~/.mirrordash/data/config.json.
    """
    env_path = os.environ.get("MIRRORDASH_CONFIG_PATH")
    if env_path:
        return Path(env_path).resolve()

    home_data_config = Path(os.path.expanduser("~")) / ".mirrordash" / "data" / "config.json"
    if home_data_config.exists():
        return home_data_config

    home_config = Path(os.path.expanduser("~")) / ".mirrordash" / "config.json"
    if home_config.exists():
        return home_config

    if not for_saving:
        dev_config = ROOT_DIR / "config.json"
        if dev_config.exists():
            return dev_config

    return home_data_config

# Thread-safe config lock and cache
_config_lock = threading.Lock()
_config_cache: dict | None = None

def get_default_globals() -> dict:
    return {
        "language": "en",
        "timezone": "Europe/Stockholm",
        "time_format": "24h",
        "temperature_unit": "C",
        "distance_unit": "km",
        "latitude": 59.3293,
        "longitude": 18.0686
    }

def load_config() -> dict:
    """Load config from memory cache, or disk if cache is cold. Returns a deep copy."""
    global _config_cache
    with _config_lock:
        if _config_cache is not None:
            return copy.deepcopy(_config_cache)

        config_path = get_config_path()
        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    data = json.load(f)
                    if not isinstance(data, dict):
                        data = {}
                    if "modules" not in data:
                        data["modules"] = {}
                    if "globals" not in data:
                        data["globals"] = get_default_globals()
                    _config_cache = data
                    return copy.deepcopy(_config_cache)
            except json.JSONDecodeError as e:
                logger.error(f"{config_path.name} is malformed: {e}. Using empty config.")

        _config_cache = {
            "globals": get_default_globals(),
            "modules": {}
        }
        return copy.deepcopy(_config_cache)

def save_config(config: dict) -> None:
    """Atomically save config to disk and update cache thread-safely."""
    global _config_cache
    config_path = get_config_path(for_saving=True)

    # Ensure parent directory exists
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Work on a deep copy of the config
    config_copy = copy.deepcopy(config)

    # Write to a temp file in the same directory then rename — atomic on POSIX
    fd, tmp_path = tempfile.mkstemp(dir=str(config_path.parent), suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(config_copy, f, indent=2)
        os.rename(tmp_path, str(config_path))
        with _config_lock:
            _config_cache = config_copy  # update cache with deep copy
        logger.info(f"Configuration saved successfully to {config_path}.")
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

def invalidate_config_cache() -> None:
    """Force next load_config() to re-read from disk."""
    global _config_cache
    with _config_lock:
        _config_cache = None

def find_module_config(modules_config: dict, module_name: str) -> tuple[str | None, dict | None]:
    """Finds the configuration key and dictionary for a module name, allowing underscore/hyphen mismatches."""
    if not isinstance(modules_config, dict):
        return None, None
    if module_name in modules_config:
        return module_name, modules_config[module_name]

    # Try normalized lookup (converting hyphens to underscores)
    norm_target = module_name.replace('-', '_')
    for key in modules_config:
        if key.replace('-', '_') == norm_target:
            return key, modules_config[key]

    return None, None

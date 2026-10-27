# Licensed under the PolyForm Noncommercial License 1.0.0.

import copy
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger("mirrordash.core.config")

# Get project root (parent directory of mirrordash_core)
ROOT_DIR = Path(__file__).parent.parent.resolve()

def get_base_dir() -> Path:
    """Get the active base directory."""
    return Path(os.path.expanduser("~")) / ".mirrordash"

def get_module_dir() -> Path:
    """Where modules keep their data_dir and cache_dir: on a mirror the module host's own folder,
    which it may write and the core's data dir is not (MIRRORDASH_MODULE_DIR, ARCHITECTURE #41)."""
    return Path(os.environ.get("MIRRORDASH_MODULE_DIR") or get_base_dir() / "module-data")

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

def migrate_config(data: dict) -> bool:
    """Migrates old module configurations to the new format containing a 'module' property."""
    changed = False
    if "modules" not in data or not isinstance(data["modules"], dict):
        return changed
    for instance_id, module_cfg in list(data["modules"].items()):
        if isinstance(module_cfg, dict) and "module" not in module_cfg:
            module_cfg["module"] = instance_id
            changed = True
    return changed

def default_config() -> dict:
    """A new mirror's settings: the clock in the top left corner."""
    return {
        "globals": get_default_globals(),
        "modules": {
            "mirrordash-clock": {
                "module": "mirrordash-clock",
                "enabled": True,
                "position": "top_left",
                "show_seconds": True,
                "show_header": False
            }
        }
    }


def _read_config_or_last_good_copy(config_path: Path) -> tuple[dict | None, bool]:
    """(the settings on disk, whether they must be saved again). A file that doesn't read is never
    overwritten with defaults: it is kept for a person to look at, and the last good copy save_config
    kept is used instead. A power cut mid-write or a bad hand edit ends up here."""
    if not config_path.exists():
        return None, False
    try:
        return json.loads(config_path.read_text()), False
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        broken = config_path.with_name(f"{config_path.name}.broken-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
        config_path.rename(broken)
        last_good_copy = _read_json(_backup_path(config_path))
        logger.error(f"{config_path.name} is malformed: {e}. Kept as {broken.name}; "
                     + ("using the last good copy." if last_good_copy is not None else "no good copy, using defaults."))
        return last_good_copy, last_good_copy is not None


def _fill_in_missing_sections(data) -> tuple[dict, bool]:
    """(the settings with "modules" and "globals" always there, whether anything had to change)."""
    if not isinstance(data, dict):
        data = {}
    data.setdefault("modules", {})
    data.setdefault("globals", get_default_globals())
    return data, migrate_config(data)


def load_config() -> dict:
    """Load config from memory cache, or disk if cache is cold. Returns a deep copy."""
    global _config_cache
    with _config_lock:
        if _config_cache is not None:
            return copy.deepcopy(_config_cache)
        data, needs_saving = _read_config_or_last_good_copy(get_config_path())
        if data is None:
            data, needs_saving = default_config(), True
        else:
            data, migrated = _fill_in_missing_sections(data)
            needs_saving = needs_saving or migrated
        _config_cache = data

    if needs_saving:
        try:
            save_config(_config_cache)
        except Exception as e:
            logger.error(f"Failed to save migrated configuration: {e}")

    with _config_lock:
        return copy.deepcopy(_config_cache)


def save_config(config: dict) -> None:
    """Atomically save config to disk and update cache thread-safely."""
    global _config_cache
    config_path = get_config_path(for_saving=True)

    # Ensure parent directory exists
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Work on a deep copy of the config
    config_copy = copy.deepcopy(config)

    # The file as it is now becomes the backup load_config falls back to, but only when it reads:
    # a broken file must never replace a good backup. ponytail: one backup generation.
    if _read_json(config_path) is not None:
        write_atomic(_backup_path(config_path), config_path.read_text())
    write_atomic(config_path, json.dumps(config_copy, indent=2))
    with _config_lock:
        _config_cache = config_copy  # update cache with deep copy
    logger.info(f"Configuration saved successfully to {config_path}.")

def _backup_path(config_path: Path) -> Path:
    return config_path.with_name(config_path.name + ".bak")

def _read_json(path: Path):
    """The file's JSON, or None when it is missing or doesn't read."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None

def write_atomic(path: Path | str, text: str, mode: int | None = None) -> None:
    """Write a file so a power cut leaves either the old or the new content, never a torn or empty file."""
    path = Path(path)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    # The rename itself is only durable once the directory is written
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)

def invalidate_config_cache() -> None:
    """Force next load_config() to re-read from disk."""
    global _config_cache
    with _config_lock:
        _config_cache = None

def find_module_config(modules_config: dict, module_name: str) -> tuple[str | None, dict | None]:
    """Finds the configuration key and dictionary for a module name, checking 'module' key first."""
    if not isinstance(modules_config, dict):
        return None, None
    
    def same_module(name: str) -> bool:  # "mirrordash-clock" and "mirrordash_clock" are one module
        return name.replace("-", "_") == module_name.replace("-", "_")

    for instance_id, instance_config in modules_config.items():
        if not isinstance(instance_config, dict):
            continue
        # An instance not migrated yet has no "module": its key is the module's name (migrate_config)
        if same_module(instance_config.get("module") or instance_id):
            return instance_id, instance_config

    return None, None

def get_core_version() -> str:
    """Resolve the currently installed version of mirrordash."""
    import importlib.metadata
    for pkg_name in ("mirrordash", "mirrordash-core", "mirrordash_core"):
        try:
            return importlib.metadata.version(pkg_name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return "unknown"

def version_key(version: str) -> tuple:
    """Sortable key for "1.2.3", "v1.2.3" and pre-releases like "1.2.3rc1"; a release sorts above its
    own pre-releases (0.5.0rc1 < 0.5.0). Anything else sorts lowest."""
    # ponytail: only the version shapes MirrorDash publishes, not all of PEP 440 (no epochs, .post or .dev);
    # the mirror has no `packaging` to lean on.
    import re
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-?(a|b|rc)(\d+))?", version.strip())
    if not match:
        return (0,)
    major, minor, patch, prerelease_kind, prerelease_number = match.groups()
    # a < b < rc < the release itself (3)
    prerelease_rank = ("a", "b", "rc").index(prerelease_kind) if prerelease_kind else 3
    return (int(major), int(minor), int(patch), prerelease_rank, int(prerelease_number or 0))



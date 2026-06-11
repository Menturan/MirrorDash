import os
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from mirrordash_core.config import get_config_path, load_config, save_config, invalidate_config_cache

def test_get_config_path_env_var(tmp_path):
    custom_path = tmp_path / "custom_config.json"
    with patch.dict(os.environ, {"MIRRORDASH_CONFIG_PATH": str(custom_path)}):
        assert get_config_path() == custom_path

def test_get_config_path_home_exists(tmp_path):
    # Mock home path to a temp dir
    fake_home = tmp_path / "home"
    fake_config_dir = fake_home / ".mirrordash"
    fake_config_dir.mkdir(parents=True)
    fake_config_file = fake_config_dir / "config.json"
    fake_config_file.touch()
    
    with patch.dict(os.environ, {}), \
         patch("os.path.expanduser", return_value=str(fake_home)):
        # Since the home config exists, it should be chosen over any dev config
        assert get_config_path() == fake_config_file

def test_get_config_path_dev_fallback(tmp_path):
    # Mock home path to a temp dir where config doesn't exist
    fake_home = tmp_path / "home"
    
    # Mock ROOT_DIR to a path where config.json DOES exist
    fake_root = tmp_path / "root"
    fake_root.mkdir()
    dev_config_file = fake_root / "config.json"
    dev_config_file.touch()
    
    with patch.dict(os.environ, {}), \
         patch("os.path.expanduser", return_value=str(fake_home)), \
         patch("mirrordash_core.config.ROOT_DIR", fake_root):
        assert get_config_path() == dev_config_file

def test_get_config_path_default_home(tmp_path):
    # Mock home path where it doesn't exist, and root where it doesn't exist
    fake_home = tmp_path / "home"
    fake_root = tmp_path / "root"
    
    with patch.dict(os.environ, {}), \
         patch("os.path.expanduser", return_value=str(fake_home)), \
         patch("mirrordash_core.config.ROOT_DIR", fake_root):
        expected = Path(fake_home) / ".mirrordash" / "data" / "config.json"
        assert get_config_path() == expected

def test_load_config_malformed(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text("invalid json{")
    
    with patch("mirrordash_core.config.get_config_path", return_value=config_file):
        invalidate_config_cache()
        config = load_config()
        # Should return defaults
        assert "globals" in config
        assert "modules" in config
        assert config["globals"]["language"] == "en"

def test_save_config_creates_parent_and_writes(tmp_path):
    # Target file in a nested subdirectory that doesn't exist yet
    config_file = tmp_path / "nested" / "dir" / "config.json"
    
    test_config = {
        "globals": {"language": "sv"},
        "modules": {"mirrordash-clock": {"enabled": True}}
    }
    
    with patch("mirrordash_core.config.get_config_path", return_value=config_file):
        invalidate_config_cache()
        save_config(test_config)
        
        # Verify directory was created and file written
        assert config_file.exists()
        with open(config_file, "r") as f:
            saved = json.load(f)
        assert saved["globals"]["language"] == "sv"
        assert saved["modules"]["mirrordash-clock"]["enabled"] is True

def test_get_config_path_for_saving_ignores_dev_fallback(tmp_path):
    # Mock home path to a temp dir where config doesn't exist
    fake_home = tmp_path / "home"
    
    # Mock ROOT_DIR to a path where config.json DOES exist
    fake_root = tmp_path / "root"
    fake_root.mkdir()
    dev_config_file = fake_root / "config.json"
    dev_config_file.touch()
    
    with patch.dict(os.environ, {}), \
         patch("os.path.expanduser", return_value=str(fake_home)), \
         patch("mirrordash_core.config.ROOT_DIR", fake_root):
        # When saving, it should ignore the dev_config_file and fallback to default home
        expected = Path(fake_home) / ".mirrordash" / "data" / "config.json"
        assert get_config_path(for_saving=True) == expected

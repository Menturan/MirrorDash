from unittest.mock import patch

import pytest
from conftest import call
from fastapi.testclient import TestClient

from mirrordash_core.admin import hash_password
from mirrordash_core.app import app
from mirrordash_core.features.modules.service import update_modules

mock_salt = "0123456789abcdef"
mock_hash = hash_password("secret", mock_salt)

MOCK_CONFIG = {
    "admin_auth": {
        "hash": mock_hash,
        "salt": mock_salt
    }
}

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture(autouse=True)
def mock_admin_shared_load_config():
    with patch("mirrordash_core.admin.load_config", return_value=MOCK_CONFIG):
        yield


def test_failsafe_update_success(module_site, client):
    response = call(update_modules, "mirrordash-news")

    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # Upgraded in a copy of the module site and imported from it by the module host, never as the core
    assert module_site.calls == [("pip", "install", "--upgrade", "mirrordash-news"),
                                 ("check", "mirrordash-news", "mirrordash_news", "mirrordash-news")]
    assert module_site.kept


def test_failsafe_update_rollback(module_site, client):
    module_site.answer = lambda op, args: (1, "ImportError: Broken module") if op == "check" else (0, "")

    response = call(update_modules, "mirrordash-news")

    assert response.status_code == 500
    assert "Verification failed. Rolled back successfully. Error: ImportError: Broken module" in response.json()["detail"]
    assert module_site.kept is False


def test_a_module_that_doesnt_load_doesnt_hold_back_the_others(module_site, client):
    """In one site change, so one restart: b doesn't load, so b is put back as it is in the running site
    and a and c are kept, checked together once more; only when none loads is nothing changed."""
    module_site.answer = lambda op, args: (1, "ImportError: b") if op == "check" and args[0] == "b" else (0, "")
    running = [{"name": "b", "package_name": "b", "version": "1.0", "type": "pypi"}]
    with patch("mirrordash_core.features.modules.installed.installed_modules", return_value=running):
        response = call(update_modules, "a", "b", "c")
    assert response.status_code == 200 and response.json()["updated"] == ["a", "c"]
    assert "b didn't work" in response.json()["message"]
    assert module_site.calls == [("pip", "install", "--upgrade", "a"), ("check", "a", "a", "a"),
                                 ("pip", "install", "--upgrade", "b"), ("check", "b", "b", "b"),
                                 ("pip", "install", "--upgrade", "c"), ("check", "c", "c", "c"),
                                 ("pip", "install", "b==1.0"), ("check", "a", "a", "a", "c", "c", "c")]
    assert module_site.kept

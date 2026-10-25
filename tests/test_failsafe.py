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

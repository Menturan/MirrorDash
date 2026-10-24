from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from mirrordash_core.app import app


@pytest.fixture
def client():
    return TestClient(app)

def test_active_modules_carousel_fields(client):
    config = {"modules": {
        "mirrordash-calendar": {"position": "middle_left", "carousel_group": "group1", "carousel_interval": 10},
        # No carousel_group or carousel_interval (should fall back to the defaults)
        "mirrordash-clock": {"position": "middle_left"},
    }}

    with patch("mirrordash_core.features.kiosk.routes.module_host.running", ["mirrordash-calendar", "mirrordash-clock"]), \
         patch("mirrordash_core.features.kiosk.routes.load_config", return_value=config), \
         patch("mirrordash_core.features.kiosk.routes.find_entry_point", return_value=None):
        response = client.get("/api/active-modules")
        assert response.status_code == 200
        data = response.json()
        assert "modules" in data
        modules = data["modules"]

        # Verify calendar module
        calendar_mod = next(m for m in modules if m["name"] == "mirrordash-calendar")
        assert calendar_mod["position"] == "middle_left"
        assert calendar_mod["carousel_group"] == "group1"
        assert calendar_mod["carousel_interval"] == 10

        # Verify clock module
        clock_mod = next(m for m in modules if m["name"] == "mirrordash-clock")
        assert clock_mod["position"] == "middle_left"
        assert clock_mod["carousel_group"] is None
        assert clock_mod["carousel_interval"] == 15

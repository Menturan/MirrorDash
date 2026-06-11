import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from mirrordash_core.app import app

@pytest.fixture
def client():
    return TestClient(app)

def test_active_modules_carousel_fields(client):
    # Mocking module_loader.instances
    mock_instance_1 = MagicMock()
    mock_instance_1.config = {
        "position": "middle_left",
        "carousel_group": "group1",
        "carousel_interval": 10
    }
    # Mocking translate helper
    mock_instance_1.translate = lambda key, default: default
    
    mock_instance_2 = MagicMock()
    mock_instance_2.config = {
        "position": "middle_left"
        # No carousel_group or carousel_interval (should fallback to default)
    }
    mock_instance_2.translate = lambda key, default: default

    mock_instances = {
        "mirrordash-calendar": mock_instance_1,
        "mirrordash-clock": mock_instance_2
    }

    with patch("mirrordash_core.app.module_loader.instances", mock_instances):
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

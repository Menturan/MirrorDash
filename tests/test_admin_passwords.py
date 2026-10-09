"""Coding rule 8d (every password field has Show/Hide) and the SSH card's answer."""
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from mirrordash_core.app import app

CORE = Path(__file__).parent.parent / "mirrordash_core"


def test_every_password_field_can_be_shown():
    for path in [*CORE.glob("templates/*.html"), *CORE.glob("api/*.py")]:
        text = path.read_text()
        for field in re.finditer(r'<input type="password"[^>]*>', text):
            field_id = re.search(r'id="([^"]+)"', field.group(0))
            assert field_id, f"{path.name}: a password field without an id can't have Show/Hide"
            assert f'data-reveal="{field_id.group(1)}"' in text, f"{path.name}: {field_id.group(1)} has no Show/Hide"


def _post_ssh(form, ssh_active=False):
    from mirrordash_core.api.admin import require_api_key
    app.dependency_overrides[require_api_key] = lambda: None
    proc = MagicMock(returncode=0)
    proc.communicate = AsyncMock(return_value=(b"$6$hash\n", b""))
    try:
        with patch("mirrordash_core.api.admin_system.load_config", return_value={}), \
             patch("mirrordash_core.api.admin_system.save_config") as save, \
             patch("mirrordash_core.api.admin_system.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)), \
             patch("mirrordash_core.api.admin_system.open", create=True), \
             patch("mirrordash_core.api.admin_system.os.chmod"), \
             patch("mirrordash_core.system.get_ssh_status", AsyncMock(return_value=ssh_active)), \
             patch("mirrordash_core.system.set_ssh_status", AsyncMock(return_value=True)):
            return TestClient(app).post("/admin/panels/system/save", data=form), save
    finally:
        app.dependency_overrides.clear()


def test_turning_ssh_on_says_so():
    r, save = _post_ssh({"ssh": "true", "pi_password": "longenough"})
    assert r.status_code == 200
    assert "SSH is on" in r.headers["HX-Trigger-After-Swap"] and '"md-ssh": {"on": true}' in r.headers["HX-Trigger-After-Swap"]
    save.assert_called_once()


def test_a_refused_ssh_password_changes_nothing():
    r, save = _post_ssh({"ssh": "true", "pi_password": "short"})
    assert r.status_code == 400 and "8 characters" in r.json()["detail"]
    save.assert_not_called()

"""Matter: the core starts the node with its own token, which opens /api/v1 next to the API card's."""
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from mirrordash_core.admin import hash_password, require_api_key, token_hash
from mirrordash_core.app import app
from mirrordash_core.features.matter import routes as matter

ROOTFS = Path(__file__).parent.parent / "scripts/image/rootfs"


def test_turning_matter_on_and_off(tmp_path):
    config = {"admin_auth": {"hash": hash_password("secret", "salt"), "salt": "salt"}, "system": {}}
    systemctl = AsyncMock(return_value=True)
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        with patch.object(matter, "MATTER_DIR", tmp_path / "matter"), patch.object(matter, "ENV_FILE", tmp_path / "env"), \
             patch.object(matter, "load_config", return_value=config), patch.object(matter, "save_config"), \
             patch.object(matter, "_systemctl", systemctl), \
             patch("mirrordash_core.admin.load_config", return_value=config), \
             patch("mirrordash_core.features.api.api.load_config", return_value=config):
            client = TestClient(app)
            assert client.post("/admin/panels/system/matter/on").status_code == 200
            systemctl.assert_awaited_with("restart")
            env = dict(line.split("=", 1) for line in (tmp_path / "env").read_text().splitlines())
            assert (tmp_path / "env").stat().st_mode & 0o777 == 0o600
            assert int(env["MATTER_PASSCODE"]) not in matter.FORBIDDEN_PASSCODES and 0 <= int(env["MATTER_DISCRIMINATOR"]) < 4096

            # The node's token opens the API, alongside no API card token at all
            auth = {"Authorization": f"Bearer {env['MIRRORDASH_TOKEN']}"}
            assert client.get("/api/v1/status", headers=auth).status_code == 200
            assert client.get("/api/v1/status", headers={"Authorization": "Bearer wrong"}).status_code == 401

            # Codes are kept across starts; the token is new each time
            passcode = config["matter"]["passcode"]
            client.post("/admin/panels/system/matter/on")
            assert config["matter"]["passcode"] == passcode
            assert client.get("/api/v1/status", headers=auth).status_code == 401

            (tmp_path / "matter/pairing.json").write_text('{"qr": "MT:Y.K90AFN00KA0648G00", "manual": "34970112332", "commissioned": false}')
            card = client.get("/admin/panels/system/matter").text
            assert "3497-011-2332" in card and "MT:Y.K90AFN00KA0648G00" in card

            client.post("/admin/panels/system/matter/off")
            systemctl.assert_awaited_with("stop")
            assert config["matter"]["enabled"] is False and "token_hash" not in config["matter"]
    finally:
        app.dependency_overrides.clear()


def test_the_api_card_token_still_works_beside_matter():
    config = {"api_token": {"hash": token_hash("a")}, "matter": {"enabled": True, "token_hash": token_hash("m")}, "system": {}}
    with patch("mirrordash_core.admin.load_config", return_value=config), \
         patch("mirrordash_core.features.api.api.load_config", return_value=config):
        client = TestClient(app)
        for token in ("a", "m"):
            assert client.get("/api/v1/status", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_the_image_installs_the_node_but_leaves_it_off():
    setup = (Path(__file__).parent.parent / "scripts/configure_image.sh").read_text()
    assert "put 0644 /etc/systemd/system/mirrordash-matter.service" in setup
    assert "enable mirrordash-matter" not in setup  # /etc forgets it; the core starts the node
    unit = (ROOTFS / "etc/systemd/system/mirrordash-matter.service").read_text()
    assert "EnvironmentFile=/storage/mirrordash/data/matter.env" in unit and "[Install]" not in unit
    sudoers = (ROOTFS / "etc/sudoers.d/mirrordash").read_text()
    assert all(f"systemctl {a} mirrordash-matter" in sudoers for a in ("restart", "stop"))

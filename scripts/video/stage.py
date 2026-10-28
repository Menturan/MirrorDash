"""The real MirrorDash app in this process, with a throwaway HOME and nothing that touches hardware.

    import stage; url = stage.start(); stage.hotspot = True  # the mirror shows its Wi-Fi prompt
"""
import os
import socket
import tempfile
import threading
import time
from unittest.mock import AsyncMock, patch

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.expanduser("~/.cache/ms-playwright"))
os.environ["HOME"] = tempfile.mkdtemp(prefix="mirrordash-video-")  # before the app reads ~/.mirrordash
_data = os.path.join(os.environ["HOME"], ".mirrordash", "data")
os.makedirs(_data)
os.environ["MIRRORDASH_CONFIG_PATH"] = os.path.join(_data, "config.json")  # a new mirror, not the repo's config.json
with open(os.environ["MIRRORDASH_CONFIG_PATH"], "w") as f:
    f.write('{"globals": {"language": "en", "timezone": "Europe/Stockholm", "time_format": "24h"}}')
hotspot = False


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start():
    import uvicorn

    async def is_hotspot():
        return hotspot

    for target, value in [
        ("mirrordash_core.features.kiosk.routes.is_wifi_hotspot_active", is_hotspot),
        ("mirrordash_core.features.kiosk.routes.get_hotspot_password", AsyncMock(return_value="mirror-4821")),
        ("mirrordash_core.system_settings.apply_saved_system_settings_at_startup", lambda: None),
    ]:
        patch(target, value).start()
    from mirrordash_core import app as app_module
    from mirrordash_core.app import app

    # Nothing at startup touches this computer: no screen, GPIO, network watch, MQTT or module processes
    for obj in (app_module.module_host, app_module.display_power_manager, app_module.gpio_inputs,
                app_module.internet_monitor, app_module.mqtt_bridge):
        for name in ("start", "stop", "start_modules", "stop_modules"):
            if hasattr(obj, name):
                patch.object(obj, name, AsyncMock()).start()

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    time.sleep(2)
    return f"http://127.0.0.1:{port}"

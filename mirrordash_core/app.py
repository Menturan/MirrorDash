# Licensed under the PolyForm Noncommercial License 1.0.0.
"""Puts the app together: each feature under features/ brings its own routes (a vertical slice)."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from mirrordash_core import admin, system_settings
from mirrordash_core.admin import PACKAGE_DIR
from mirrordash_core.features.api import api as api_v1
from mirrordash_core.features.api import routes as api
from mirrordash_core.features.auth import routes as auth
from mirrordash_core.features.backup import routes as backup
from mirrordash_core.features.dashboard import routes as dashboard
from mirrordash_core.features.hardware import routes as hardware
from mirrordash_core.features.hardware.devices import gpio_inputs
from mirrordash_core.features.kiosk import routes as kiosk
from mirrordash_core.features.kiosk.internet import internet_monitor
from mirrordash_core.features.logs import routes as logs
from mirrordash_core.features.modules import routes as modules
from mirrordash_core.features.modules import service as modules_service
from mirrordash_core.features.modules.loader import module_loader
from mirrordash_core.features.power import routes as power
from mirrordash_core.features.power.display_power import display_power_manager
from mirrordash_core.features.settings import routes as settings
from mirrordash_core.features.updates import routes as updates
from mirrordash_core.features.updates import service as updates_service
from mirrordash_core.features.wifi import routes as wifi

logger = logging.getLogger("mirrordash.core.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await module_loader.start_modules()
        await display_power_manager.start()
        await gpio_inputs.start()
        await internet_monitor.start()
    except Exception as e:
        logger.error(f"Error during module startup: {e}", exc_info=True)
    yield
    await internet_monitor.stop()
    await gpio_inputs.stop()
    await display_power_manager.stop()
    await module_loader.stop_modules()

# No /docs, /redoc or /openapi.json here: the admin page is the only client, so they'd only be attack surface.
# /api/v1 is its own app with its own description (features/api/api.py).
app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

# No CORS: the admin page and the mirror's screen come from this server, Home Assistant calls from its
# own server, and "any origin, with credentials" would let any web page use a logged-in admin session.
# While the setup hotspot is up, everything but the setup page redirects to it
app.middleware("http")(wifi.captive_portal_redirect)

for feature in (admin, system_settings, auth, backup, dashboard, hardware, api,
                kiosk, logs, modules, modules_service, power, settings, updates, updates_service, wifi):
    app.include_router(feature.router)
app.mount("/api/v1", api_v1.api)

app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

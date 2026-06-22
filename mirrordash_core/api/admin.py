# Licensed under the PolyForm Noncommercial License 1.0.0.

from fastapi import APIRouter

# Re-exports for backwards compatibility
from mirrordash_core.api.admin_shared import require_api_key, hash_password, templates
from mirrordash_core.api.admin_config import get_panel_config
from mirrordash_core.api.admin_modules import start_community_modules_scan, stop_community_modules_scan

# Import sub-routers
from mirrordash_core.api import (
    admin_auth,
    admin_backup,
    admin_config,
    admin_logs,
    admin_modules,
    admin_modules_panels,
    admin_system,
    admin_system_panels,
)

router = APIRouter(prefix="/admin")

# Register sub-routers
router.include_router(admin_auth.router)
router.include_router(admin_config.router)
router.include_router(admin_modules.router)
router.include_router(admin_modules_panels.router)
router.include_router(admin_system.router)
router.include_router(admin_system_panels.router)
router.include_router(admin_backup.router)
router.include_router(admin_logs.router)

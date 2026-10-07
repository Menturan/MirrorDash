# Licensed under the PolyForm Noncommercial License 1.0.0.

from fastapi import APIRouter, Depends

# Re-exports for backwards compatibility
from mirrordash_core.api.admin_shared import require_api_key, hash_password, templates, job_status
from mirrordash_core.api.admin_config import get_panel_config

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


@router.get("/jobs/current", dependencies=[Depends(require_api_key)])
async def get_current_job() -> dict:
    return job_status()


# Register sub-routers
router.include_router(admin_auth.router)
router.include_router(admin_config.router)
router.include_router(admin_modules.router)
router.include_router(admin_modules_panels.router)
router.include_router(admin_system.router)
router.include_router(admin_system_panels.router)
router.include_router(admin_backup.router)
router.include_router(admin_logs.router)

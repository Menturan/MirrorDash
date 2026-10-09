# Licensed under the PolyForm Noncommercial License 1.0.0.

import logging
from fastapi import APIRouter, Depends, Request
from mirrordash_core.admin import events_header, require_api_key, templates
from mirrordash_core.config import load_config, save_config

logger = logging.getLogger("mirrordash.core.homeassistant")
router = APIRouter(prefix="/admin")


def _api_access_card(request: Request, new_token: str = "", message: str = ""):
    created = load_config().get("api_token", {}).get("created", "")
    return templates.TemplateResponse(
        request=request, name="admin_api_access.html",
        context={"created": created, "new_token": new_token},
        headers=events_header(**{"md-notify": {"message": message, "kind": "success"}}) if message else None,
    )


@router.get("/panels/system/api-access", dependencies=[Depends(require_api_key)])
async def get_api_access_card(request: Request):
    return _api_access_card(request)


@router.post("/panels/system/api-token/create", dependencies=[Depends(require_api_key)])
async def create_api_token(request: Request):
    """A new token replaces the old one. Only its hash is kept, so it is shown this once."""
    import datetime
    import secrets
    from mirrordash_core.admin import token_hash

    token = secrets.token_urlsafe(32)
    config = load_config()
    config["api_token"] = {"hash": token_hash(token), "created": datetime.date.today().isoformat()}
    save_config(config)
    return _api_access_card(request, new_token=token, message="Token created.")


@router.post("/panels/system/api-token/remove", dependencies=[Depends(require_api_key)])
async def remove_api_token(request: Request):
    config = load_config()
    config.pop("api_token", None)
    save_config(config)
    return _api_access_card(request, message="Token removed.")

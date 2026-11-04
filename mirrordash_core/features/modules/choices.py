# SPDX-License-Identifier: AGPL-3.0-only
"""The choices of a list or search setting (ARCHITECTURE #48): a module's settings_list_<key>(item) or
settings_search_<key>(item, query), called by a throwaway instance made from the admin's unsaved form.

Its answer is input from a module, so it is checked here: a list of {"id", "name", "detail"} texts, cut to
size. A ValueError's text is shown under the field as it is (the module's own words: "Enter the API key
first"); anything else is a generic error, its details in the log."""

import asyncio
import inspect
import logging
import re

from mirrordash_core.features.modules.module_files import find_entry_point, package_dir
from mirrordash_core.features.modules.throwaway import throwaway_instance

logger = logging.getLogger("mirrordash.core.modules")

CHOICES_TIMEOUT = 15  # seconds a module may take to answer (an API at the other end)
MAX_CHOICES = 2000  # a list longer than this isn't one an owner picks from: a search setting is
MAX_TEXT = 200  # characters of an id, name or detail
MAX_ERROR = 300  # characters of a module's error text
KINDS = ("list", "search")
SETTING_KEY = re.compile(r"^[a-z0-9_]{1,64}$")  # the key a method name is made from (mirrordash-sdk validate checks it)


def method_name(kind: str, key: str) -> str:
    """The module's method for a setting: settings_list_school, settings_search_city."""
    return f"settings_{kind}_{key}"


def _clean(raw) -> list[dict]:
    if not isinstance(raw, (list, tuple)):
        raise TypeError(f"gave {type(raw).__name__}, not a list")
    choices = []
    for entry in raw[:MAX_CHOICES]:
        if isinstance(entry, dict) and entry.get("id") is not None and entry.get("name") is not None:
            choices.append({"id": str(entry["id"])[:MAX_TEXT], "name": str(entry["name"])[:MAX_TEXT],
                            "detail": str(entry.get("detail") or "")[:MAX_TEXT]})
    return choices


async def setting_choices(instance_id: str, module_cfg: dict, globals_config: dict, link, request: dict) -> dict:
    """{"choices": [...]} or {"error": text} for request {kind, key, item, query}: item is the field's list
    row (or the whole form for a field outside a list), query what the owner typed (search)."""
    kind, key, item, query = request.get("kind"), request.get("key"), request.get("item"), request.get("query")
    if kind not in KINDS or not isinstance(key, str) or not SETTING_KEY.match(key) or not isinstance(item, dict):
        return {"error": "Not a list or search setting."}
    ep = find_entry_point(module_cfg.get("module") or instance_id)
    if ep is None:
        return {"error": "The module isn't installed."}
    try:
        with throwaway_instance(ep, package_dir(ep), instance_id, module_cfg, globals_config, link) as plugin:
            method = getattr(plugin, method_name(kind, key), None)
            if not inspect.iscoroutinefunction(method):
                return {"error": f"The module has no async {method_name(kind, key)}()."}
            call = method(item, str(query or "")) if kind == "search" else method(item)
            return {"choices": _clean(await asyncio.wait_for(call, CHOICES_TIMEOUT))}
    except ValueError as e:  # the module's own words for the owner
        return {"error": str(e)[:MAX_ERROR] or "Couldn't load the choices."}
    except TimeoutError:
        return {"error": "The module took too long to answer."}
    except Exception as e:
        logger.warning(f"{instance_id}: {method_name(kind, key)} failed: {e!r}")
        return {"error": "Couldn't load the choices (see Logs)."}

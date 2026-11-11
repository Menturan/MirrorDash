# SPDX-License-Identifier: AGPL-3.0-only
"""Changes to the module instances in config.json that more than the admin page makes."""

from mirrordash_core.config import load_config, save_config
from mirrordash_core.features.modules.client import module_host


async def set_instances_enabled(changes: dict[str, bool]) -> None:
    """Turn module instances on or off (the admin page's toggle, MQTT's switches): one save and one reload
    for all of them; one that isn't there is left out."""
    config = load_config()
    instances = config.get("modules", {})
    changed = [instance_id for instance_id, enabled in changes.items()
               if isinstance(instances.get(instance_id), dict) and instances[instance_id].get("enabled", True) != enabled]
    for instance_id in changed:
        instances[instance_id]["enabled"] = changes[instance_id]
    if changed:
        save_config(config)
        await module_host.reload_modules()

# SPDX-License-Identifier: AGPL-3.0-only
"""The secrets in the settings, which a plain backup leaves out (ARCHITECTURE #39).

A backup without a password carries no password, key or pairing code: without_secrets takes them out of
the config (the admin password, the MQTT password, the Matter code, and every module setting its schema
shows as a password field). A restore of such a backup keeps this mirror's own ones
(_keep_this_mirrors_secrets).
"""

import copy

# The secrets in the config a plain backup leaves out, as (section, key): the admin password's hash and
# recovery code, the MQTT broker's password, and the Matter pairing code (it lets a phone add the mirror)
SECRET_CONFIG_ENTRIES = (("admin_auth", None), ("mqtt", "password"), ("matter", "passcode"), ("matter", "discriminator"))

def secret_module_settings(instance_config: dict) -> set[str]:
    """The settings of a module instance that are secrets (an API key, a password): what its config_schema
    shows as a password field (format password, or a name like api_key or token, forms.py)."""
    from mirrordash_core.features.modules.module_files import find_entry_point
    from mirrordash_core.features.settings.schema import get_module_schema
    from mirrordash_core.forms import SECRET_KEYS
    ep = find_entry_point(str(instance_config.get("module", "")))
    properties = ((get_module_schema(ep) if ep else None) or {}).get("properties", {})
    return {key for key, prop in properties.items()
            if isinstance(prop, dict) and (prop.get("format") == "password" or key in SECRET_KEYS)}

def without_secrets(config: dict) -> dict:
    """The config for a plain backup: no password, key or pairing code in it (_keep_this_mirrors_secrets
    puts the mirror's own back on a restore)."""
    config = copy.deepcopy(config)
    for section, key in SECRET_CONFIG_ENTRIES:
        if key is None:
            config.pop(section, None)
        elif isinstance(config.get(section), dict):
            config[section].pop(key, None)
    for instance_config in config.get("modules", {}).values():
        if isinstance(instance_config, dict):
            for key in secret_module_settings(instance_config):
                instance_config.pop(key, None)
    return config

def _keep_this_mirrors_secrets(restored: dict, current: dict) -> None:
    """A plain backup has no secrets: what it left out comes from this mirror's own settings, where it has
    them (the same module instance for a module's key)."""
    for section, key in SECRET_CONFIG_ENTRIES:
        current_section = current.get(section)
        if key is None:
            if not restored.get(section) and current_section:
                restored[section] = current_section
        elif isinstance(current_section, dict) and key in current_section:
            restored_section = restored.setdefault(section, {})
            if isinstance(restored_section, dict) and key not in restored_section:
                restored_section[key] = current_section[key]
    current_modules = current.get("modules", {})
    for instance_id, instance_config in restored.get("modules", {}).items():
        current_instance = current_modules.get(instance_id)
        if not (isinstance(instance_config, dict) and isinstance(current_instance, dict)
                and current_instance.get("module") == instance_config.get("module")):
            continue
        for key in secret_module_settings(instance_config):
            if key not in instance_config and key in current_instance:
                instance_config[key] = current_instance[key]

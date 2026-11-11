# SPDX-License-Identifier: AGPL-3.0-only
"""The secrets in the settings, which a plain backup leaves out (ARCHITECTURE #39).

A backup without a password carries no password, key or pairing code: without_secrets takes them out of
the config (the admin password, the MQTT password, the Matter code, and every module setting its schema
shows as a password field, in a nested object or a list's rows too). A restore of such a backup keeps this
mirror's own ones (_keep_this_mirrors_secrets).
"""

import copy

# The secrets in the config a plain backup leaves out, as (section, key): the admin password's hash and
# recovery code, the MQTT broker's password, and the Matter pairing code (it lets a phone add the mirror)
SECRET_CONFIG_ENTRIES = (("admin_auth", None), ("mqtt", "password"), ("matter", "passcode"), ("matter", "discriminator"))
# Setting names that hold a secret even when the schema forgot "format": "password" (the form shows those as
# typed; mirrordash-sdk validate warns): a backup leaves them out all the same
SECRET_KEYS = ("api_key", "password", "token", "secret")

def module_secrets(instance_config: dict) -> dict:
    """Where a module instance's secrets are (an API key, a password): the settings its config_schema marks
    "format": "password", and those with a name like api_key or token, also inside a nested object or a
    list's rows (_secrets_in)."""
    from mirrordash_core.features.modules.module_files import find_entry_point
    from mirrordash_core.features.modules.schema import get_module_schema
    ep = find_entry_point(str(instance_config.get("module", "")))
    return _secrets_in(((get_module_schema(ep) if ep else None) or {}).get("properties", {}))

def _secrets_in(properties: dict) -> dict:
    """{key: True} for a secret, {key: {...}} for a nested object holding some, {key: [{...}]} for a list
    whose rows hold some."""
    secrets = {}
    for key, prop in properties.items() if isinstance(properties, dict) else ():
        if not isinstance(prop, dict):
            continue
        items = prop.get("items") if isinstance(prop.get("items"), dict) else {}
        if prop.get("format") == "password" or key in SECRET_KEYS:
            secrets[key] = True
        elif inner := _secrets_in(prop.get("properties")):
            secrets[key] = inner
        elif inner := _secrets_in(items.get("properties")):
            secrets[key] = [inner]
    return secrets

def _strip(values: dict, secrets: dict) -> dict:
    """values without the secrets (changed in place, and returned)."""
    for key, secret in secrets.items():
        value = values.get(key)
        if secret is True:
            values.pop(key, None)
        elif isinstance(secret, list):
            for row in value if isinstance(value, list) else ():
                if isinstance(row, dict):
                    _strip(row, secret[0])
        elif isinstance(value, dict):
            _strip(value, secret)
    return values

def _put_back(restored: dict, current: dict, secrets: dict) -> None:
    """This mirror's secrets into the restored settings where those left them out. A list's row gets its
    secret only when it is the same row (equal but for its secrets), never another row's at that place."""
    for key, secret in secrets.items():
        mine, own = restored.get(key), current.get(key)
        if secret is True:
            if key not in restored and key in current:
                restored[key] = own
        elif isinstance(secret, list):
            if isinstance(mine, list) and isinstance(own, list):
                for row, own_row in zip(mine, own, strict=False):  # rows added or gone: the rest still pair
                    if isinstance(row, dict) and isinstance(own_row, dict) and row == _strip(copy.deepcopy(own_row), secret[0]):
                        _put_back(row, own_row, secret[0])
        elif isinstance(mine, dict) and isinstance(own, dict):
            _put_back(mine, own, secret)

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
            _strip(instance_config, module_secrets(instance_config))
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
        _put_back(instance_config, current_instance, module_secrets(instance_config))

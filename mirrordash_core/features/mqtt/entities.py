# SPDX-License-Identifier: AGPL-3.0-only
"""Home Assistant entities from a module (ARCHITECTURE #42): what it may expose, and the values that pass.

A module declares what it exposes in its config_schema.json ("exposes": a sensor, a switch, a select, ...).
declarations reads and checks that list; module_components makes the discovery entries Home Assistant
needs for them (component builds one entry, also for the mirror's own). state_payload turns a value the
module sends into the state Home Assistant gets, and command_value a command Home Assistant sends into the
value the module gets; anything else is refused. The bridge (bridge.py) does the sending.
"""

import json
import logging
import math
import re
import urllib.parse

from mirrordash_core.forms import SAFE_KEY

logger = logging.getLogger("mirrordash.core.mqtt")


TEXT_MAX = 255  # Home Assistant's text entities and states

EXPOSES_MAX = 32  # entities a module may expose

EXPOSE_TYPES = {"sensor", "binary_sensor", "event", "text", "select", "number", "button", "switch"}

CONTROL_TYPES = {"text", "select", "number", "button", "switch"}

UNIT = re.compile(r"[\w %°/.²³µ-]{1,16}")

def checked_declaration(name, declared) -> dict | None:
    """One entry of a module's "exposes", as an entity {"type", "title", "control", ...} with only what is
    what it may be (it is its author's text), or None if it can't be one."""
    if not (isinstance(name, str) and SAFE_KEY.match(name) and isinstance(declared, dict)
            and declared.get("type") in EXPOSE_TYPES):
        return None
    kind = declared["type"]
    entity = {"type": kind, "title": str(declared.get("title") or name)[:64],
              "control": declared.get("control") is True and kind in CONTROL_TYPES}
    if kind == "select":
        entity["options"] = [option for option in declared.get("options") or []
                             if isinstance(option, str) and 0 < len(option) <= 64][:64]
        if not entity["options"]:
            return None
    elif kind == "number":
        try:
            low = float(declared.get("min", 0))
            high = float(declared.get("max", 100))
            step = float(declared.get("step", 1))
        except (TypeError, ValueError):
            return None
        if not all(map(math.isfinite, (low, high, step))) or low >= high or step <= 0:
            return None
        entity.update(min=low, max=high, step=step)
    elif kind == "text":
        max_length = declared.get("maxLength")
        entity["max"] = min(max_length, TEXT_MAX) if type(max_length) is int and max_length > 0 else TEXT_MAX
        entity["url"] = declared.get("format") == "url"
    elif kind == "event":
        entity["event_types"] = [event_type for event_type in declared.get("event_types") or []
                                 if isinstance(event_type, str) and SAFE_KEY.match(event_type)][:16]
        if not entity["event_types"]:
            return None
    elif kind == "sensor":
        entity.update({key: declared[key] for key in ("unit", "device_class")
                       if isinstance(declared.get(key), str) and UNIT.fullmatch(declared[key])})
    return entity

def declarations(schema: dict | None) -> dict[str, dict]:
    """A module's "exposes" (config_schema.json), its first EXPOSES_MAX entries that can be entities:
    {name: {"type", "title", "control", ...}} (checked_declaration)."""
    exposes = (schema or {}).get("exposes")
    if not isinstance(exposes, dict):
        return {}
    entities = {}
    for name, declared in list(exposes.items())[:EXPOSES_MAX]:
        if entity := checked_declaration(name, declared):
            entities[name] = entity
    return entities

def has_control_characters(text: str) -> bool:
    """A line break, a tab, an escape or DEL: none of them belongs in a text entity's value."""
    return any(ord(character) < 32 or ord(character) == 127 for character in text)

def on_off(payload: str) -> bool:
    """A switch command's ON or OFF as True or False; ValueError for anything else."""
    if payload not in ("ON", "OFF"):
        raise ValueError("not ON or OFF")
    return payload == "ON"

def command_value(entity: dict, payload: str):
    """What a command to entity carries, if it is something entity takes; ValueError if not."""
    kind = entity["type"]
    if kind == "switch" and payload in ("ON", "OFF"):
        return payload == "ON"
    if kind == "button" and payload == "PRESS":
        return None
    if kind == "select" and payload in entity["options"]:
        return payload
    if kind == "number":
        value = float(payload)
        if math.isfinite(value) and entity["min"] <= value <= entity["max"]:
            return int(value) if value.is_integer() and entity["step"].is_integer() else value
    if kind == "text" and len(payload) <= entity["max"] and not has_control_characters(payload):
        if not entity["url"]:
            return payload
        parts = urllib.parse.urlsplit(payload)
        if parts.scheme in ("http", "https") and parts.netloc:
            return payload
    raise ValueError(f"not a value for this {kind}")

def state_payload(entity: dict, value) -> str | None:
    """A module's value as entity's state, or None if it isn't one."""
    kind = entity["type"]
    number = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if kind in ("binary_sensor", "switch"):
        return ("ON" if value else "OFF") if isinstance(value, bool) else None
    if kind == "event":
        return json.dumps({"event_type": value}) if value in entity["event_types"] else None
    if kind == "select":
        return value if value in entity["options"] else None
    if kind == "number":
        return str(value) if number else None
    if kind in ("sensor", "text") and (number or isinstance(value, str)):
        text = str(value)
        return text if len(text) <= entity.get("max", TEXT_MAX) else None
    return None

def component(uid: str, platform: str, name: str, state: str | None, command: str | None = None, **more) -> dict:
    """One entity of a device's discovery."""
    entry = {"platform": platform, "unique_id": uid, "name": name, **more}
    if state:
        entry["state_topic"] = state
    if command:
        entry["command_topic"] = command
    return entry

def module_components(base: str, mirror_id: str, instance: str, entities: dict, control: set) -> dict:
    """The components of a shared module instance's device: its entities, the controls the owner allowed."""
    components = {}
    for name, entity in entities.items():
        topic = f"{base}/m/{instance}/{name}"
        platform = entity["type"]
        owner_allows_control = entity["control"] and name in control
        if platform == "button" and not owner_allows_control:
            continue
        if platform in CONTROL_TYPES and not owner_allows_control:  # shown, not run: what it is now
            platform = "binary_sensor" if platform == "switch" else "sensor"
        extra = {}
        if platform == entity["type"]:  # the entity's own settings only fit its own platform
            extra = {key: entity[key] for key in ("options", "min", "max", "step", "event_types") if key in entity}
        if platform == "sensor":
            for entity_key, discovery_key in (("unit", "unit_of_measurement"), ("device_class", "device_class")):
                if entity_key in entity:
                    extra[discovery_key] = entity[entity_key]
        state_topic = None if platform == "button" else f"{topic}/state"
        command_topic = f"{topic}/set" if owner_allows_control else None
        components[name] = component(f"{mirror_id}_{instance}_{name}", platform, entity["title"],
                                     state_topic, command_topic, **extra)
    return components

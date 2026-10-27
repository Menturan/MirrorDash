# Licensed under the PolyForm Noncommercial License 1.0.0.
"""MQTT (ARCHITECTURE #42): the mirror as a Home Assistant device, through the owner's own broker.

One connection from the core: device discovery (homeassistant/device/<id>/config for the mirror,
homeassistant/device/<id>/<instance>/config for each module instance the owner shares), the states, and the commands the owner allowed. MQTT says
nothing about who sent a message, so a command is only ever what it may be: off until the owner turns it
on, its value checked like any input from outside, never an admin action, at most COMMAND_RATE a second,
never one the broker kept (retained) and logged. Topics, under mirrordash/<id>:

  status                          online / offline (the last will)
  <key>/state, <key>/set          the mirror's own (screen, brightness, motion, temperature, humidity, lux,
                                  cpu_temperature, button)
  enabled/<instance>/state|set    a module instance on or off (control_modules)
  m/<instance>/<name>/state|set   what a module exposes (its config_schema.json's "exposes")

Instance ids are SAFE_NAME and names SAFE_KEY, so no topic gets a wildcard or another level from them.
"""

import asyncio
import collections
import json
import logging
import math
import re
import secrets
import ssl
import time
import urllib.parse

import aiomqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from paho.mqtt.subscribeoptions import SubscribeOptions

from mirrordash_core.config import get_core_version, load_config
from mirrordash_core.event_bus import event_bus
from mirrordash_core.features.modules.loader import SAFE_NAME
from mirrordash_core.forms import SAFE_KEY

logger = logging.getLogger("mirrordash.core.mqtt")

DISCOVERY = "homeassistant"
MAX_PACKET = 64 * 1024  # MQTT 5: the broker drops a bigger message before it reaches the mirror
MAX_QUEUED = 100  # messages waiting to be handled; more are dropped by the client, so a flood can't take memory
COMMAND_RATE = 10  # commands a second, all together; more are dropped
TEXT_MAX = 255  # Home Assistant's text entities and states
RETRY_FIRST, RETRY_MAX = 2, 60  # seconds between connection attempts, doubling
CPU_INTERVAL = 60
DISCOVERY_INTERVAL = 60  # the modules' declarations read again (an update may change them)
MODULE_CHANGE_INTERVAL = 5  # seconds between turning modules on or off (each change reloads them all)
EXPOSES_MAX = 32  # entities a module may expose
EXPOSE_TYPES = {"sensor", "binary_sensor", "event", "text", "select", "number", "button", "switch"}
CONTROL_TYPES = {"text", "select", "number", "button", "switch"}
UNIT = re.compile(r"[\w %°/.²³µ-]{1,16}")


CLIENT_LOG = logging.getLogger("mirrordash.core.mqtt.client")
CLIENT_LOG.setLevel(logging.ERROR)


def new_id() -> str:
    return f"mirrordash-{secrets.token_hex(4)}"


def new_client_id() -> str:
    return f"mirrordash-{secrets.token_hex(12)}"


def guard_packet_size(client: aiomqtt.Client) -> None:
    """paho reads whatever packet comes (up to 256 MB), whatever the MaximumPacketSize the mirror asked for:
    a broker that ignores it, or someone in between on plain MQTT, could fill the core's memory (200 MB
    measured). A packet over MAX_PACKET ends the connection instead, as a failed read.
    ponytail: paho's private _sock_recv and _in_packet (paho 2.1, pinned in uv.lock); test_mqtt.py fails if
    they change."""
    paho = client._client
    read = paho._sock_recv

    def guarded(size: int) -> bytes:
        packet = paho._in_packet
        if packet.get("have_remaining") and packet.get("remaining_length", 0) > MAX_PACKET:
            raise OSError(f"the broker sent a packet over {MAX_PACKET // 1024} kB")
        return read(size)
    paho._sock_recv = guarded


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


class MqttBridge:
    """The connection to the owner's broker (config["mqtt"]), kept up while it is on."""

    def __init__(self):
        self.status = "off"  # for the admin card
        self._task: asyncio.Task | None = None
        self._client: aiomqtt.Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._published: dict[str, str] = {}  # retained topic -> what was sent last
        self._pending: dict[str, tuple[str, bool]] = {}  # topic -> (payload, retain), sent on the next pass
        self._modules: dict[str, dict] = {}  # shared instance -> its declarations
        self._recent_command_times: collections.deque[float] = collections.deque(maxlen=COMMAND_RATE)
        self._refused_logged = 0.0
        self._refused = 0
        self._fingerprint = ""
        self._discovered_at = 0.0
        self._brightness: int | None = None  # waiting for the next pass (_apply_pending)
        self._brightness_at = 0.0
        self._enable: dict[str, bool] = {}
        self._enable_at = 0.0

    # The rest of the core

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        for event in ("hardware.motion", "hardware.climate", "hardware.light", "hardware.button"):
            event_bus.subscribe(event, self._on_event)
        await self.restart()

    async def stop(self) -> None:
        if self._client:  # a clean disconnect sends no last will: Home Assistant is told here
            try:
                await self._client.publish(f"{self._base()}/status", "offline", qos=1, retain=True, timeout=2)
            except aiomqtt.MqttError:
                pass
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self.status = "off"

    async def restart(self) -> None:
        """After its settings changed: connect again with them, or stay off."""
        await self.stop()
        cfg = load_config().get("mqtt", {})
        if cfg.get("enabled") and cfg.get("host") and cfg.get("id"):
            self._task = asyncio.create_task(self._run(), name="mqtt")

    async def forget(self) -> bool:
        """Turned off by the owner: Home Assistant removes the devices (an empty retained discovery), and
        the mirror shows offline. False if it wasn't connected, so they stay there (unavailable)."""
        client, removed = self._client, False
        if client:
            try:
                for topic in self._published:  # the devices, and every state it left on the broker
                    await client.publish(topic, b"", qos=1, retain=True)
                await client.publish(f"{self._base()}/status", "offline", qos=1, retain=True)
                removed = True
            except aiomqtt.MqttError as e:
                logger.warning(f"MQTT: couldn't remove the mirror from Home Assistant: {e}")
        self._published = {}
        await self.stop()
        return removed

    def module_state(self, instance: str, name, value) -> None:
        """A module instance's value of something it exposes (client.py): published if it is shared and the
        value is what the entity declares; the last one per entity, on the next pass."""
        entity = self._modules.get(instance, {}).get(name)
        payload = state_payload(entity, value) if entity else None
        if payload is not None:
            self._pending[f"{self._base()}/m/{instance}/{name}/state"] = (payload, entity["type"] != "event")

    # The connection

    def _base(self) -> str:
        return f"mirrordash/{load_config().get('mqtt', {}).get('id', 'mirrordash')}"

    def _client_for(self, cfg: dict) -> aiomqtt.Client:
        props = Properties(PacketTypes.CONNECT)
        props.MaximumPacketSize = MAX_PACKET
        tls = ssl.create_default_context() if cfg.get("tls") else None  # the broker's certificate checked
        client = aiomqtt.Client(
            cfg["host"], int(cfg.get("port") or (8883 if tls else 1883)), username=cfg.get("username") or None,
            password=cfg.get("password") or None, protocol=aiomqtt.ProtocolVersion.V5,
            # Its own secret, not the id every topic shows: another client must know it to take the connection
            # over (on a broker without use_username_as_clientid, which ignores it)
            identifier=cfg.get("client_id") or cfg["id"],
            clean_start=True,  # no session: nothing the broker queued while the mirror was away is run later
            will=aiomqtt.Will(f"{self._base()}/status", "offline", qos=1, retain=True), timeout=10,
            properties=props, tls_context=tls, max_queued_incoming_messages=MAX_QUEUED,
            logger=CLIENT_LOG)  # a flood would log every message it drops: our own lines say what matters
        guard_packet_size(client)
        return client

    async def _run(self) -> None:
        delay = RETRY_FIRST
        while True:
            self.status = "connecting"
            try:
                async with self._client_for(load_config().get("mqtt", {})) as client:
                    self._client, self._published, self._discovered_at = client, {}, 0.0
                    self.status, delay = "connected", RETRY_FIRST
                    logger.info("MQTT: connected")
                    await self._serve(client)
            except aiomqtt.MqttError as e:
                self.status = f"not connected: {e}"
                logger.warning(f"MQTT: {e}; trying again in {delay} s")
            except Exception as e:  # anything else ends this connection, never MQTT until the next restart
                self.status = "not connected: an error, see Logs"
                logger.error(f"MQTT: {e!r}; trying again in {delay} s", exc_info=True)
            finally:
                self._client = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, RETRY_MAX)

    async def _serve(self, client: aiomqtt.Client) -> None:
        base = self._base()
        await client.publish(f"{base}/status", "online", qos=1, retain=True)
        # retainHandling 2: a command the broker kept isn't sent on subscribing (the retain flag is checked too)
        commands = SubscribeOptions(qos=1, retainHandling=2)
        for topic in (f"{base}/+/set", f"{base}/enabled/+/set", f"{base}/m/+/+/set"):
            await client.subscribe(topic, options=commands)
        await client.subscribe(f"{DISCOVERY}/status", options=SubscribeOptions(qos=1, retainHandling=2))
        publisher = asyncio.create_task(self._publisher(client))
        try:
            async for message in client.messages:
                await self._on_message(str(message.topic), message.payload, message.retain)
        finally:
            publisher.cancel()
            await asyncio.gather(publisher, return_exceptions=True)

    async def _publish(self, client: aiomqtt.Client, topic: str, payload: str, retain: bool = True) -> None:
        if retain and self._published.get(topic) == payload:
            return
        await client.publish(topic, payload, qos=1, retain=retain)
        if retain:
            self._published[topic] = payload

    async def _publisher(self, client: aiomqtt.Client) -> None:
        """Every second: discovery when it changed, the states that changed."""
        cpu_at = 0.0
        while True:
            try:
                now = time.monotonic()
                await self._apply_pending(now)
                config = load_config()
                await self._discover(client, config, now)
                states = self._states(config, now - cpu_at >= CPU_INTERVAL)
                if now - cpu_at >= CPU_INTERVAL:
                    cpu_at = now
                pending, self._pending = self._pending, {}
                for topic, (payload, retain) in {**{t: (p, True) for t, p in states.items()}, **pending}.items():
                    await self._publish(client, topic, payload, retain)
            except aiomqtt.MqttError:
                raise
            except Exception as e:  # a bug here must not end the states: the next pass tries again
                logger.error(f"MQTT: publishing failed: {e!r}", exc_info=True)
            await asyncio.sleep(1)

    # What Home Assistant sees

    async def _discover(self, client: aiomqtt.Client, config: dict, now: float) -> None:
        """The devices' discovery, again when the settings or the hardware changed, every DISCOVERY_INTERVAL
        (an updated module's declarations) and after Home Assistant started (its birth message)."""
        from mirrordash_core.features.hardware import display
        from mirrordash_core.features.hardware.devices import (
            BUTTON_TYPES,
            PRESENCE_TYPES,
            PRESS_TYPES,
            find_device,
        )
        from mirrordash_core.features.modules.loader import find_entry_point
        from mirrordash_core.features.settings.schema import get_module_schema
        cfg, system, modules = config.get("mqtt", {}), config.get("system", {}), config.get("modules", {})
        fingerprint = json.dumps([cfg, system.get("devices"), display.brightness_supported,
                                  {i: m.get("module") for i, m in modules.items() if isinstance(m, dict)}], sort_keys=True, default=str)
        if fingerprint == self._fingerprint and now - self._discovered_at < DISCOVERY_INTERVAL:
            return
        self._fingerprint, self._discovered_at = fingerprint, now
        base, mid, version = self._base(), cfg["id"], get_core_version()
        control = cfg.get("control_screen") is True
        has = lambda *types: any(find_device(system, t) for t in types)  # noqa: E731
        comps = {
            "screen": component(f"{mid}_screen", "switch" if control else "binary_sensor", "Screen", f"{base}/screen/state",
                                f"{base}/screen/set" if control else None, **({} if control else {"device_class": "power"})),
            "cpu_temperature": component(f"{mid}_cpu_temperature", "sensor", "CPU temperature", f"{base}/cpu_temperature/state",
                                         device_class="temperature", unit_of_measurement="°C", entity_category="diagnostic"),
        }
        if display.brightness_supported is not False:
            comps["brightness"] = component(f"{mid}_brightness", "number" if control else "sensor", "Brightness",
                                            f"{base}/brightness/state", f"{base}/brightness/set" if control else None,
                                            unit_of_measurement="%", **({"min": 10, "max": 100, "step": 1} if control else {}))
        if has(*PRESENCE_TYPES):
            comps["motion"] = component(f"{mid}_motion", "binary_sensor", "Motion", f"{base}/motion/state", device_class="motion")
        if has("dht11"):
            comps["temperature"] = component(f"{mid}_temperature", "sensor", "Temperature", f"{base}/temperature/state",
                                             device_class="temperature", unit_of_measurement="°C")
            comps["humidity"] = component(f"{mid}_humidity", "sensor", "Humidity", f"{base}/humidity/state",
                                          device_class="humidity", unit_of_measurement="%")
        if has("light"):
            comps["lux"] = component(f"{mid}_lux", "sensor", "Light", f"{base}/lux/state", device_class="illuminance",
                                     unit_of_measurement="lx")
        for button in BUTTON_TYPES:
            if has(button):
                comps[button] = component(f"{mid}_{button}", "event", button.replace("_", " ").capitalize(),
                                          f"{base}/{button}/state", device_class="button", event_types=list(PRESS_TYPES))
        if cfg.get("control_modules") is True:
            for instance in modules:
                if SAFE_NAME.match(instance):
                    comps[f"enabled_{instance}"] = component(f"{mid}_enabled_{instance}", "switch", f"Module {instance}",
                                                             f"{base}/enabled/{instance}/state", f"{base}/enabled/{instance}/set",
                                                             entity_category="config")
        origin = {"name": "MirrorDash", "sw_version": version, "support_url": "https://github.com/Menturan/MirrorDash"}
        devices = {f"{DISCOVERY}/device/{mid}/config": {
            "device": {"identifiers": [mid], "name": "MirrorDash", "manufacturer": "MirrorDash", "sw_version": version},
            "origin": origin, "availability_topic": f"{base}/status", "components": comps}}

        shared = {}
        for instance, choice in (cfg.get("modules") or {}).items():
            module_cfg = modules.get(instance)
            if not (isinstance(choice, dict) and choice.get("share") is True and isinstance(module_cfg, dict)
                    and SAFE_NAME.match(instance) and module_cfg.get("enabled", True)):
                continue
            ep = find_entry_point(str(module_cfg.get("module", instance)))
            if not (entities := declarations(get_module_schema(ep) if ep else None)):
                continue
            shared[instance] = entities
            allowed = {n for n in choice.get("control") or [] if isinstance(n, str)}
            # Under the mirror's id (node id/object id): one ACL line, homeassistant/device/<id>/#, covers all
            devices[f"{DISCOVERY}/device/{mid}/{instance}/config"] = {
                "device": {"identifiers": [f"{mid}_{instance}"], "name": f"MirrorDash {instance}", "via_device": mid},
                "origin": origin, "availability_topic": f"{base}/status",
                "components": module_components(base, mid, instance, entities, allowed)}
        self._modules = shared
        # A value it sent just before the owner stopped sharing it must not follow the clearing below
        self._pending = {t: v for t, v in self._pending.items() if not t.startswith(f"{base}/m/") or t.split("/")[3] in shared}

        for topic, payload in devices.items():
            old = json.loads(self._published[topic])["components"] if self._published.get(topic) else {}
            new = payload["components"]
            # Home Assistant removes a component it is sent as its platform alone, and never changes one's
            # platform (a sensor that may now be controlled): those go first, then come back as they are now
            if gone := {n: {"platform": c["platform"]} for n, c in old.items() if new.get(n, {}).get("platform") != c["platform"]}:
                await self._publish(client, topic, json.dumps({**payload, "components": {**new, **gone}}))
            await self._publish(client, topic, json.dumps(payload))
        for topic in [t for t in self._published if t.startswith(f"{DISCOVERY}/") and t not in devices]:
            await self._publish(client, topic, "")  # a device no longer shared: Home Assistant removes it
            del self._published[topic]
        # What a module no longer shared said stays on the broker (retained) for any client to read: cleared,
        # once a connection for each entity it declares (an empty retained message deletes one)
        for instance, module_cfg in modules.items():
            if instance in shared or not (isinstance(module_cfg, dict) and SAFE_NAME.match(instance)):
                continue
            ep = find_entry_point(str(module_cfg.get("module", instance)))
            for name in declarations(get_module_schema(ep) if ep else None):
                await self._publish(client, f"{base}/m/{instance}/{name}/state", "")

    def _states(self, config: dict, cpu: bool) -> dict[str, str]:
        from mirrordash_core.features.hardware.devices import (
            PRESENCE_TYPES,
            find_device,
            gpio_inputs,
        )
        from mirrordash_core.features.power.display_power import display_power_manager
        base, cfg, system = self._base(), config.get("mqtt", {}), config.get("system", {})
        states = {f"{base}/screen/state": "ON" if display_power_manager.is_on else "OFF",
                  f"{base}/brightness/state": str(system.get("brightness", 100))}
        if any(find_device(system, t) for t in PRESENCE_TYPES):
            states[f"{base}/motion/state"] = "ON" if gpio_inputs.motion_active else "OFF"
        if cpu:
            from mirrordash_core.features.dashboard.telemetry import get_cpu_temperature
            if (temperature := get_cpu_temperature()) is not None:
                states[f"{base}/cpu_temperature/state"] = f"{temperature:.1f}"
        if cfg.get("control_modules") is True:
            for instance, module_cfg in config.get("modules", {}).items():
                if isinstance(module_cfg, dict) and SAFE_NAME.match(instance):
                    states[f"{base}/enabled/{instance}/state"] = "ON" if module_cfg.get("enabled", True) else "OFF"
        return states

    def _on_event(self, data) -> None:
        """A hardware event (event_bus, from any thread): its state for the next pass."""
        if self._loop and isinstance(data, dict):
            self._loop.call_soon_threadsafe(self._event_states, data)

    def _event_states(self, data: dict) -> None:
        base = self._base()
        number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)  # noqa: E731
        if "press" in data and isinstance(data.get("button"), str) and SAFE_KEY.match(data["button"]):
            self._pending[f"{base}/{data['button']}/state"] = (json.dumps({"event_type": str(data["press"])}), False)
        if isinstance(data.get("motion"), bool):
            self._pending[f"{base}/motion/state"] = ("ON" if data["motion"] else "OFF", True)
        for key, topic in (("temperature_c", "temperature"), ("humidity", "humidity"), ("lux", "lux")):
            if number(data.get(key)):
                self._pending[f"{base}/{topic}/state"] = (str(data[key]), True)

    # Commands

    async def _on_message(self, topic: str, payload, retain: bool) -> None:
        if topic == f"{DISCOVERY}/status":
            if payload == b"online":  # Home Assistant started: it wants the discovery again
                self._published = {t: p for t, p in self._published.items() if not t.startswith(DISCOVERY + "/")}
                self._discovered_at = 0.0
            return
        if retain:  # one the broker kept, delivered again at a reconnect: never carried out
            self._refuse(topic, "a retained command")
            return
        now = time.monotonic()
        if len(self._recent_command_times) == COMMAND_RATE and now - self._recent_command_times[0] < 1:
            self._refuse(topic, f"more than {COMMAND_RATE} commands a second")
            return
        self._recent_command_times.append(now)
        base = self._base() + "/"
        try:
            text = payload.decode("utf-8") if isinstance(payload, (bytes, bytearray)) else str(payload)
            what = await self._command(topic.removeprefix(base).split("/") if topic.startswith(base) else [], text)
        except (ValueError, PermissionError) as e:
            self._refuse(topic, str(e))
            return
        except Exception as e:  # a module going away meanwhile, say: one command never ends the connection
            logger.error(f"MQTT: {topic!r} failed: {e!r}")
            return
        logger.info(f"MQTT: {what} ({topic!r})")  # repr: a topic may hold a line break, which would forge a log line

    def _refuse(self, topic: str, why: str) -> None:
        """Logged, but at most every 10 s: a flood of refused commands would otherwise flood the log."""
        self._refused += 1
        if time.monotonic() - self._refused_logged >= 10:
            logger.warning(f"MQTT: refused {topic!r}: {why}" + (f" (and {self._refused - 1} more)" if self._refused > 1 else ""))
            self._refused_logged, self._refused = time.monotonic(), 0

    async def _command(self, parts: list[str], payload: str) -> str:
        """Carry out one command, if the owner allowed it and its value is one it takes; what was done."""
        cfg = load_config().get("mqtt", {})
        match parts:
            case ["screen", "set"] if cfg.get("control_screen") is True:
                if payload not in ("ON", "OFF"):
                    raise ValueError("not ON or OFF")
                from mirrordash_core.features.power.routes import update_screen_state
                await update_screen_state({"state": payload.lower()})
                return f"screen {payload}"
            case ["brightness", "set"] if cfg.get("control_screen") is True:
                if not (payload.isascii() and payload.isdigit()) or not 10 <= int(payload) <= 100:
                    raise ValueError("not a brightness from 10 to 100")
                self._brightness = int(payload)  # the last one, on the next pass (a slider sends many)
                return f"brightness {payload}"
            case ["enabled", instance, "set"] if cfg.get("control_modules") is True:
                if payload not in ("ON", "OFF"):
                    raise ValueError("not ON or OFF")
                if not isinstance(load_config().get("modules", {}).get(instance), dict):
                    raise ValueError("no such module instance")
                self._enable[instance] = payload == "ON"  # together, at most every MODULE_CHANGE_INTERVAL
                return f"module {instance!r} {payload}"
            case ["m", instance, name, "set"]:
                entity = self._modules.get(instance, {}).get(name)
                choice = (cfg.get("modules") or {}).get(instance) or {}
                if not (entity and entity["control"] and name in (choice.get("control") or [])):
                    raise PermissionError("not something the owner lets Home Assistant control")
                value = command_value(entity, payload)
                from mirrordash_core.features.modules.client import module_host
                if not await module_host.control(instance, name, value):
                    raise ValueError("the module isn't running")
                return f"{instance} {name} = {payload[:80]!r}"
        raise PermissionError("not something the owner lets Home Assistant control")

    async def _apply_pending(self, now: float) -> None:
        """The commands that are heavy to carry out, coalesced: brightness writes the settings to the card
        (at most once a second), a module on or off reloads every module (all waiting ones at once, at most
        every MODULE_CHANGE_INTERVAL), so a flood can't wear the card or keep the modules restarting."""
        if self._brightness is not None and now - self._brightness_at >= 1:
            value, self._brightness, self._brightness_at = self._brightness, None, now
            from mirrordash_core.system_settings import update_system_settings
            await update_system_settings(settings={"brightness": value})
        if self._enable and now - self._enable_at >= MODULE_CHANGE_INTERVAL:
            changes, self._enable, self._enable_at = self._enable, {}, now
            from mirrordash_core.features.modules.routes import set_instances_enabled
            await set_instances_enabled(changes)


mqtt_bridge = MqttBridge()

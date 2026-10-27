"""The real MqttBridge against the brokers run.sh starts: what Home Assistant sees, what it may do, and what
another client on the broker (as Zigbee2MQTT) can't, with and without the admin card's ACL. The screen, the
modules and the module host are stand-ins that record what they were asked."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiomqtt

from mirrordash_core.features.mqtt import bridge as mqtt

TV = {"exposes": {"now_playing": {"type": "sensor"}, "channel": {"type": "select", "options": ["SVT1", "SVT2"], "control": True},
                  "play_url": {"type": "text", "format": "url", "control": True}}}
BASE = "mirrordash/check"
CLIENT_ID = "mirrordash-0123456789abcdef01234567"  # the bridge's secret one (routes.py new_client_id)


def config(port: int) -> dict:
    return {"mqtt": {"enabled": True, "id": "check", "host": "127.0.0.1", "port": port, "username": "mirror",
                     "password": "pw-mirror", "client_id": CLIENT_ID, "control_screen": True, "control_modules": True,
                     "modules": {"tv": {"share": True, "control": ["channel"]}}},
            "modules": {"tv": {"module": "tv"}}, "system": {}}


def client(port: int, user: str, ident: str | None = None) -> aiomqtt.Client:
    return aiomqtt.Client("127.0.0.1", port, username=user, password=f"pw-{user}", identifier=ident,
                          protocol=aiomqtt.ProtocolVersion.V5)


async def until(check, seconds: float = 5) -> bool:
    for _ in range(int(seconds * 20)):
        if check():
            return True
        await asyncio.sleep(0.05)
    return False


async def run(name: str, port: int) -> list[str]:
    bad, seen = [], []
    screen, enabled, control = AsyncMock(), AsyncMock(return_value=True), AsyncMock(return_value=True)
    b = mqtt.MqttBridge()

    def expect(what: str, ok: bool) -> None:
        print(f"  {'ok ' if ok else 'BAD'} {what}")
        if not ok:
            bad.append(f"{name}: {what}")

    async def watch():
        async with client(port, "watch") as w:
            await w.subscribe("homeassistant/device/check/#")
            await w.subscribe(f"{BASE}/#")
            async for m in w.messages:
                seen.append((str(m.topic), bytes(m.payload)))

    async def send(user: str, topic: str, payload: bytes, retain: bool = False):
        async with client(port, user) as c:
            await c.publish(topic, payload, qos=1, retain=retain)
        await asyncio.sleep(0.4)

    with patch.object(mqtt, "load_config", return_value=config(port)), \
         patch("mirrordash_core.features.power.routes.update_screen_state", screen), \
         patch("mirrordash_core.features.modules.routes.set_instances_enabled", enabled), \
         patch("mirrordash_core.features.modules.client.module_host.control", control), \
         patch("mirrordash_core.features.modules.loader.find_entry_point", return_value=SimpleNamespace()), \
         patch("mirrordash_core.features.settings.schema.get_module_schema", return_value=TV):
        watcher = asyncio.create_task(watch())
        await asyncio.sleep(0.3)
        await b.start()
        expect("connects", await until(lambda: b.status == "connected"))
        discovery = lambda topic: [json.loads(p) for t, p in seen if t == topic and p]  # noqa: E731
        expect("the mirror's discovery reaches Home Assistant",
               await until(lambda: discovery("homeassistant/device/check/config")))
        expect("the module's discovery, with only the allowed control",
               await until(lambda: discovery("homeassistant/device/check/tv/config"))
               and "command_topic" in discovery("homeassistant/device/check/tv/config")[-1]["components"]["channel"]
               and "command_topic" not in discovery("homeassistant/device/check/tv/config")[-1]["components"]["play_url"])
        expect("the screen's state", await until(lambda: any(t == f"{BASE}/screen/state" for t, _ in seen)))
        b.module_state("tv", "now_playing", "News")
        expect("a module's state", await until(lambda: (f"{BASE}/m/tv/now_playing/state", b"News") in seen))

        await send("ha", f"{BASE}/screen/set", b"OFF")
        expect("Home Assistant turns the screen off", screen.await_count == 1)
        await send("zigbee", f"{BASE}/screen/set", b"ON")
        expect("another client's command " + ("is carried out: no ACL, MQTT can't tell" if name == "open" else "is refused by the ACL"),
               screen.await_count == (2 if name == "open" else 1))

        await send("ha", f"{BASE}/m/tv/channel/set", b"SVT2")
        expect("an allowed module control reaches the module", control.await_args_list[-1].args == ("tv", "channel", "SVT2"))
        await send("ha", f"{BASE}/m/tv/play_url/set", b"https://example.com/x")
        await send("ha", f"{BASE}/m/tv/channel/set", b"x" * 100_000)
        expect("a control the owner didn't allow, and a 100 kB one, don't", control.await_count == 1)

        await send("ha", f"{BASE}/enabled/tv/set", b"OFF", retain=True)
        await asyncio.sleep(mqtt.MODULE_CHANGE_INTERVAL + 1)  # the live one is carried out on a pass
        expect("Home Assistant turns a module off", enabled.await_args_list[-1].args == ({"tv": False},))
        enabled.reset_mock()
        await b.restart()  # a reconnect: the broker would deliver the retained command again
        await until(lambda: b.status == "connected")
        await asyncio.sleep(1.5)
        expect("a retained command isn't carried out after a reconnect", not enabled.await_count)
        await send("ha", f"{BASE}/enabled/tv/set", b"", retain=True)

        before = len(discovery("homeassistant/device/check/config"))
        await send("ha", "homeassistant/status", b"online")
        expect("Home Assistant's restart gets the discovery again",
               await until(lambda: len(discovery("homeassistant/device/check/config")) > before))

        async def takeover(ident: str) -> bool:
            """Does another client connecting with ident throw the mirror off?"""
            await until(lambda: b.status == "connected")
            connection = b._client
            try:
                async with client(port, "zigbee", ident):
                    await asyncio.sleep(1)
            except aiomqtt.MqttError:
                pass
            return b._client is not connection

        expect("another client with the id the topics show can't throw the mirror off", not await takeover("check"))
        expect("one that knows its secret client ID " + ("can (no ACL)" if name == "open" else "can't"),
               await takeover(CLIENT_ID) == (name == "open"))

        await until(lambda: b.status == "connected")
        await b.forget()
        await asyncio.sleep(0.5)
        expect("turned off: Home Assistant removes the devices and sees it offline",
               ("homeassistant/device/check/config", b"") in seen and (f"{BASE}/status", b"offline") in seen)
        watcher.cancel()
    return bad


async def main():
    bad = []
    for name, port in (("open", 18830), ("hard", 18831)):
        print(f"== {name} broker")
        bad += await run(name, port)
    print("ALL GOOD" if not bad else "FAILED:\n" + "\n".join(bad))
    raise SystemExit(1 if bad else 0)


asyncio.run(main())

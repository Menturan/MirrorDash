"""As pi in the sandbox-check container (run.sh): the core's client against the real unit. Asserts what each
module's user may and may not do, and prints what it saw."""
import asyncio
import base64
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

os.environ["MIRRORDASH_MODULE_SOCKET"] = "/run/mirrordash-modules/host.sock"
os.environ["MIRRORDASH_MODULE_DIR"] = "/storage/mirrordash/modules"
from mirrordash_core.features.modules import client  # noqa: E402

M = "/storage/mirrordash/modules"
config = {"modules": {"a": {"module": "md_probe", "other": "b"}, "b": {"module": "md_probe", "other": "a"}},
          "globals": {}}
client.load_config = lambda: config
client.manager = MagicMock(broadcast=AsyncMock())
client.LAN_HELPER = "/nonexistent"  # it would set IPAddressAllow to the stub alone, which isn't in here
client.RESTART_DELAY = 1


async def until(check, t=60):
    for _ in range(int(t / 0.2)):
        if check():
            return
        await asyncio.sleep(0.2)
    raise AssertionError("timed out")


async def who(core, instance):
    reply = await core.call_route({"instance": instance, "method": "GET", "path": "/who"})
    return json.loads(base64.b64decode(reply["body"]))


def sh(command):
    return subprocess.run(["bash", "-c", command], capture_output=True, text=True).stdout.strip()


async def main():
    core = client.ModuleHost()
    await core.start_modules()
    await until(lambda: core.running == ["a", "b"])
    a, b = await who(core, "a"), await who(core, "b")
    print("a:", json.dumps(a), "\nb:", json.dumps(b))
    assert {a["uid"], b["uid"]} == {70100, 70101} and a["groups"] == b["groups"]
    for w in (a, b):
        assert all(int(w["caps"][k], 16) == 0 for k in ("CapInh", "CapPrm", "CapEff", "CapAmb")), w["caps"]
        assert w["setuid0"] == w["read_other"] == w["list_other"] == w["list_modules"] == "PermissionError"
        assert w["write_site"] == w["write_code"] == w["read_uids"] == "PermissionError" and w["parent_mem"] != "OPENED"
        assert w["write_own"] == "ok" and w["oom"] == "500" and 200 < w["threads"] < 256  # TASKS
    assert a["append_restored"] == "ok"  # what pi restored was given back to the module
    print(sh(f"stat -c '%u:%g %A %n' {M}/site_a {M}/pkgs {M}/pkgs/md-probe_1/md_probe/plugin.py {M}/data/*/*"))

    checks = (await core.diagnose())["checks"]
    assert not [w for w, r in checks if r], [w for w, r in checks if r]
    print(f"self-check: 0 of {len(checks)} open")

    async with core.packages("/storage/mirrordash/venv/bin/python") as site:
        code, err = await site.pip("install", "six")
        assert code == 0, err
        assert (await site.check(["md_probe"]))[0] == 0
    link = os.readlink(f"{M}/site")
    print(sh(f"stat -c '%u %A %n' {M}/pkgs/six_1/six.py {M}/pkgs/six_1/__pycache__/six* {M}/{link}/*"))
    assert os.stat(f"{M}/pkgs/six_1/six.py").st_uid == 70003 and os.readlink(f"{M}/{link}/six") == "../pkgs/six_1"

    # A module's build, as its own user, reaches no other module's folder nor the links
    probe = Path(f"{M}/pkgs/md-probe_1/md_probe/plugin.py")
    before = probe.read_text()
    try:
        async with core.packages("/storage/mirrordash/venv/bin/python") as site:
            code, err = await site.pip("install", "/test/evil")
            raise RuntimeError(err)
    except RuntimeError as e:
        evil = json.loads(str(e).split("EVIL ", 1)[1].splitlines()[0])
    print("evil build:", json.dumps(evil))
    assert evil.pop("uid") == 70004 and set(evil.values()) == {"PermissionError"}, evil
    assert probe.read_text() == before
    assert not os.path.exists(f"{M}/site_a/old_pkg")  # an older site's leftovers went with the change
    print("a module's build can't change another module")

    pids = (a["pid"], b["pid"])
    config["modules"]["leak"] = {"module": "md_probe", "leak": True}
    await core.reload_modules()
    await until(lambda: "leak" in core.paused, 120)
    assert ((await who(core, "a"))["pid"], (await who(core, "b"))["pid"]) == pids
    assert sh("systemctl is-active mirrordash-modules") == "active"
    print("a leak: killed alone and paused; a and b ran on")

    config["modules"]["b"]["note"] = "changed"
    await core.reload_modules()
    await until(lambda: "b" in core.running and str(b["pid"]) not in (core._instances["b"].html or str(b["pid"])))
    assert (await who(core, "a"))["pid"] == a["pid"]
    print("a changed setting restarted its instance alone")
    await core.stop_modules()
    await asyncio.sleep(7)
    assert sh("ps -eo uid= | awk '$1>=70000' | wc -l") == "0"
    print("ALL GOOD")

asyncio.run(main())

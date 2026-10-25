import os
import shutil
import tempfile
from pathlib import Path

import pytest

# Always resolve test config to a throwaway copy of the repo's config.json.
# This keeps tests away from the developer's ~/.mirrordash/data/config.json (real passwords
# and settings) and from the tracked config.json, which unmocked saves used to rewrite.
_test_config = Path(tempfile.mkdtemp(prefix="mirrordash-test-")) / "config.json"
shutil.copy(Path(__file__).parent.parent / "config.json", _test_config)
os.environ.setdefault("MIRRORDASH_CONFIG_PATH", str(_test_config))


@pytest.fixture(autouse=True)
def _clear_remote_json_cache():
    """Update checks are cached per URL; start every test without earlier answers."""
    from mirrordash_core import fetch
    fetch._remote_json_cache.clear()


@pytest.fixture(autouse=True)
def _fresh_logins(monkeypatch):
    """Every test starts logged out and with no wrong passwords counted against its address."""
    from mirrordash_core import admin
    monkeypatch.setattr(admin, "_sessions", {})
    monkeypatch.setattr(admin, "_failures", {})


@pytest.fixture
def host_here():
    """host_here(path): the module host's control process as a task, serving path, with the processes the
    forkserver would make as tasks of this process too (no fork, no user each), so a test's monkeypatches
    reach the modules."""
    import asyncio

    from mirrordash_core.features.modules import host
    children = set()

    def spawn(kind, uid, instance, socks):
        children.add(asyncio.get_running_loop().create_task(host.CHILDREN[kind](*socks, instance)))
    return lambda path: asyncio.create_task(host.serve(path, spawn))


@pytest.fixture(autouse=True)
def _not_on_a_mirror(monkeypatch):
    """UV_PYTHON_INSTALL_DIR means "on a mirror" to the updates (a Python patch upgrade first); CI's
    setup-uv sets it too. Tests of the mirror's path set it themselves."""
    monkeypatch.delenv("UV_PYTHON_INSTALL_DIR", raising=False)


@pytest.fixture(autouse=True)
def _never_restart_the_test_run(monkeypatch):
    """run_restart() SIGTERMs its own process; a test that reaches it must not end the run."""
    real_kill = os.kill
    monkeypatch.setattr(os, "kill", lambda pid, sig: None if pid == os.getpid() else real_kill(pid, sig))


@pytest.fixture
def module_site(monkeypatch):
    """module_host.packages() without a module host: each uv pip change and module check is recorded in
    `calls` as ("pip", *args) or ("check", *names) and answered by `answer(op, args)` ((code, err),
    success by default); `kept` tells whether the change was switched in, `fresh` and `python` how it began."""
    import contextlib
    from types import SimpleNamespace

    from mirrordash_core.features.modules.client import module_host
    site = SimpleNamespace(calls=[], kept=None, fresh=None, python=None, answer=lambda op, args: (0, ""))

    async def step(op, *args):
        site.calls.append((op, *args))
        return site.answer(op, args)

    @contextlib.asynccontextmanager
    async def packages(python=None, fresh=False):
        site.python, site.fresh = python, fresh
        try:
            yield SimpleNamespace(pip=lambda *args, timeout=600: step("pip", *args),
                                  check=lambda names: step("check", *names))
        except BaseException:
            site.kept = False
            raise
        site.kept = True

    monkeypatch.setattr(module_host, "packages", packages)
    return site


class Answer:
    """What a route would have answered: status_code and json(), from calling the function itself."""
    def __init__(self, status_code, body):
        self.status_code, self._body = status_code, body

    def json(self):
        return self._body


def call(fn, *args, **kwargs) -> Answer:
    """Call an async feature function as the admin panels do (they don't go over HTTP)."""
    import asyncio

    from fastapi import HTTPException
    try:
        return Answer(200, asyncio.run(fn(*args, **kwargs)))
    except HTTPException as e:
        return Answer(e.status_code, {"detail": e.detail})

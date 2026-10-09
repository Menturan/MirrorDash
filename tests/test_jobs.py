import asyncio

import pytest
from fastapi import HTTPException

from mirrordash_core import admin as admin_shared
from mirrordash_core.admin import job_response, job_status, start_job


@pytest.fixture(autouse=True)
def _reset_job():
    admin_shared._job.update(id=None, state="idle", error="")


def test_job_lifecycle_success_failure_and_single_flight():
    async def scenario():
        gate = asyncio.Event()

        async def slow_ok():
            await gate.wait()

        job_id = start_job(slow_ok)
        await asyncio.sleep(0)
        assert job_status()["id"] == job_id and job_status()["state"] == "running"
        assert start_job(slow_ok) is None  # only one package operation at a time
        gate.set()
        await asyncio.sleep(0.01)
        assert job_status()["state"] == "restarting"

        async def fails():
            raise HTTPException(status_code=500, detail="Upgrade failed: boom")

        start_job(fails)
        await asyncio.sleep(0.01)
        assert job_status()["state"] == "failed"
        assert job_status()["error"] == "Upgrade failed: boom"
        assert job_status()["boot_id"] == admin_shared.BOOT_ID

    asyncio.run(scenario())


def test_job_response_is_a_page_event_not_a_script():
    import json
    r = job_response("abc", "t", "Installing <b>x</b>", "ok")
    assert r.body == b""  # nothing is injected into the page
    follow = json.loads(r.headers["HX-Trigger-After-Swap"])["md-follow"]
    assert follow["jobId"] == "abc" and follow["message"] == "Installing <b>x</b>"


def test_job_response_when_busy():
    import json
    r = job_response(None, "t", "m", "ok")
    assert "still running" in json.loads(r.headers["HX-Trigger-After-Swap"])["md-notify"]["message"]

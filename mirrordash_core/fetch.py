# SPDX-License-Identifier: AGPL-3.0-only

import asyncio
import json
import logging
import time
import urllib.request

logger = logging.getLogger("mirrordash.core.fetch")


# ponytail: in-memory, unbounded cache keyed by URL (one entry per installed module and the
# core package). Failures are cached too, so a module that isn't on PyPI isn't re-queried on
# every page view; the cost is that an update can show up to TTL late. A restart clears it.
REMOTE_JSON_TTL = 600.0


_remote_json_cache: dict[str, tuple[float, object, float]] = {}  # url: (monotonic, data, wall-clock time)


async def fetch_json_cached(url: str, headers: dict | None = None, max_age: float = REMOTE_JSON_TTL) -> object | None:
    """GET a JSON document (PyPI/GitHub update checks), cached for max_age seconds. None on error."""
    hit = _remote_json_cache.get(url)
    if hit and time.monotonic() - hit[0] < max_age:
        return hit[1]

    def _get():
        req = urllib.request.Request(url, headers={"User-Agent": "MirrorDash/1.0", **(headers or {})})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        data = await asyncio.to_thread(_get)
    except Exception:
        data = None
    _remote_json_cache[url] = (time.monotonic(), data, time.time())
    return data


def answered_at(url: str) -> float | None:
    """When fetch_json_cached last asked for url (seconds since 1970), or None if it never did."""
    hit = _remote_json_cache.get(url)
    return hit[2] if hit else None

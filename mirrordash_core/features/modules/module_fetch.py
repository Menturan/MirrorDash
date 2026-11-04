# SPDX-License-Identifier: AGPL-3.0-only
"""A module's self.fetch and self.fetch_json: HTTP calls with a saved last good answer (ARCHITECTURE #23, #29).

_make_fetch builds them for each instance (loader._inject_module_helpers). Every answer is saved in the
instance's cache_dir, so a failed call shows the last good answer; while the screen is off a fetch waits
until it is on again; and in a preview every fetch answers from the module's sample answers instead.
"""

import asyncio
import fnmatch
import functools
import hashlib
import http.client
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from email.message import Message

from mirrordash_core.config import get_core_version

logger = logging.getLogger("mirrordash.core.modules")


def _request_body(json_body: object, form_data: dict | bytes | None) -> bytes | None:
    """What fetch sends: json= as JSON, data= as a form (a dict) or as it is (bytes)."""
    if json_body is not None:
        return json.dumps(json_body).encode()
    return urllib.parse.urlencode(form_data).encode() if isinstance(form_data, dict) else form_data


def _cache_file_for(cache_dir: str | None, method: str, url: str, body: bytes | None) -> str | None:
    """Where the last good answer to this exact request is kept (none without a cache_dir)."""
    if not cache_dir:
        return None
    request_hash = hashlib.sha256(f"{method} {url}\n".encode() + (body or b"")).hexdigest()[:16]
    return os.path.join(cache_dir, f"fetch-{request_hash}")


class Answer(tuple):
    """What fetch gives: (data, error) to unpack as before, and .fetched_at, when data was downloaded
    (a datetime; None without data). A saved answer keeps the time it was downloaded.
    .headers: this call's answer's headers, read case-insensitively (.get("Retry-After")), an error's
    too (a 429's Retry-After), while data is then the last good answer; empty when no call was made (a
    saved answer reused, the preview's samples) or none came back (offline)."""

    def __new__(cls, data, error, fetched_at: float | None = None, headers: Message | None = None):
        answer = super().__new__(cls, (data, error))
        answer.fetched_at = datetime.fromtimestamp(fetched_at) if data is not None and fetched_at else None
        answer.headers = headers if headers is not None else Message()
        return answer


def _read_cached_answer(cache_file: str | None, parse_answer, max_age: float | None = None):
    """(The saved answer, parsed; when it was downloaded); (None, None) when there is none, it doesn't
    parse, or it is older than max_age."""
    try:
        saved_at = os.path.getmtime(cache_file)
        if max_age is not None and time.time() - saved_at >= max_age:
            return None, None
        with open(cache_file, "rb") as f:
            return parse_answer(f.read()), saved_at
    except (TypeError, OSError, ValueError):  # no cache_dir, nothing saved yet, or a broken file
        return None, None


def _save_answer(cache_file: str | None, raw_answer: bytes, module_name: str, where: str) -> None:
    """Keep a good answer for when the next fetch fails; written whole or not at all."""
    if not cache_file:
        return
    try:
        with open(cache_file + ".tmp", "wb") as f:
            f.write(raw_answer)
        os.replace(cache_file + ".tmp", cache_file)
    except OSError as e:
        logger.debug(f"{module_name}: could not cache {where}: {e}")


def _make_fetch(cache_dir: str | None, module_name: str, awake: asyncio.Event, keep_running: bool = False,
                default_max_age=lambda: None, fixtures: dict[str, bytes] | None = None):
    """A module's `self.fetch` and `self.fetch_json` -> (answer, error), an Answer: .fetched_at says
    when the data was downloaded, also for a saved answer (a card's "updated 10:42"); .headers are this
    call's answer's (a 429's Retry-After), the module's own, so handed over like the body but never logged.

    `await self.fetch(url, method="GET", headers=..., params=..., json=..., data=..., timeout=10)` gives the
    raw bytes (RSS, ICS, XML); `self.fetch_json(...)` takes the same arguments and gives the parsed JSON.
    params go in the query; json= is sent as a JSON body, data= as a form (dict) or as is (bytes).

    While the screen is off (awake cleared) it waits until the screen is back on (unless keep_running), so
    a sleeping mirror calls no APIs and a fetch that fell due in the dark happens once, on waking.
    ponytail: only fetches through here sleep; a module with its own HTTP client keeps polling.

    error is None, "rejected" (401/403: usually the API key), "offline" (no answer), "http <code>" or
    "invalid" (fetch_json: not JSON). On an error, answer is the last good one for the same method, URL
    and body (kept in the module's cache_dir), or None.
    ponytail: no retry or backoff; the module's own interval is the retry. Upgrade path: backoff here.

    max_age=seconds: a saved answer younger than that comes back as fresh (error None) without a call, so
    saving a setting or a restart doesn't fetch everything again. Left out, it is default_max_age() (the
    module's interval), so a paid API keeps to its schedule by itself; max_age=0 always calls. ponytail: after a restart the next call
    can come up to two intervals after the last one, once; the cache file's mtime is the clock (and
    fetched_at), so a Pi booting on an old time may make one extra call.

    fixtures (the admin's preview, preview_fixtures): every fetch answers from the module's sample answers,
    the first pattern matching host + path, else "offline"; never the network, the cache or a wait.
    """
    user_agent = f"MirrorDash/{get_core_version()}"

    async def fetch(url: str, *, parse=None, method: str = "GET", headers: dict | None = None,
                    params: dict | None = None, json: object = None, data: dict | bytes | None = None,
                    timeout: float = 10, max_age: float | None = None) -> Answer:
        if json is not None and data is not None:
            raise ValueError("fetch: pass json= or data=, not both")
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        body = _request_body(json, data)
        if max_age is None:
            max_age = default_max_age()
        parse_answer = parse or (lambda raw: raw)
        url_parts = urllib.parse.urlsplit(url)
        where = f"{method} {url_parts.hostname}"  # only the host: user:password@, path (a Google Calendar private-… link), query, headers and body can hold keys
        cache_file = _cache_file_for(cache_dir, method, url, body)

        if fixtures is not None:
            sample = next((raw for pattern, raw in fixtures.items()
                           if fnmatch.fnmatchcase(url_parts.netloc + url_parts.path, pattern)), None)
            if sample is None:
                return Answer(None, "offline")
            try:
                return Answer(parse_answer(sample), None, time.time())
            except ValueError:
                return Answer(None, "invalid")

        if max_age:
            saved, saved_at = _read_cached_answer(cache_file, parse_answer, max_age)
            if saved is not None:
                return Answer(saved, None, saved_at)
        if not keep_running:
            await awake.wait()

        request_headers = {"User-Agent": user_agent,
                           **({"Accept": "application/json"} if parse else {}),
                           **({"Content-Type": "application/json"} if json is not None else {}),
                           **(headers or {})}

        def download() -> tuple[bytes, Message]:
            request = urllib.request.Request(url, data=body, method=method, headers=request_headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(), response.headers

        response_headers = None  # offline: no answer, so no headers
        try:
            raw_answer, response_headers = await asyncio.to_thread(download)
            answer = parse_answer(raw_answer)
        except urllib.error.HTTPError as e:
            error, response_headers = "rejected" if e.code in (401, 403) else f"http {e.code}", e.headers
        except (urllib.error.URLError, OSError, http.client.HTTPException):  # timeouts, cut-off answers
            error = "offline"
        except ValueError:  # fetch_json: not JSON, or not UTF-8
            error = "invalid"
        else:
            _save_answer(cache_file, raw_answer, module_name, where)
            return Answer(answer, None, time.time(), response_headers)

        logger.warning(f"{module_name}: fetching {where} failed ({error}); showing the last answer if there is one")
        saved, saved_at = _read_cached_answer(cache_file, parse_answer)
        return Answer(saved, error, saved_at, response_headers)

    return (functools.partial(fetch, parse=None),
            functools.partial(fetch, parse=lambda raw: json.loads(raw.decode("utf-8"))))



"""fetch.fetch_json_cached and what it remembers about GitHub's rate limit (urllib is mocked, no network)."""
import asyncio
import io
import json
import urllib.error

from mirrordash_core import fetch

URL = "https://api.github.com/x"


def http_error(code, headers):
    return urllib.error.HTTPError(URL, code, "no", headers, io.BytesIO(b""))


def ask(monkeypatch, outcome):
    fetch._remote_json_cache.clear()
    fetch._rate_limit_resets.clear()

    def urlopen(*args, **kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(json.dumps(outcome).encode())

    monkeypatch.setattr(fetch.urllib.request, "urlopen", urlopen)
    return asyncio.run(fetch.fetch_json_cached(URL, max_age=0))


def test_a_used_up_limit_is_remembered(monkeypatch):
    error = http_error(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1793012400"})
    assert ask(monkeypatch, error) is None
    assert fetch.rate_limit_reset(URL) == 1793012400.0


def test_retry_after_is_remembered(monkeypatch):
    monkeypatch.setattr(fetch.time, "time", lambda: 1000.0)
    ask(monkeypatch, http_error(429, {"retry-after": "120"}))
    assert fetch.rate_limit_reset(URL) == 1120.0


def test_other_failures_are_no_rate_limit(monkeypatch):
    for outcome in (http_error(403, {}), http_error(403, {"x-ratelimit-remaining": "5", "x-ratelimit-reset": "1"}),
                    http_error(500, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1"}), TimeoutError()):
        assert ask(monkeypatch, outcome) is None
        assert fetch.rate_limit_reset(URL) is None


def test_a_later_success_forgets_the_limit(monkeypatch):
    ask(monkeypatch, http_error(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1793012400"}))
    assert fetch.rate_limit_reset(URL) == 1793012400.0
    assert ask(monkeypatch, [1]) == [1]
    assert fetch.rate_limit_reset(URL) is None

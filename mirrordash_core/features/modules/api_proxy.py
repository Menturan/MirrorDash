# SPDX-License-Identifier: AGPL-3.0-only
"""/api/modules/<instance>/...: calls to a module's own routes (ARCHITECTURE #40), passed to its process.

A module can declare HTTP routes (its `router`). They run in the module's sandboxed process, never in the
core, so a call here is forwarded over the instance's connection (client.ModuleHost.call_route) and its
answer comes back. On the way in the admin session and password are dropped; on the way out nothing may
set a cookie on the mirror's site or act as the admin page.
"""

import base64
import logging

from starlette.responses import Response
from starlette.routing import get_route_path
from starlette.websockets import WebSocketClose

from mirrordash_core.features.modules import client

logger = logging.getLogger("mirrordash.core.modules.client")

BODY_LIMIT = 1024 * 1024  # a call to a module's route
# Headers that never reach a module (the admin session, the admin password) or come back from one
# (a cookie on the mirror's own site, framing the core's answer, a service worker over the whole site,
# wiping the site's logins)
REQUEST_HEADERS_DROPPED = {"cookie", "x-api-key", "host", "content-length"}
RESPONSE_HEADERS_DROPPED = {"set-cookie", "content-length", "transfer-encoding", "connection",
                            "content-security-policy", "x-content-type-options",
                            "service-worker-allowed", "clear-site-data"}
HEADER_INJECTION_CHARACTERS = "\r\n\0"  # would end a header and start another, or end the headers


def safe_response_headers(headers) -> list[tuple[str, str]]:
    """A module route's answer's headers that may reach the browser: [name, value] pairs, none of
    RESPONSE_HEADERS_DROPPED, none that could smuggle in a header of its own."""
    safe = []
    for item in headers or []:
        if not (isinstance(item, list) and len(item) == 2):
            continue
        name, value = str(item[0]), str(item[1])
        if name.lower() in RESPONSE_HEADERS_DROPPED or any(c in name + value for c in HEADER_INJECTION_CHARACTERS):
            continue
        safe.append((name, value))
    return safe


async def module_routes(scope, receive, send) -> None:
    """Mounted at /api/modules: /api/modules/<instance>/... goes to that instance's `router` in its own
    process, and nowhere else, so a module can't add or shadow a route of the core. No login in front: the
    module checks its callers itself. Its answer can't set a cookie on the mirror's site, and runs as a
    sandboxed page in a browser, so HTML from a module can't act as the admin page."""
    if scope["type"] != "http":
        await WebSocketClose(code=1008)(scope, receive, send)  # ponytail: HTTP only, no websockets
        return
    instance_id, _, rest = get_route_path(scope).lstrip("/").partition("/")
    if not client.module_host.knows(instance_id):
        await Response('{"detail":"Not Found"}', 404, media_type="application/json")(scope, receive, send)
        return

    body, more = b"", True
    while more:
        event = await receive()
        if event["type"] == "http.disconnect":
            return
        body += event.get("body", b"")
        more = event.get("more_body", False)
        if len(body) > BODY_LIMIT:
            await Response("Request too large", 413)(scope, receive, send)
            return

    caller = scope.get("client")
    try:
        reply = await client.module_host.call_route({
            "instance": instance_id, "method": scope["method"], "path": "/" + rest,
            "query": scope.get("query_string", b"").decode("latin-1"),
            "headers": [[k.decode("latin-1"), v.decode("latin-1")] for k, v in scope["headers"]
                        if k.decode("latin-1").lower() not in REQUEST_HEADERS_DROPPED],
            "body": base64.b64encode(body).decode(), "client": caller[0] if caller else None})
        status = reply.get("status")
        content = base64.b64decode(reply.get("body") or "")
        if not isinstance(status, int) or not 100 <= status <= 599:
            raise ValueError(f"bad status {status!r}")
    except (ConnectionError, TimeoutError, ValueError, TypeError) as e:
        logger.warning(f"{instance_id}: {scope['method']} /{rest} got no answer from the module: {e!r}")
        await Response("The module didn't answer", 504 if isinstance(e, TimeoutError) else 502)(scope, receive, send)
        return

    response = Response(content, status)
    for name, value in safe_response_headers(reply.get("headers")):
        try:
            response.headers.append(name, value)
        except (ValueError, UnicodeEncodeError):
            pass
    # sandbox: the browser runs the answer as a page of no site, so a module's HTML can't act as the admin page
    response.headers["Content-Security-Policy"] = "sandbox"
    response.headers["X-Content-Type-Options"] = "nosniff"
    await response(scope, receive, send)

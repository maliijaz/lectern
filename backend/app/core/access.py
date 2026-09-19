"""An optional shared-secret gate in front of the whole app.

Lectern is built to run on localhost, where there is nothing to protect it from. But it is
also worth exposing: a Cloudflare tunnel over a machine with a GPU gives someone the whole
product over the internet, and a free hosting tier gives a public demo. Both hand out a URL
that anyone can reach, and behind that URL is either your GPU or your API quota.

So: set ``LECTERN_ACCESS_KEY`` and the app answers nothing without it. Leave it empty and
nothing changes, because requiring a password to reach your own laptop is a way of making
people turn security off.

One shared secret, not user accounts. What is being protected is a teacher's own install,
not a multi-tenant service, and a login system nobody asked for is a login system nobody
maintains.
"""

from __future__ import annotations

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

#: Header for programmatic callers - the CLI, curl, a script.
HEADER = "X-Lectern-Key"
#: Query parameter, so a plain link works. Exchanged for a cookie immediately.
QUERY_PARAM = "key"
COOKIE = "lectern_access"

#: Health checks must answer before anything is authenticated, or the platform decides the
#: container is dead and restarts it forever.
EXEMPT_PATHS = frozenset({"/health"})


def _matches(candidate: str | None, expected: str) -> bool:
    """Constant-time comparison, so the response time does not leak the key."""
    if not candidate:
        return False
    return secrets.compare_digest(candidate, expected)


class AccessKeyMiddleware(BaseHTTPMiddleware):
    """Requires the shared secret, when one is configured."""

    def __init__(self, app, access_key: str) -> None:  # noqa: ANN001
        super().__init__(app)
        self.access_key = access_key

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001, ANN201
        if not self.access_key or request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        if _matches(request.headers.get(HEADER), self.access_key):
            return await call_next(request)

        if _matches(request.cookies.get(COOKIE), self.access_key):
            return await call_next(request)

        # ?key=... in a shared link. Swap it for a cookie and bounce to the clean URL, so
        # the secret stops travelling in browser history, bookmarks and Referer headers.
        if _matches(request.query_params.get(QUERY_PARAM), self.access_key):
            clean = request.url.remove_query_params(QUERY_PARAM)
            response = RedirectResponse(str(clean), status_code=303)
            response.set_cookie(
                COOKIE,
                self.access_key,
                httponly=True,
                samesite="lax",
                secure=request.url.scheme == "https",
                max_age=60 * 60 * 24 * 30,
            )
            return response

        return _refuse(request)


def _refuse(request: Request) -> Response:
    """401 as JSON for the API, as a readable page for a browser."""
    if request.url.path.startswith("/api/") or "application/json" in request.headers.get(
        "accept", ""
    ):
        return JSONResponse(
            {
                "code": "access_denied",
                "message": (
                    f"This instance requires an access key. Send it as a {HEADER} header, "
                    f"or open the URL with ?{QUERY_PARAM}=... once."
                ),
            },
            status_code=401,
        )
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8>"
        "<title>Lectern</title>"
        "<style>body{font:16px/1.6 system-ui,sans-serif;max-width:32rem;margin:20vh auto;"
        "padding:0 1.5rem;color:#1f2937}code{background:#f3f4f6;padding:.15em .4em;"
        "border-radius:.25rem}</style>"
        "<h1>Lectern</h1><p>This instance is private. Open it with the access key once "
        f"and it will be remembered:</p><p><code>?{QUERY_PARAM}=your-key</code></p>",
        status_code=401,
    )

"""
Request-ID propagation for correlation across logs and error responses.

Every request gets an `X-Request-ID` (reused if the caller already supplied
one, so an external system can pass its own trace ID through). It's stashed
in a contextvar so any log line emitted while handling the request can carry
it via `RequestIDLogFilter`, and it's echoed back on the response header and
in any error body -- the "which request produced this?" question should
always be answerable without correlating timestamps by hand.

Implemented as a raw ASGI middleware (not Starlette's `BaseHTTPMiddleware`)
deliberately: `BaseHTTPMiddleware` runs the downstream app in a separate task
behind an in-memory channel, which adds latency and changed this project's
own concurrency tests' timing enough to trip SQLite's single-writer lock
under load (`database is locked`). A plain ASGI callable wraps the app with
no extra task/channel indirection, so it doesn't change request timing.
"""
from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    return _request_id_ctx.get()


class RequestIDMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id")
        request_id = incoming.decode("latin-1") if incoming else uuid.uuid4().hex[:16]
        token = _request_id_ctx.set(request_id)

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-request-id", request_id.encode("latin-1")),
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            _request_id_ctx.reset(token)


class RequestIDLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_ctx.get()
        return True

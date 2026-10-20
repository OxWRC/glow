"""Per-request structured logging middleware.

Pure ASGI middleware (not Starlette's BaseHTTPMiddleware, which runs the
downstream app in a separate task via call_next and buffers streaming
responses) so it can see the raw ASGI messages directly: exact response
status/byte counts, and reliable exception propagation.

Registered via app.add_middleware() *after* CORSMiddleware in main.py, so it
sits outside CORS but inside Starlette's ServerErrorMiddleware (which is
always outermost). That position means a genuinely unhandled exception
reaches this middleware before ServerErrorMiddleware turns it into the
generic 500 response — we log it, then re-raise unchanged so that default
handling still happens, exactly as required.
"""

import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from starlette.types import ASGIApp, Receive, Scope, Send

from glow_api import audit, request_context

log = structlog.get_logger("glow_api.request")

_PACKAGE_ROOT = str(Path(__file__).resolve().parent)


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())
        method = scope["method"]
        raw_path = scope["path"]
        client_ip = self._client_ip(scope)
        start = time.perf_counter()

        timeline = request_context.new_timeline()
        response_state: dict[str, Any] = {"status_code": None, "response_bytes": 0}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                response_state["status_code"] = message["status"]
            elif message["type"] == "http.response.body":
                response_state["response_bytes"] += len(message.get("body", b"") or b"")
            await send(message)

        structlog.contextvars.bind_contextvars(request_id=request_id)
        error_fields = None
        exc_to_reraise = None
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            exc_to_reraise = exc
            error_fields = self._filtered_error(exc)
        finally:
            duration_s = time.perf_counter() - start
            route_path, route_name = self._route_template(scope)
            is_audit = audit.is_audit_route(method, route_path)

            status_code = response_state["status_code"]
            if status_code is None and exc_to_reraise is not None:
                status_code = 500

            log_fields: dict[str, Any] = {
                "request_id": request_id,
                "method": method,
                "path": route_path or raw_path,
                "raw_path": raw_path,
                "route_name": route_name,
                "client_ip": client_ip,
                "status_code": status_code,
                "duration_s": round(duration_s, 4),
                "response_bytes": response_state["response_bytes"],
                "audit": is_audit,
            }
            if is_audit:
                log_fields["ts"] = datetime.now(timezone.utc).isoformat()
                log_fields["timeline"] = timeline

            if error_fields is not None:
                log_fields["error"] = error_fields
                log.error("request_failed", **log_fields)
            elif route_path == "/health":
                log.debug("request_completed", **log_fields)
            else:
                log.info("request_completed", **log_fields)

            if is_audit:
                audit.write_audit_line(log_fields)

            request_context.clear_timeline()

        if exc_to_reraise is not None:
            raise exc_to_reraise

    @staticmethod
    def _client_ip(scope: Scope) -> str:
        for key, value in scope.get("headers") or []:
            if key == b"x-forwarded-for":
                # The app sits behind a single-hop AWS ALB, which appends the
                # real client address as the first entry.
                return value.decode("latin-1").split(",")[0].strip()
        client = scope.get("client")
        return client[0] if client else "unknown"

    @staticmethod
    def _route_template(scope: Scope) -> tuple[str | None, str | None]:
        route = scope.get("route")
        if route is not None:
            return getattr(route, "path", None), getattr(route, "name", None)
        return None, None

    @staticmethod
    def _filtered_error(exc: BaseException) -> dict[str, Any]:
        frames = traceback.extract_tb(exc.__traceback__)
        own_frames = [
            {
                "file": frame.filename,
                "line": frame.lineno,
                "function": frame.name,
                "code": frame.line,
            }
            for frame in frames
            if frame.filename.startswith(_PACKAGE_ROOT)
        ]
        return {
            "type": exc.__class__.__name__,
            "message": str(exc),
            "trace": own_frames,
        }

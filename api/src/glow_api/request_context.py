"""Per-request timeline accumulation.

Individual functions deep in the call stack (auth checks, query execution,
dispatch) call `record_event` to append a stage to the current request's
timeline. `RequestLoggingMiddleware` creates the timeline at the start of a
request and reads it back after the request completes to emit it as one
field on the request's log line.

The timeline is stored as a mutable list behind a contextvar rather than
reassigned per-event: uvicorn/Starlette handle each HTTP request in its own
asyncio Task (context is copied at task creation, not shared across
concurrent requests), so every function invoked during that request's await
chain sees and mutates the same list object the middleware created, with no
explicit propagation needed.
"""

import contextvars
import time
from typing import Any

_timeline: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "_timeline", default=None
)
# A mutable holder for the same reason as the timeline: sync dependencies run
# in a threadpool on a *copy* of the context, so a plain .set() there would
# never reach the middleware.
_actor: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "_actor", default=None
)


def new_timeline() -> list[dict[str, Any]]:
    """Create and install a fresh timeline list for the current request context."""
    timeline: list[dict[str, Any]] = []
    _timeline.set(timeline)
    _actor.set({})
    return timeline


def clear_timeline() -> None:
    _timeline.set(None)
    _actor.set(None)


def set_actor(who: str) -> None:
    """Record WHO is making the current request, once they've authenticated.
    No-op outside a request, like record_event."""
    holder = _actor.get()
    if holder is not None:
        holder["who"] = who


def get_actor() -> str | None:
    holder = _actor.get()
    return holder.get("who") if holder else None


def record_event(stage: str, **fields: Any) -> None:
    """Append a timeline entry for the current request.

    No-op if called outside an active request context (e.g. background
    tasks, or code paths exercised without going through the middleware) —
    logging must never break the request it's describing.
    """
    timeline = _timeline.get()
    if timeline is None:
        return
    timeline.append({"stage": stage, "ts": time.time(), **fields})

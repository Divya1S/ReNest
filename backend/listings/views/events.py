"""
Server-Sent Events endpoint for real-time notification updates.

Each authenticated client opens GET /api/events and receives a persistent
text/event-stream response.  The generator polls the DB every POLL_INTERVAL
seconds and pushes a `notification.new` event when the unread count changes,
plus a heartbeat comment every tick to keep the connection alive.

Async generator: under ASGI (uvicorn) asyncio.sleep() releases the event
loop between ticks so thousands of SSE connections share a small thread
pool rather than each pinning a gunicorn worker.  Under WSGI the generator
is iterated synchronously — behaviour is identical to before, just slightly
less efficient (one worker per connection).

To switch to ASGI mode:
  pip install uvicorn[standard]
  gunicorn dormcycle.asgi:application -k uvicorn.workers.UvicornWorker
"""
from __future__ import annotations

import asyncio
import json

from django.http import StreamingHttpResponse
from django.views import View

from ..models import Notification
from typing import Any

_POLL_INTERVAL = 4      # seconds between DB polls
_MAX_DURATION = 5 * 60  # max connection life (300 s) — client reconnects after


def _sse_event(event_type: str, payload: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"


def _sse_comment(text: str = "ping") -> str:
    return f": {text}\n\n"


async def _notification_stream(user: Any) -> Any:
    """
    Async generator: yields SSE chunks and awaits between polls.
    Under ASGI this releases the event loop so other connections are served
    during the sleep.  Under WSGI Django iterates it synchronously.
    """
    from asgiref.sync import sync_to_async

    _count_unread = sync_to_async(
        lambda u: Notification.objects.filter(user=u, is_read=False).count(),
        thread_sensitive=True,
    )

    last_count: int | None = None
    ticks = int(_MAX_DURATION / _POLL_INTERVAL)

    for _ in range(ticks):
        count = await _count_unread(user)
        if count != last_count:
            last_count = count
            yield _sse_event("notification.new", {"unread_count": count})
        else:
            yield _sse_comment()
        await asyncio.sleep(_POLL_INTERVAL)

    yield _sse_event("stream.end", {"reason": "max_duration"})


class EventStreamView(View):
    """
    GET /api/events
    Returns a text/event-stream response.  Authentication is handled manually
    (DRF SessionAuthentication) so Django's View machinery can return a
    StreamingHttpResponse before DRF processes the request.
    """

    def get(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        # Manual auth — DRF isn't used here because StreamingHttpResponse
        # must be returned before the response is fully constructed.
        if not request.user or not request.user.is_authenticated:
            from django.http import HttpResponse
            return HttpResponse("Unauthorized", status=401)

        response = StreamingHttpResponse(
            _notification_stream(request.user),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"   # disable nginx buffering
        response["Access-Control-Allow-Origin"] = "*"
        return response

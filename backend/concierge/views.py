import asyncio
import json
import logging
import queue
import threading
import weakref
from datetime import timedelta
from typing import Any, AsyncIterator, Iterator

from django.db import connections
from django.db.models import Sum
from django.http import StreamingHttpResponse
from django.http.response import HttpResponseBase
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import permissions, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from dormcycle.middleware import get_request_id, set_request_id

from . import engine
from .models import ConciergeMessage, ConciergeThread, ConciergeTurnOutcome
from .seeding import ensure_seeded

logger = logging.getLogger(__name__)

# While a turn runs, the stream may be silent for as long as one model call.
# Proxies and the browser client (which aborts after 75 s without a chunk)
# read silence as a dead connection, so the stream says "still here" this
# often. Sent as an SSE comment, which EventSource-style parsers ignore.
HEARTBEAT_SECONDS = 15.0
HEARTBEAT_FRAME = ": keepalive\n\n"

# Days of turn outcomes summed by the stats endpoint.
STATS_WINDOW_DAYS = 7

# Message meta that stays on the server. The trajectory exists for replay and
# debugging (admin, database); shipping it would add kilobytes of tool output
# to every history row the widget loads.
_PRIVATE_META_KEYS = frozenset({"trajectory", "trajectory_truncated"})

# Stream workers still running, so tests (and anything that needs a clean
# shutdown) can wait for work that continues after the response has closed.
_stream_workers: "weakref.WeakSet[threading.Thread]" = weakref.WeakSet()


def join_stream_workers(timeout: float = 10.0) -> None:
    """Block until in-flight stream workers have finished (or `timeout` passes)."""
    for worker in list(_stream_workers):
        worker.join(timeout)


def _get_thread(user: Any) -> ConciergeThread:
    thread, _ = ConciergeThread.objects.get_or_create(user=user)
    return thread


def _public_meta(meta: Any) -> dict[str, Any]:
    return {key: value for key, value in (meta or {}).items() if key not in _PRIVATE_META_KEYS}


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    return {**result, "meta": _public_meta(result.get("meta"))}


def _serialize_message(message: ConciergeMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "used_tools": message.used_tools,
        # Route/plan/reflection metadata + server-hydrated generative-UI blocks,
        # so history re-renders exactly what the live turn showed.
        "meta": _public_meta(message.meta),
        # 1 (helpful), -1 (not helpful) or null. Without it a reload showed
        # every reply as unrated and the thumbs state was lost.
        "rating": message.rating,
        "created_at": message.created_at.isoformat(),
    }


class ConciergeChatView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "concierge"

    @extend_schema(
        request=inline_serializer("ConciergeChatRequest", {"message": serializers.CharField()}),
        responses=inline_serializer(
            "ConciergeChatResponse",
            {
                "reply": serializers.CharField(),
                "used_tools": serializers.ListField(child=serializers.CharField()),
                "degraded": serializers.BooleanField(),
                "meta": serializers.DictField(),
                "message_id": serializers.IntegerField(required=False),
                "rating": serializers.IntegerField(required=False, allow_null=True),
            },
        ),
        summary="Send a message to the Nest Concierge",
    )
    def post(self, request: Request) -> Response:
        text = str(request.data.get("message") or "").strip()
        if not text:
            return Response({"detail": "Message is required."}, status=status.HTTP_400_BAD_REQUEST)
        if len(text) > 2000:
            return Response({"detail": "Message is too long (2000 characters max)."}, status=status.HTTP_400_BAD_REQUEST)

        ensure_seeded()
        result = engine.run_turn(request.user, _get_thread(request.user), text)
        # Degraded turns are 200s on purpose: the widget renders the fallback
        # copy instead of treating an assistant outage as an app error.
        return Response(_public_result(result))


class ConciergeChatStreamView(APIView):
    """Streaming variant of chat: SSE progress events, then the final result.

    Agentic turns take seconds; streaming 'planning → checking live listings →
    self-checking' turns dead air into visible progress. The turn runs in a
    worker thread that feeds a queue; this generator drains it as SSE lines,
    with a comment frame as a heartbeat while the queue is quiet. The JSON
    endpoint above stays as the non-streaming fallback.
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "concierge"

    @extend_schema(
        request=inline_serializer("ConciergeChatStreamRequest", {"message": serializers.CharField()}),
        responses={200: None},
        summary="Send a message to the Nest Concierge (SSE stream)",
    )
    def post(self, request: Request) -> HttpResponseBase:
        text = str(request.data.get("message") or "").strip()
        if not text:
            return Response({"detail": "Message is required."}, status=status.HTTP_400_BAD_REQUEST)
        if len(text) > 2000:
            return Response({"detail": "Message is too long (2000 characters max)."}, status=status.HTTP_400_BAD_REQUEST)

        ensure_seeded()
        user = request.user
        thread = _get_thread(user)
        events: "queue.Queue[dict[str, Any] | None]" = queue.Queue()
        # The request id lives in a thread-local of the request thread; the
        # worker has to be handed it or every log line of the turn says rid=-.
        request_id = getattr(request, "request_id", None) or get_request_id()

        def worker() -> None:
            set_request_id(request_id)
            delivered = False

            def deliver(result: dict[str, Any]) -> None:
                # The done event and the end of the stream go out together.
                # The engine calls this as soon as the reply is stored, before
                # its housekeeping (cache store, memory compression), so the
                # user is never waiting on a summarisation call.
                nonlocal delivered
                delivered = True
                events.put({"type": "done", **_public_result(result)})
                events.put(None)

            try:
                result = engine.run_turn(
                    user,
                    thread,
                    text,
                    on_event=lambda event: events.put({"type": "phase", **event}),
                    on_reply=deliver,
                )
                if not delivered:      # cached and degraded results are just returned
                    deliver(result)
            except Exception:  # pragma: no cover (run_turn already catches; belt and braces)
                logger.exception("concierge stream worker crashed")
                if not delivered:
                    deliver({"reply": engine.DEGRADED_REPLY, "used_tools": [], "degraded": True, "meta": {}})
            finally:
                # This thread owns its own database connection and nothing
                # will reuse it. close_old_connections() only closes a
                # connection past CONN_MAX_AGE, which would leave one open
                # per streamed turn.
                connections.close_all()
                set_request_id(None)

        worker_thread = threading.Thread(target=worker, daemon=True, name="concierge-stream")
        _stream_workers.add(worker_thread)
        worker_thread.start()

        def frame(item: dict[str, Any]) -> str:
            return f"data: {json.dumps(item, default=str)}\n\n"

        def stream() -> Iterator[str]:
            while True:
                try:
                    item = events.get(timeout=HEARTBEAT_SECONDS)
                except queue.Empty:
                    yield HEARTBEAT_FRAME
                    continue
                if item is None:
                    return
                yield frame(item)

        async def astream() -> AsyncIterator[str]:
            # Under ASGI (uvicorn in production) Django buffers a *synchronous*
            # iterator completely before sending anything, which defeats the
            # point of SSE: the client would receive every phase event at once
            # when the turn finished. An async generator streams as intended.
            # The blocking queue read is offloaded so the event loop stays
            # free, and bounded by the heartbeat interval so an abandoned
            # stream releases its executor thread.
            while True:
                try:
                    item = await asyncio.to_thread(events.get, True, HEARTBEAT_SECONDS)
                except queue.Empty:
                    yield HEARTBEAT_FRAME
                    continue
                if item is None:
                    return
                yield frame(item)

        is_asgi = getattr(request, "scope", None) is not None
        response = StreamingHttpResponse(
            astream() if is_asgi else stream(), content_type="text/event-stream"
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"  # never proxy-buffer SSE
        return response


class ConciergeHistoryView(APIView):
    @extend_schema(
        responses=inline_serializer(
            "ConciergeHistoryResponse",
            {
                "enabled": serializers.BooleanField(),
                "messages": serializers.ListField(child=serializers.DictField()),
            },
        ),
        summary="Fetch recent concierge conversation history",
    )
    def get(self, request: Request) -> Response:
        thread = _get_thread(request.user)
        queryset = thread.messages.order_by("-created_at", "-id")
        before = request.query_params.get("before", "")
        if before.isdigit():
            # Cursor pagination for scrollback: ids are monotonic, so "older
            # than message <id>" is exact even across created_at ties.
            queryset = queryset.filter(id__lt=int(before))
        rows = list(queryset[:51])
        has_more = len(rows) > 50
        rows = rows[:50][::-1]
        return Response(
            {
                "enabled": engine.is_enabled(),
                "messages": [_serialize_message(row) for row in rows],
                "has_more": has_more,
            }
        )


class ConciergeResetView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "concierge"

    @extend_schema(request=None, responses={204: None}, summary="Clear the concierge conversation")
    def post(self, request: Request) -> Response:
        if request.user.is_authenticated:  # always true (IsAuthenticated); narrows the type union
            ConciergeThread.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


_RATINGS = {"up": 1, "down": -1, "clear": None}


class ConciergeFeedbackView(APIView):
    @extend_schema(
        request=inline_serializer("ConciergeFeedbackRequest", {"rating": serializers.CharField()}),
        responses={204: None},
        summary="Rate a concierge reply (up / down / clear)",
    )
    def post(self, request: Request, pk: int) -> Response:
        rating_key = str(request.data.get("rating") or "")
        if rating_key not in _RATINGS:
            return Response({"detail": "rating must be 'up', 'down', or 'clear'."}, status=400)
        if not request.user.is_authenticated:  # always true (IsAuthenticated); narrows the type union
            return Response({"detail": "Message not found."}, status=404)
        updated = ConciergeMessage.objects.filter(
            pk=pk,
            thread__user=request.user,                 # never rate someone else's thread
            role=ConciergeMessage.Role.ASSISTANT,
        ).update(rating=_RATINGS[rating_key])
        if not updated:
            return Response({"detail": "Message not found."}, status=404)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _outcome_stats() -> dict[str, Any]:
    """How turns ended over the last STATS_WINDOW_DAYS days, failures included.

    Read from the daily tallies rather than from message rows: a failed turn
    stores no message, so messages can only ever describe the successes.
    """
    since = timezone.localdate() - timedelta(days=STATS_WINDOW_DAYS - 1)
    totals: dict[str, int] = {
        row["outcome"]: int(row["total"] or 0)
        for row in ConciergeTurnOutcome.objects.filter(day__gte=since)
        .values("outcome")
        .annotate(total=Sum("count"))
    }
    by_reason = {reason: totals[reason] for reason in engine.DEGRADED_REASONS if totals.get(reason)}
    degraded = sum(by_reason.values())
    answered = totals.get(engine.OUTCOME_OK, 0)
    turns = answered + degraded
    return {
        "window_days": STATS_WINDOW_DAYS,
        "since": since.isoformat(),
        "turns": turns,
        "ok": answered,
        "degraded": degraded,
        "by_reason": by_reason,
        # Degraded turns that still got a help-section answer (also counted
        # under their reason above).
        "answered_from_kb": totals.get(engine.OUTCOME_KB_ANSWER, 0),
        "failure_rate": round(degraded / turns, 4) if turns else None,
    }


class ConciergeStatsView(APIView):
    """Operator dashboard for concierge quality (staff only)."""

    permission_classes = [permissions.IsAdminUser]

    @extend_schema(
        responses=inline_serializer(
            "ConciergeStatsResponse",
            {"turns": serializers.DictField(), "outcomes": serializers.DictField()},
        ),
        summary="Concierge quality metrics (staff)",
    )
    def get(self, request: Request) -> Response:
        from . import rag, semantic_cache
        from .models import KnowledgeChunk

        recent = list(
            ConciergeMessage.objects.filter(role=ConciergeMessage.Role.ASSISTANT)
            .order_by("-created_at")
            .values("meta", "rating")[:300]
        )
        routes: dict[str, int] = {}
        cached = revised = reflected = reflection_failed = downgraded = 0
        latencies: list[int] = []
        ratings = {"up": 0, "down": 0}
        usage = {"turns_with_usage": 0, "model_calls": 0, "prompt_tokens": 0,
                 "candidates_tokens": 0, "thoughts_tokens": 0, "total_tokens": 0}
        for row in recent:
            meta = row["meta"] or {}
            route_name = ((meta.get("route") or {}).get("name")) or "unknown"
            routes[route_name] = routes.get(route_name, 0) + 1
            cached += 1 if meta.get("cached") else 0
            revised += 1 if meta.get("revised") else 0
            downgraded += 1 if meta.get("model_downgraded") else 0
            if meta.get("reflection") is not None:
                reflected += 1
                reflection_failed += 0 if meta["reflection"].get("passed") else 1
            if isinstance(meta.get("latency_ms"), int):
                latencies.append(meta["latency_ms"])
            turn_usage = meta.get("usage")
            if isinstance(turn_usage, dict):
                usage["turns_with_usage"] += 1
                for key in ("model_calls", "prompt_tokens", "candidates_tokens", "thoughts_tokens", "total_tokens"):
                    value = turn_usage.get(key)
                    usage[key] += value if isinstance(value, int) else 0
            if row["rating"] == 1:
                ratings["up"] += 1
            elif row["rating"] == -1:
                ratings["down"] += 1
        latencies.sort()
        chunk = KnowledgeChunk.objects.only("vector_space").first()
        return Response(
            {
                "turns": {
                    "sampled": len(recent),
                    "routes": routes,
                    "cached": cached,
                    "revised": revised,
                    "downgraded": downgraded,
                    "reflections": {"graded": reflected, "failed": reflection_failed},
                    "latency_ms": {
                        "avg": int(sum(latencies) / len(latencies)) if latencies else None,
                        "p95": latencies[int(len(latencies) * 0.95)] if latencies else None,
                    },
                    "ratings": ratings,
                    # Token totals over the sampled turns (cached turns make
                    # no model call and carry no usage block).
                    "usage": {
                        **usage,
                        "avg_total_tokens": (
                            int(usage["total_tokens"] / usage["turns_with_usage"])
                            if usage["turns_with_usage"] else None
                        ),
                    },
                },
                "outcomes": _outcome_stats(),
                "semantic_cache": semantic_cache.stats(),
                "breaker_open": engine.breaker_is_open(),
                "knowledge_base": {
                    "chunks": KnowledgeChunk.objects.count(),
                    "embedding_space": chunk.vector_space if chunk else None,
                    "remote_embeddings_available": rag.remote_available(),
                },
            }
        )

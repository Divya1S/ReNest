import json
import logging
import queue
import threading
from typing import Any

from django.db import close_old_connections
from django.http import StreamingHttpResponse
from django.http.response import HttpResponseBase
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import permissions, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import engine
from .models import ConciergeMessage, ConciergeThread
from .seeding import ensure_seeded

logger = logging.getLogger(__name__)


def _get_thread(user: Any) -> ConciergeThread:
    thread, _ = ConciergeThread.objects.get_or_create(user=user)
    return thread


def _serialize_message(message: ConciergeMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "used_tools": message.used_tools,
        # Route/plan/reflection metadata + server-hydrated generative-UI blocks,
        # so history re-renders exactly what the live turn showed.
        "meta": message.meta,
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
        return Response(result)


class ConciergeChatStreamView(APIView):
    """Streaming variant of chat: SSE progress events, then the final result.

    Agentic turns take seconds; streaming 'planning → checking live listings →
    self-checking' turns dead air into visible progress. The turn runs in a
    worker thread that feeds a queue; this generator drains it as SSE lines.
    The JSON endpoint above stays as the non-streaming fallback.
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

        def worker() -> None:
            try:
                result = engine.run_turn(
                    user, thread, text, on_event=lambda event: events.put({"type": "phase", **event})
                )
                events.put({"type": "done", **result})
            except Exception:  # pragma: no cover — run_turn already catches; belt & braces
                logger.exception("concierge stream worker crashed")
                events.put({"type": "done", "reply": engine.DEGRADED_REPLY,
                            "used_tools": [], "degraded": True, "meta": {}})
            finally:
                close_old_connections()  # worker thread owns its own DB connection
                events.put(None)

        threading.Thread(target=worker, daemon=True).start()

        def stream() -> Any:
            while (item := events.get()) is not None:
                yield f"data: {json.dumps(item)}\n\n"

        response = StreamingHttpResponse(stream(), content_type="text/event-stream")
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


class ConciergeStatsView(APIView):
    """Operator dashboard for concierge quality — staff only."""

    permission_classes = [permissions.IsAdminUser]

    @extend_schema(
        responses=inline_serializer("ConciergeStatsResponse", {"turns": serializers.DictField()}),
        summary="Concierge quality metrics (staff)",
    )
    def get(self, request: Request) -> Response:
        from . import engine, rag, semantic_cache
        from .models import KnowledgeChunk

        recent = list(
            ConciergeMessage.objects.filter(role=ConciergeMessage.Role.ASSISTANT)
            .order_by("-created_at")
            .values("meta", "rating")[:300]
        )
        routes: dict[str, int] = {}
        cached = revised = reflected = reflection_failed = 0
        latencies: list[int] = []
        ratings = {"up": 0, "down": 0}
        for row in recent:
            meta = row["meta"] or {}
            route_name = ((meta.get("route") or {}).get("name")) or "unknown"
            routes[route_name] = routes.get(route_name, 0) + 1
            cached += 1 if meta.get("cached") else 0
            revised += 1 if meta.get("revised") else 0
            if meta.get("reflection") is not None:
                reflected += 1
                reflection_failed += 0 if meta["reflection"].get("passed") else 1
            if isinstance(meta.get("latency_ms"), int):
                latencies.append(meta["latency_ms"])
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
                    "reflections": {"graded": reflected, "failed": reflection_failed},
                    "latency_ms": {
                        "avg": int(sum(latencies) / len(latencies)) if latencies else None,
                        "p95": latencies[int(len(latencies) * 0.95)] if latencies else None,
                    },
                    "ratings": ratings,
                },
                "semantic_cache": semantic_cache.stats(),
                "breaker_open": engine.breaker_is_open(),
                "knowledge_base": {
                    "chunks": KnowledgeChunk.objects.count(),
                    "embedding_space": chunk.vector_space if chunk else None,
                    "remote_embeddings_available": rag.remote_available(),
                },
            }
        )

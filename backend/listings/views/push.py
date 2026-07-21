from __future__ import annotations

import os

from rest_framework import permissions
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import PushSubscription
from ..throttles import PushSubscribeThrottle
from dormcycle.typed import current_user


class VapidPublicKeyView(APIView):
    """GET /api/push/vapid-key — returns the VAPID public key for the frontend."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        key = os.getenv("VAPID_PUBLIC_KEY", "")
        return Response({"public_key": key, "enabled": bool(key)})


class PushSubscribeView(APIView):
    """
    POST /api/push/subscribe
    Body: { "endpoint": "...", "keys": { "p256dh": "...", "auth": "..." } }
    Upserts a PushSubscription for the authenticated user.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [PushSubscribeThrottle]
    parser_classes = [JSONParser]

    def post(self, request: Request) -> Response:
        platform = request.data.get("platform", "web")

        # Phase 22 — native APNs/FCM token from Capacitor shell
        if platform in ("apns", "fcm"):
            native_token = request.data.get("token", "").strip()
            if not native_token:
                return Response({"detail": "token is required for native platforms."}, status=400)
            _, created = PushSubscription.objects.update_or_create(
                native_token=native_token,
                defaults={
                    "user": current_user(request),
                    "platform": platform,
                    "endpoint": f"{platform}:{native_token}",
                    "user_agent": request.META.get("HTTP_USER_AGENT", "")[:255],
                },
            )
            return Response({"subscribed": True, "created": created})

        # Web VAPID flow
        endpoint = request.data.get("endpoint", "")
        keys = request.data.get("keys", {})
        p256dh = keys.get("p256dh", "")
        auth = keys.get("auth", "")

        if not endpoint or not p256dh or not auth:
            return Response({"detail": "endpoint, keys.p256dh and keys.auth are required."}, status=400)

        sub, created = PushSubscription.objects.update_or_create(
            endpoint=endpoint,
            defaults={
                "user": current_user(request),
                "platform": PushSubscription.Platform.WEB,
                "p256dh": p256dh,
                "auth": auth,
                "user_agent": request.META.get("HTTP_USER_AGENT", "")[:255],
            },
        )
        return Response({"subscribed": True, "created": created})


class PushUnsubscribeView(APIView):
    """
    POST /api/push/unsubscribe
    Body: { "endpoint": "..." }
    Removes the subscription for the given endpoint if it belongs to the user.
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request: Request) -> Response:
        endpoint = request.data.get("endpoint", "")
        deleted, _ = PushSubscription.objects.filter(
            user=current_user(request), endpoint=endpoint
        ).delete()
        return Response({"unsubscribed": True, "deleted": deleted > 0})

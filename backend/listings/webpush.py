"""
Web Push notification helpers.

VAPID keys are read from environment variables:
  VAPID_PRIVATE_KEY  — base64url-encoded private key (generate once with gen_vapid_keys())
  VAPID_PUBLIC_KEY   — base64url-encoded public key  (served to the frontend)
  VAPID_CLAIMS_SUB   — mailto: or https: subscriber identifier

Generate a key pair (run once, store results in .env):
  python manage.py shell -c "from listings.webpush import gen_vapid_keys; gen_vapid_keys()"
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .models import PushSubscription

logger = logging.getLogger(__name__)

_VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
_VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
_VAPID_CLAIMS_SUB = os.getenv("VAPID_CLAIMS_SUB", "mailto:admin@renest.app")


def gen_vapid_keys() -> None:
    """Print a fresh VAPID key pair to stdout. Run once; store in .env."""
    try:
        from py_vapid import Vapid
    except ImportError:
        print("pywebpush not installed. Run: pip install pywebpush")
        return
    v = Vapid()
    v.generate_keys()
    print("VAPID_PRIVATE_KEY =", v.private_key.private_bytes(
        encoding=__import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.PEM,
        format=__import__("cryptography.hazmat.primitives.serialization", fromlist=["PrivateFormat"]).PrivateFormat.Raw,
        encryption_algorithm=__import__("cryptography.hazmat.primitives.serialization", fromlist=["NoEncryption"]).NoEncryption(),
    ).hex())
    print("VAPID_PUBLIC_KEY  =", v.public_key.public_bytes(
        encoding=__import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.X962,
        format=__import__("cryptography.hazmat.primitives.serialization.ec", fromlist=["EllipticCurvePublicFormat"]).EllipticCurvePublicFormat.UncompressedPoint,
    ).hex())


def send_push(subscription: PushSubscription, *, title: str, body: str, url: str = "/dashboard") -> bool:
    """
    Send a Web Push notification to a single subscription.
    Returns True on success, False on any error.
    Expired subscriptions (410 Gone) are deleted automatically.
    """
    if not _VAPID_PRIVATE_KEY or not _VAPID_PUBLIC_KEY:
        logger.debug("VAPID keys not configured — Web Push skipped")
        return False

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.warning("pywebpush not installed — Web Push skipped")
        return False

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url,
        "icon": "/images/icon-192.png",
    })

    subscription_info: dict[str, Any] = {
        "endpoint": subscription.endpoint,
        "keys": {
            "p256dh": subscription.p256dh,
            "auth": subscription.auth,
        },
    }

    try:
        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=_VAPID_PRIVATE_KEY,
            vapid_claims={"sub": _VAPID_CLAIMS_SUB},
        )
        return True
    except Exception as exc:
        # 410 Gone means the subscription is no longer valid — delete it
        status = getattr(exc, "response", None)
        if status is not None and getattr(status, "status_code", None) == 410:
            logger.info("Push subscription expired, deleting: %s", subscription.pk)
            subscription.delete()
        else:
            logger.warning("Web Push failed for subscription %s: %s", subscription.pk, exc)
        return False


def send_push_to_user(user: Any, *, title: str, body: str, url: str = "/dashboard", notification_type: str | None = None) -> int:
    """
    Send a push notification to all subscriptions for a user.

    Respects Phase 26 preferences:
    - If channel = off or email → skips push entirely.
    - If current time is within the user's quiet window → queues in Redis sorted set
      (key: dormcycle:quiet_queue, score = epoch of quiet_end) instead of sending.

    Returns number of push messages actually sent (0 if queued or suppressed).
    """
    from .models import PushSubscription as PS

    # Preference check
    if notification_type:
        _channel = _get_push_channel(user, notification_type)
        if _channel in ("off", "email"):
            return 0

    # Quiet-hours check
    if notification_type and _is_quiet_hours(user, notification_type):
        _enqueue_quiet(user, title=title, body=body, url=url, notification_type=notification_type)
        return 0

    subs = PS.objects.filter(user=user)
    return sum(send_push(s, title=title, body=body, url=url) for s in subs)


def _get_push_channel(user: Any, notification_type: str) -> str:
    """Return the channel setting for this user+type. Defaults to 'both'."""
    from .models import NotificationPreference
    try:
        pref = NotificationPreference.objects.get(user=user, notification_type=notification_type)
        return pref.channel
    except NotificationPreference.DoesNotExist:
        return "both"


def _is_quiet_hours(user: Any, notification_type: str) -> bool:
    """Return True if current campus-local time is within the user's quiet window."""
    import datetime
    from django.utils import timezone
    from .models import NotificationPreference

    try:
        pref = NotificationPreference.objects.get(user=user, notification_type=notification_type)
    except NotificationPreference.DoesNotExist:
        pref = None

    start = pref.quiet_hours_start if pref else None
    end = pref.quiet_hours_end if pref else None

    if start is None or end is None:
        # Default quiet window: 23:00–08:00
        start = datetime.time(23, 0)
        end = datetime.time(8, 0)

    tz_name = getattr(getattr(user, "campus", None), "timezone", None) or "America/Los_Angeles"
    try:
        import zoneinfo
        local_tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        local_tz = timezone.get_current_timezone()

    now_local = timezone.now().astimezone(local_tz).time().replace(second=0, microsecond=0)

    if start <= end:
        return start <= now_local < end
    # Overnight window (e.g. 23:00–08:00)
    return now_local >= start or now_local < end


def _enqueue_quiet(user: Any, *, title: str, body: str, url: str, notification_type: str) -> None:
    """Push notification payload onto the Redis quiet_queue sorted set."""
    import datetime
    import json as _json
    import time

    from django.core.cache import cache
    from django.utils import timezone

    tz_name = getattr(getattr(user, "campus", None), "timezone", None) or "America/Los_Angeles"
    try:
        import zoneinfo
        local_tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        local_tz = timezone.get_current_timezone()

    now_local = timezone.now().astimezone(local_tz)
    pref = None
    try:
        from .models import NotificationPreference
        pref = NotificationPreference.objects.get(user=user, notification_type=notification_type)
    except Exception:
        pass

    end_time = pref.quiet_hours_end if pref and pref.quiet_hours_end else datetime.time(8, 0)
    fire_at_local = now_local.replace(
        hour=end_time.hour, minute=end_time.minute, second=0, microsecond=0
    )
    if fire_at_local <= now_local:
        fire_at_local += datetime.timedelta(days=1)

    fire_epoch = fire_at_local.timestamp()

    payload = _json.dumps({
        "user_id": user.pk,
        "title": title,
        "body": body,
        "url": url,
        "notification_type": notification_type,
    })

    # Use Django cache as a simple list; store as "quiet_queue:{user_id}" JSON list
    key = f"quiet_queue:{user.pk}"
    existing = cache.get(key) or []
    existing.append({"score": fire_epoch, "payload": payload})
    ttl = int(fire_epoch - time.time()) + 3600
    cache.set(key, existing, timeout=max(ttl, 3600))


def flush_quiet_queue_for_user(user: Any) -> int:
    """Dispatch any queued notifications whose fire time has passed. Returns count sent."""
    import json as _json
    import time

    from django.core.cache import cache

    key = f"quiet_queue:{user.pk}"
    items = cache.get(key) or []
    now_epoch = time.time()
    remaining = []
    sent = 0

    for item in items:
        if item["score"] <= now_epoch:
            try:
                data = _json.loads(item["payload"])
                send_push_to_user(
                    user,
                    title=data["title"],
                    body=data["body"],
                    url=data.get("url", "/dashboard"),
                    # No notification_type to avoid re-queuing
                )
                sent += 1
            except Exception:
                pass
        else:
            remaining.append(item)

    if remaining != items:
        cache.set(key, remaining, timeout=3600)
    return sent

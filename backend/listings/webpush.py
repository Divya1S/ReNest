"""
Web Push notification helpers.

VAPID keys are read from environment variables:
  VAPID_PRIVATE_KEY  - base64url-encoded private key (generate once with gen_vapid_keys())
  VAPID_PUBLIC_KEY   - base64url-encoded public key  (served to the frontend)
  VAPID_CLAIMS_SUB   - mailto: or https: subscriber identifier

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
        logger.debug("VAPID keys not configured, Web Push skipped")
        return False

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.warning("pywebpush not installed, Web Push skipped")
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
        # 404/410 mean the endpoint no longer exists, so delete the subscription
        # so the same dead endpoint is not retried on every future send.
        status = getattr(exc, "response", None)
        if status is not None and getattr(status, "status_code", None) in (404, 410):
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
    - If current time is within the user's quiet window → holds the message in
      the user's quiet queue (see `_enqueue_quiet`) instead of sending.

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

    # Native (APNs/FCM) tokens are not VAPID endpoints; sending to them always
    # fails. They are delivered by the mobile shell's own channel.
    subs = PS.objects.filter(user=user, platform=PS.Platform.WEB)
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
    from django.utils import timezone
    from .models import NotificationPreference

    try:
        pref = NotificationPreference.objects.get(user=user, notification_type=notification_type)
    except NotificationPreference.DoesNotExist:
        pref = None

    start = pref.quiet_hours_start if pref else None
    end = pref.quiet_hours_end if pref else None

    if start is None or end is None:
        # No quiet window configured: deliver. Applying a default window here
        # would silently hold notifications for every user who never opened
        # the preferences page.
        return False

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


# After its due time an item waits for the next flush run. If no flush has
# happened this long after the last item fell due, the scheduler is down and
# the notifications are stale anyway, so the entry is allowed to expire.
_QUIET_QUEUE_GRACE_SECONDS = 6 * 3600


def _quiet_queue_key(user_id: Any) -> str:
    return f"quiet_queue:{user_id}"


def _store_quiet_queue(user_id: Any, items: list[dict[str, Any]], now_epoch: float) -> None:
    """Write the queue back with a TTL that covers its latest due item.

    The entry used to be rewritten with a flat one-hour timeout on flush, so
    an item due later than that expired from the cache and was never sent.
    """
    from django.core.cache import cache

    key = _quiet_queue_key(user_id)
    if not items:
        cache.delete(key)
        return
    latest_due = max(float(item["score"]) for item in items)
    ttl = int(max(latest_due - now_epoch, 0)) + _QUIET_QUEUE_GRACE_SECONDS
    cache.set(key, items, timeout=ttl)


def _enqueue_quiet(user: Any, *, title: str, body: str, url: str, notification_type: str) -> None:
    """Hold a push until the user's quiet window ends.

    The queue is one Django-cache entry per user, "quiet_queue:{user_id}",
    holding a list of {"score": due epoch, "payload": JSON string}. It is not
    a Redis sorted set: it works on any cache backend, at the price of a
    read-modify-write that is not atomic (two pushes queued in the same
    instant can lose one) and of living only as long as the cache does (the
    local-memory fallback forgets it on restart). `flush_quiet_queue_for_user`
    sends the items that have fallen due.
    """
    import datetime
    import json as _json

    from django.core.cache import cache
    from django.utils import timezone

    tz_name = getattr(getattr(user, "campus", None), "timezone", None) or "America/Los_Angeles"
    try:
        import zoneinfo
        local_tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        local_tz = timezone.get_current_timezone()

    now = timezone.now()
    now_local = now.astimezone(local_tz)
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

    existing = cache.get(_quiet_queue_key(user.pk)) or []
    existing.append({"score": fire_epoch, "payload": payload})
    _store_quiet_queue(user.pk, existing, now.timestamp())


def flush_quiet_queue_for_user(user: Any) -> int:
    """Dispatch any queued notifications whose fire time has passed. Returns count sent."""
    import json as _json

    from django.core.cache import cache
    from django.utils import timezone

    items = cache.get(_quiet_queue_key(user.pk)) or []
    if not items:
        return 0
    now_epoch = timezone.now().timestamp()
    remaining = []
    sent = 0

    for item in items:
        if item["score"] > now_epoch:
            remaining.append(item)
            continue
        try:
            data = _json.loads(item["payload"])
            # The user may have switched this type off while it was queued.
            queued_type = data.get("notification_type")
            if queued_type and _get_push_channel(user, queued_type) in ("off", "email"):
                continue
            send_push_to_user(
                user,
                title=data["title"],
                body=data["body"],
                url=data.get("url", "/dashboard"),
                # No notification_type to avoid re-queuing
            )
            sent += 1
        except Exception:
            # A due item is dropped rather than retried forever, but not silently.
            logger.warning("Queued push for user %s could not be sent", user.pk, exc_info=True)

    if len(remaining) != len(items):
        _store_quiet_queue(user.pk, remaining, now_epoch)
    return sent

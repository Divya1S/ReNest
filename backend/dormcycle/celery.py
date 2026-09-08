from __future__ import annotations

import logging
import os

from celery import Celery
from celery.signals import task_failure, task_prerun

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dormcycle.settings")

app = Celery("dormcycle")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

logger = logging.getLogger("dormcycle.celery")

_DLQ_KEY = "celery:dlq"
_DLQ_MAX = 500  # cap the list so Redis memory is bounded


def redis_client():
    """Low-level Redis client for the dead-letter list, or None without Redis.

    Django's built-in RedisCache exposes no public client (the ``.client``
    attribute belongs to django-redis, which this project does not use), so
    connect directly with the broker URL instead.
    """
    from django.conf import settings

    url = getattr(settings, "CELERY_BROKER_URL", "") or os.getenv("REDIS_URL", "")
    if not url:
        return None
    import redis

    return redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)


@task_failure.connect
def on_task_failure(sender, task_id, exception, args, kwargs, traceback, einfo, **_kw):
    """
    After all retries are exhausted push a summary to the Redis dead-letter
    list and emit a structured log line (picked up by Sentry if configured).
    """
    logger.error(
        "Task permanently failed",
        extra={
            "task": sender.name,
            "task_id": task_id,
            "exc": repr(exception),
            "args": repr(args)[:200],
        },
    )
    try:
        import json

        client = redis_client()
        if client is None:
            return
        payload = json.dumps({
            "task": sender.name,
            "task_id": task_id,
            "exc": repr(exception),
        })
        client.lpush(_DLQ_KEY, payload)
        client.ltrim(_DLQ_KEY, 0, _DLQ_MAX - 1)
    except Exception:
        # DLQ write is best-effort; the log line above is the primary signal.
        logger.warning("Could not record task failure in the dead-letter queue", exc_info=True)


@task_prerun.connect
def on_task_prerun(sender, task_id, task, args, kwargs, **_kw):
    """Forward the request_id kwarg (if present) into the logging context."""
    request_id = kwargs.get("request_id") or kwargs.get("_request_id", "-")
    from dormcycle.middleware import _local
    _local.request_id = request_id

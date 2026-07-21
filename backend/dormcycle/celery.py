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
        from django.core.cache import cache
        # cache client is the Redis connection when REDIS_URL is set
        client = cache.client.get_client()  # django-redis low-level client
        payload = json.dumps({
            "task": sender.name,
            "task_id": task_id,
            "exc": repr(exception),
        })
        client.lpush(_DLQ_KEY, payload)
        client.ltrim(_DLQ_KEY, 0, _DLQ_MAX - 1)
    except Exception:
        pass  # DLQ write is best-effort; the log line is the primary signal


@task_prerun.connect
def on_task_prerun(sender, task_id, task, args, kwargs, **_kw):
    """Forward the request_id kwarg (if present) into the logging context."""
    request_id = kwargs.get("request_id") or kwargs.get("_request_id", "-")
    from dormcycle.middleware import _local
    _local.request_id = request_id

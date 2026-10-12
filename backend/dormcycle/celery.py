from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from celery import Celery
from celery.signals import task_failure, task_postrun, task_prerun

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dormcycle.settings")

app = Celery("dormcycle")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

logger = logging.getLogger("dormcycle.celery")

_DLQ_KEY = "celery:dlq"
_DLQ_MAX = 500  # cap the list so Redis memory is bounded
# Task arguments can be large (payloads, id lists) and may carry user data;
# the dead-letter entry only needs enough to recognise and replay the call.
_DLQ_SUMMARY_MAX = 200

SCHEDULED_JOBS_TASK = "dormcycle.run_scheduled_jobs"


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


def _summarise(value: Any, limit: int = _DLQ_SUMMARY_MAX) -> str:
    """repr() of a task argument structure, cut to a bounded length."""
    try:
        text = repr(value)
    except Exception:
        text = f"<unrepresentable {type(value).__name__}>"
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


@task_failure.connect
def on_task_failure(
    sender: Any = None,
    task_id: Any = None,
    exception: Any = None,
    args: Any = None,
    kwargs: Any = None,
    traceback: Any = None,
    einfo: Any = None,
    **_kw: Any,
) -> None:
    """
    After all retries are exhausted push a summary to the Redis dead-letter
    list and emit a structured log line (picked up by Sentry if configured).

    Celery swallows exceptions raised by signal receivers, so a bug in here
    fails silently. The Redis write therefore comes first and each step is
    guarded on its own: a logging problem must not cost the dead-letter entry,
    and a Redis outage must not cost the log line.
    """
    task_name = getattr(sender, "name", None) or str(sender)
    entry = {
        "task": task_name,
        "task_id": str(task_id) if task_id is not None else None,
        "exc": _summarise(exception, 500),
        "args": _summarise(args if args is not None else ()),
        "kwargs": _summarise(kwargs if kwargs is not None else {}),
        "failed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        client = redis_client()
        if client is not None:
            client.lpush(_DLQ_KEY, json.dumps(entry))
            client.ltrim(_DLQ_KEY, 0, _DLQ_MAX - 1)
    except Exception:
        logger.warning("Could not record task failure in the dead-letter queue", exc_info=True)

    try:
        logger.error(
            # Name, id and error go in the message as well: the plain-text
            # (development) formatter does not print extra fields.
            "Task permanently failed: %s[%s] %s",
            task_name,
            entry["task_id"],
            entry["exc"],
            # LogRecord reserves "args" (and "name", "msg", ...): passing one of
            # those in extra raises KeyError, hence the task_ prefix.
            extra={
                "task_name": task_name,
                "task_id": entry["task_id"],
                "task_exc": entry["exc"],
                "task_args": entry["args"],
                "task_kwargs": entry["kwargs"],
            },
        )
    except Exception:
        # Nothing left to report to; never let a receiver error propagate.
        pass


# Request id that was active before a task bound its own, keyed by task id,
# so task_postrun can put it back.
_previous_request_ids: dict[str, str] = {}


@task_prerun.connect
def on_task_prerun(
    sender: Any = None,
    task_id: Any = None,
    task: Any = None,
    args: Any = None,
    kwargs: Any = None,
    **_kw: Any,
) -> None:
    """Forward the request_id kwarg (if present) into the logging context.

    A task called without one keeps whatever id is already active. In eager
    mode the task runs inline inside the web request, and resetting the id
    there would strip it from every log line for the rest of that request.
    """
    task_kwargs = kwargs or {}
    request_id = task_kwargs.get("request_id") or task_kwargs.get("_request_id")
    if not request_id:
        return
    from dormcycle.middleware import get_request_id, set_request_id

    _previous_request_ids[str(task_id)] = get_request_id()
    set_request_id(str(request_id))


@task_postrun.connect
def on_task_postrun(sender: Any = None, task_id: Any = None, **_kw: Any) -> None:
    """Restore the request id that was active before the task bound its own.

    Without this a worker process would keep the id of the last task that
    carried one and stamp it on unrelated tasks that follow.
    """
    previous = _previous_request_ids.pop(str(task_id), None)
    if previous is None:
        return
    from dormcycle.middleware import set_request_id

    set_request_id(previous)


@app.task(name=SCHEDULED_JOBS_TASK)
def run_scheduled_job_set(job_set: str) -> dict[str, Any]:
    """Run one listings.ops job set ("tick", "daily" or "weekly") from beat.

    Beat schedules this single task once per job set instead of listing the
    individual jobs. The job sets in listings/ops.py are also what the HTTP
    maintenance endpoint and `manage.py run_maintenance` run, so a job added
    there is picked up by every scheduler and the three cannot drift apart.
    """
    from listings.ops import run_scheduled_jobs

    results = run_scheduled_jobs([job_set])
    failed = sorted(name for name, result in results.items() if not result.get("ok"))
    if failed:
        logger.error("Scheduled job set %s finished with failures: %s", job_set, ", ".join(failed))
    else:
        logger.info("Scheduled job set %s finished: %d job(s) ok", job_set, len(results))
    return {"job_set": job_set, "ok": not failed, "jobs": sorted(results), "failed": failed}

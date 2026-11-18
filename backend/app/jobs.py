"""Putting work on the Redis queue (RQ). With QUEUE_BACKEND=background, work runs inside the API process instead
(used by tests and for running without Redis)."""

import asyncio
import logging
from datetime import datetime

from fastapi import BackgroundTasks

from app.config import settings

log = logging.getLogger("app.jobs")
QUEUE_NAME = "actionpilot"
RETRY_INTERVALS = [10, 30, 90]  # seconds between attempts


def _queue():
    from redis import Redis
    from rq import Queue

    return Queue(QUEUE_NAME, connection=Redis.from_url(settings.redis_url))


def _retry():
    from rq import Retry

    return Retry(max=settings.process_max_retries, interval=RETRY_INTERVALS[: settings.process_max_retries])


async def enqueue_advance(meeting_id: str, background: BackgroundTasks | None, decision: dict | None = None) -> None:
    """Run (or continue) the meeting's agent graph. `decision` resumes it after human approval."""
    if settings.queue_backend == "background":
        from app.agent.runner import run_with_status

        if background is not None:
            background.add_task(run_with_status, meeting_id, decision, 1, True, False)
        else:
            asyncio.get_running_loop().create_task(run_with_status(meeting_id, decision, 1, True, False))
        return
    await asyncio.to_thread(
        lambda: _queue().enqueue(
            "app.worker.advance_job", meeting_id, decision, retry=_retry(), job_timeout=900, failure_ttl=7 * 86400
        )
    )


async def schedule_reminder(reminder_id: int, when: datetime) -> None:
    if settings.queue_backend == "background":
        log.info("Reminder %s stored for %s (background mode: not scheduled)", reminder_id, when.isoformat())
        return
    await asyncio.to_thread(lambda: _queue().enqueue_at(when, "app.worker.reminder_job", reminder_id))


def check_queue() -> None:
    if settings.queue_backend == "rq":
        from redis import Redis

        Redis.from_url(settings.redis_url).ping()

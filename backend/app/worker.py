"""Background worker: runs the meeting agent and delivers reminders. Start with:  uv run python -m app.worker"""

import asyncio
import logging
import sys

from app import db
from app.agent.runner import run_with_status
from app.config import settings
from app.reminders import deliver_reminder

log = logging.getLogger("app.worker")


async def _with_db(coro_fn, *args):
    await db.open_pool()  # each job runs in its own event loop, so it gets its own pool
    try:
        return await coro_fn(*args)
    finally:
        await db.close_db()


def advance_job(meeting_id: str, decision: dict | None = None) -> None:
    """Called by RQ. Raising makes RQ schedule a retry (10s, 30s, 90s)."""
    from rq import get_current_job

    job = get_current_job()
    retries_left = (job.retries_left or 0) if job else 0
    attempt = settings.process_max_retries + 1 - retries_left
    asyncio.run(_with_db(run_with_status, meeting_id, decision, attempt, retries_left == 0))


def reminder_job(reminder_id: int) -> None:
    asyncio.run(_with_db(deliver_reminder, reminder_id))


def main() -> None:
    from redis import Redis
    from rq import SimpleWorker, Worker

    from app.jobs import QUEUE_NAME

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(db.create_schema())
    worker_class = SimpleWorker if sys.platform == "darwin" else Worker  # macOS can't fork safely
    worker = worker_class([QUEUE_NAME], connection=Redis.from_url(settings.redis_url))
    log.info("Worker listening on queue %r (%s)", QUEUE_NAME, worker_class.__name__)
    worker.work(with_scheduler=True)  # the scheduler runs delayed retries and scheduled reminders


if __name__ == "__main__":
    main()

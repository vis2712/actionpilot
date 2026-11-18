"""Starting, continuing and resuming a meeting's graph run, with status bookkeeping for retries."""

import logging
import time

from langgraph.types import Command

from app.agent.graph import GRAPH
from app.db import get_checkpointer, get_pool

log = logging.getLogger("app.agent")


def thread_config(meeting_id: str, run_no: int) -> dict:
    # One LangGraph "thread" per processing run. Reprocessing from scratch bumps run_no, giving a fresh thread.
    return {"configurable": {"thread_id": f"{meeting_id}:{run_no}"}}


async def advance(meeting_id: str, decision: dict | None = None) -> str:
    """Moves the meeting's graph forward as far as it can go. Safe to call repeatedly (that's what retries do):
    - no checkpoint yet          → start from the beginning
    - stopped by an error        → continue from the step that failed (finished steps are not re-run)
    - waiting for approval       → resume with `decision`, or do nothing if there is no decision
    - finished                   → do nothing
    Returns what happened, for logs and tests."""
    async with get_pool().connection() as conn:
        cur = await conn.execute("SELECT owner_id, run_no FROM meetings WHERE id = %s", (meeting_id,))
        m = await cur.fetchone()
    if m is None:
        return "deleted"
    graph = GRAPH.compile(checkpointer=get_checkpointer())
    config = thread_config(meeting_id, m["run_no"])
    snap = await graph.aget_state(config)

    if snap.interrupts:
        if decision is None:
            return "waiting_for_approval"
        await graph.ainvoke(Command(resume=decision), config)
        return "resumed"
    if snap.next:
        await graph.ainvoke(None, config)
        return "continued"
    if snap.created_at is None:
        start = {
            "meeting_id": meeting_id,
            "owner_id": str(m["owner_id"]),
            "run_no": m["run_no"],
            "started_at": time.time(),
            "messages": [],
            "steps": 0,
        }
        await graph.ainvoke(start, config)
        return "started"
    return "already_finished"


async def run_with_status(
    meeting_id: str, decision: dict | None, attempt: int, final_attempt: bool, reraise: bool = True
) -> str | None:
    """Wraps advance() for the queue: records attempts and turns errors into a visible status.
    The worker re-raises so RQ schedules a retry; background mode (no queue) just records the failure."""
    async with get_pool().connection() as conn:
        await conn.execute("UPDATE meetings SET attempts = attempts + 1 WHERE id = %s", (meeting_id,))
    try:
        result = await advance(meeting_id, decision)
        log.info("meeting %s: %s (attempt %s)", meeting_id, result, attempt)
        return result
    except Exception as e:
        message = f"{type(e).__name__}: {e}"[:500]
        status, error = (
            ("failed", message) if final_attempt else ("retrying", f"Attempt {attempt} failed, retrying: {message}")
        )
        async with get_pool().connection() as conn:
            await conn.execute(
                "UPDATE meetings SET status = %s, error = %s, updated_at = now() WHERE id = %s",
                (status, error, meeting_id),
            )
        log.exception("meeting %s failed on attempt %s", meeting_id, attempt)
        if reraise:
            raise
        return None

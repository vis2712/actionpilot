"""Search across past meetings, and usage/quality stats for the dashboard."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.auth import current_user
from app.db import get_pool
from app.search import search_meetings

router = APIRouter(tags=["insights"])
User = Annotated[str, Depends(current_user)]


@router.get("/search")
async def search(user_id: User, q: Annotated[str, Query(min_length=2, max_length=500)], k: int = 8):
    return await search_meetings(user_id, q, k=min(k, 20))


@router.get("/stats")
async def stats(user_id: User):
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            """SELECT count(*) AS meetings,
                      count(*) FILTER (WHERE status = 'completed') AS completed,
                      count(*) FILTER (WHERE status = 'awaiting_approval') AS awaiting_approval,
                      count(*) FILTER (WHERE status = 'failed') AS failed,
                      round(avg(processing_ms))::int AS avg_processing_ms,
                      percentile_cont(0.95) WITHIN GROUP (ORDER BY processing_ms)::int AS p95_processing_ms,
                      coalesce(sum((usage->>'prompt_tokens')::int), 0) AS prompt_tokens,
                      coalesce(sum((usage->>'completion_tokens')::int), 0) AS completion_tokens,
                      coalesce(round(sum((usage->>'cost_usd')::numeric), 4), 0)::float AS cost_usd
               FROM meetings WHERE owner_id = %s""",
            (user_id,),
        )
        meetings = await cur.fetchone()
        cur = await conn.execute(
            """SELECT count(*) AS total,
                      count(*) FILTER (WHERE status = 'open') AS open,
                      count(*) FILTER (WHERE status = 'done') AS done,
                      count(*) FILTER (WHERE status = 'open' AND due_date < current_date) AS overdue
               FROM tasks WHERE owner_id = %s""",
            (user_id,),
        )
        tasks = await cur.fetchone()
        cur = await conn.execute(
            """SELECT count(*) FILTER (WHERE status = 'sent') AS sent,
                      count(*) FILTER (WHERE status = 'rejected') AS rejected,
                      count(*) FILTER (WHERE cardinality(blocked_recipients) > 0) AS with_blocked_recipients
               FROM emails WHERE owner_id = %s""",
            (user_id,),
        )
        emails = await cur.fetchone()
        cur = await conn.execute(
            """SELECT count(*) FILTER (WHERE status = 'scheduled') AS scheduled,
                      count(*) FILTER (WHERE status = 'sent') AS sent
               FROM reminders WHERE owner_id = %s""",
            (user_id,),
        )
        reminders = await cur.fetchone()
    return {"meetings": meetings, "tasks": tasks, "emails": emails, "reminders": reminders}

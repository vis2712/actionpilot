"""Delivering a scheduled reminder (runs in the worker when its time comes)."""

import logging
from datetime import UTC, datetime, timedelta

from app.db import get_pool
from app.emailer import send_email

log = logging.getLogger("app.reminders")


async def deliver_reminder(reminder_id: int) -> str:
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            """SELECT r.id, r.status, r.remind_at, t.title, t.due_date, t.assignee, t.status AS task_status,
                      u.email, m.title AS meeting_title
               FROM reminders r
               JOIN tasks t ON t.id = r.task_id
               JOIN users u ON u.id = r.owner_id
               LEFT JOIN meetings m ON m.id = t.meeting_id
               WHERE r.id = %s""",
            (reminder_id,),
        )
        r = await cur.fetchone()
        if r is None or r["status"] != "scheduled":
            return "skipped"
        if r["task_status"] == "done":
            await conn.execute("UPDATE reminders SET status = 'cancelled' WHERE id = %s", (reminder_id,))
            return "cancelled"
        if r["remind_at"] > datetime.now(UTC) + timedelta(minutes=1):
            return "not_due"  # the reminder was moved later; a newer job will deliver it

    body = (
        f"Reminder: {r['title']}\n"
        f"Owner: {r['assignee'] or 'unassigned'}\n"
        f"Due: {r['due_date'] or 'no date'}\n"
        + (f"From meeting: {r['meeting_title']}\n" if r["meeting_title"] else "")
        + "\nMark it done in ActionPilot when it's finished."
    )
    await send_email([r["email"]], f"Reminder: {r['title']}", body)
    async with get_pool().connection() as conn:
        await conn.execute("UPDATE reminders SET status = 'sent', sent_at = now() WHERE id = %s", (reminder_id,))
    log.info("reminder %s sent to %s", reminder_id, r["email"])
    return "sent"

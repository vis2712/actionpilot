"""The tools the agent can call. Each one is idempotent (safe to run twice) and scoped to one user and meeting.

Guardrails live here, in code, not in the prompt: the model can only email allowed recipients, can only touch
tasks of the current meeting, and can't schedule reminders in the past.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app import jobs
from app.config import settings
from app.db import get_pool
from app.search import search_meetings


@dataclass
class ToolContext:
    owner_id: str
    owner_email: str
    meeting_id: str
    item_count: int
    participants: list[str] = field(default_factory=list)

    @property
    def allowed_recipients(self) -> list[str]:
        return sorted({*(p.lower() for p in self.participants), self.owner_email.lower()})


TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create (or update) the task for one action item from the meeting notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_index": {"type": "integer", "description": "0-based index of the action item in the notes"},
                    "title": {"type": "string"},
                    "assignee": {"type": "string", "description": "Person responsible, or empty"},
                    "due_date": {"type": "string", "description": "YYYY-MM-DD, or empty if none"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["item_index", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_reminder",
            "description": "Schedule a reminder email to the user about a task. Use for tasks that have a due date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "The task_id returned by create_task"},
                    "remind_at": {"type": "string", "description": "ISO 8601 datetime, e.g. 2026-11-19T09:00:00+05:30"},
                },
                "required": ["task_id", "remind_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_past_meetings",
            "description": "Search the user's earlier meetings (summaries and transcripts) for related context.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_follow_up_email",
            "description": "Save the follow-up email draft. A human must approve it before it is sent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "array", "items": {"type": "string"}, "description": "Recipient email addresses"},
                    "subject": {"type": "string"},
                    "body": {"type": "string", "description": "Plain-text email body"},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
]


class ToolError(Exception):
    """Returned to the model as {"error": ...} so it can correct itself."""


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ToolError(f"due_date must be YYYY-MM-DD, got {value!r}") from None


async def create_task(
    ctx: ToolContext,
    item_index: int,
    title: str,
    assignee: str | None = None,
    due_date: str | None = None,
    priority: str = "medium",
) -> dict:
    if not isinstance(item_index, int) or not 0 <= item_index < ctx.item_count:
        raise ToolError(f"item_index must be between 0 and {ctx.item_count - 1}")
    if priority not in ("low", "medium", "high"):
        priority = "medium"
    due = _parse_date(due_date)
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            """INSERT INTO tasks (owner_id, meeting_id, item_index, title, assignee, due_date, priority)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (meeting_id, item_index) DO UPDATE
                 SET title = EXCLUDED.title, assignee = EXCLUDED.assignee,
                     due_date = EXCLUDED.due_date, priority = EXCLUDED.priority
               RETURNING id""",
            (
                ctx.owner_id,
                ctx.meeting_id,
                item_index,
                title.strip()[:300],
                (assignee or "").strip()[:100] or None,
                due,
                priority,
            ),
        )
        row = await cur.fetchone()
    return {"task_id": row["id"], "title": title, "due_date": due.isoformat() if due else None}


async def schedule_reminder(ctx: ToolContext, task_id: int, remind_at: str) -> dict:
    try:
        when = datetime.fromisoformat(str(remind_at))
    except ValueError:
        raise ToolError("remind_at must be an ISO 8601 datetime") from None
    if when.tzinfo is None:
        when = when.replace(tzinfo=ZoneInfo(settings.timezone))
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "SELECT id FROM tasks WHERE id = %s AND owner_id = %s AND meeting_id = %s",
            (task_id, ctx.owner_id, ctx.meeting_id),
        )
        if await cur.fetchone() is None:
            raise ToolError(f"task {task_id} is not a task from this meeting")
        if when <= datetime.now(UTC):
            return {"task_id": task_id, "scheduled": False, "reason": "that time is in the past, no reminder set"}
        cur = await conn.execute(
            """INSERT INTO reminders (owner_id, task_id, remind_at) VALUES (%s, %s, %s)
               ON CONFLICT (task_id) DO UPDATE SET remind_at = EXCLUDED.remind_at, status = 'scheduled'
               RETURNING id""",
            (ctx.owner_id, task_id, when),
        )
        reminder_id = (await cur.fetchone())["id"]
    await jobs.schedule_reminder(reminder_id, when)
    return {"task_id": task_id, "reminder_id": reminder_id, "scheduled": True, "remind_at": when.isoformat()}


async def search_past_meetings(ctx: ToolContext, query: str) -> dict:
    hits = await search_meetings(ctx.owner_id, query, k=3, exclude_meeting=ctx.meeting_id)
    return {
        "results": [
            {"meeting": h["title"], "date": h["meeting_date"].isoformat(), "excerpt": h["content"][:500]} for h in hits
        ]
    }


async def draft_follow_up_email(ctx: ToolContext, to: list[str], subject: str, body: str) -> dict:
    allowed = set(ctx.allowed_recipients)
    requested = [str(x).strip().lower() for x in (to if isinstance(to, list) else [to]) if str(x).strip()]
    recipients = [x for x in dict.fromkeys(requested) if x in allowed]
    blocked = [x for x in dict.fromkeys(requested) if x not in allowed]
    if not recipients:
        recipients = [ctx.owner_email.lower()]
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            """INSERT INTO emails (owner_id, meeting_id, recipients, blocked_recipients, subject, body)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (meeting_id) DO UPDATE
                 SET recipients = EXCLUDED.recipients, blocked_recipients = EXCLUDED.blocked_recipients,
                     subject = EXCLUDED.subject, body = EXCLUDED.body, updated_at = now()
                 WHERE emails.status = 'draft'
               RETURNING id""",
            (ctx.owner_id, ctx.meeting_id, recipients, blocked, subject.strip()[:200], body.strip()[:20_000]),
        )
        row = await cur.fetchone()
    result: dict = {"email_id": row["id"] if row else None, "to": recipients, "status": "draft, waiting for approval"}
    if blocked:
        result["blocked_recipients"] = blocked
        result["note"] = "Some recipients are not meeting participants and were removed."
    return result


TOOLS = {
    "create_task": create_task,
    "schedule_reminder": schedule_reminder,
    "search_past_meetings": search_past_meetings,
    "draft_follow_up_email": draft_follow_up_email,
}


async def run_tool(ctx: ToolContext, name: str, raw_args: str) -> dict:
    """Runs one tool call. Bad input becomes an error message for the model instead of a crash."""
    fn = TOOLS.get(name)
    if fn is None:
        return {"error": f"unknown tool {name!r}"}
    try:
        args = json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ToolError("arguments must be a JSON object")
        return await fn(ctx, **args)
    except (ToolError, json.JSONDecodeError) as e:
        return {"error": str(e)}
    except TypeError as e:  # wrong or missing argument names
        return {"error": f"bad arguments: {e}"}

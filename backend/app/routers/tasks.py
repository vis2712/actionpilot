"""Tasks created by the agent (or by hand / through MCP): list, create, update, delete."""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth import current_user
from app.db import get_pool

router = APIRouter(prefix="/tasks", tags=["tasks"])
User = Annotated[str, Depends(current_user)]

TASK_SQL = """
SELECT t.id, t.meeting_id, m.title AS meeting_title, t.title, t.assignee, t.due_date, t.priority, t.status,
       t.created_at, r.remind_at, r.status AS reminder_status,
       (t.status = 'open' AND t.due_date < current_date) AS overdue
FROM tasks t
LEFT JOIN meetings m ON m.id = t.meeting_id
LEFT JOIN reminders r ON r.task_id = t.id
"""


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    assignee: str | None = Field(default=None, max_length=100)
    due_date: date | None = None
    priority: Literal["low", "medium", "high"] = "medium"


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    assignee: str | None = Field(default=None, max_length=100)
    due_date: date | None = None
    priority: Literal["low", "medium", "high"] | None = None
    status: Literal["open", "done"] | None = None


@router.get("")
async def list_tasks(
    user_id: User,
    status_filter: Annotated[Literal["open", "done", "all"], Query(alias="status")] = "all",
    limit: int = 200,
):
    where = "WHERE t.owner_id = %s" + ("" if status_filter == "all" else " AND t.status = %s")
    params: list = [user_id] + ([] if status_filter == "all" else [status_filter])
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            f"{TASK_SQL} {where} ORDER BY t.status, t.due_date NULLS LAST, t.id DESC LIMIT %s",
            (*params, min(limit, 500)),
        )
        return await cur.fetchall()


async def _get(conn, task_id: int, user_id: str) -> dict:
    cur = await conn.execute(f"{TASK_SQL} WHERE t.id = %s AND t.owner_id = %s", (task_id, user_id))
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    return row


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_task(body: TaskCreate, user_id: User):
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            """INSERT INTO tasks (owner_id, title, assignee, due_date, priority)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (user_id, body.title, body.assignee, body.due_date, body.priority),
        )
        task_id = (await cur.fetchone())["id"]
        return await _get(conn, task_id, user_id)


@router.patch("/{task_id}")
async def update_task(task_id: int, body: TaskUpdate, user_id: User):
    fields = body.model_dump(exclude_unset=True)
    async with get_pool().connection() as conn:
        await _get(conn, task_id, user_id)
        if fields:
            cols = ", ".join(f"{k} = %s" for k in fields)  # keys come from the pydantic model, not the user
            await conn.execute(f"UPDATE tasks SET {cols} WHERE id = %s", (*fields.values(), task_id))
        if fields.get("status") == "done":  # no reminder needed for finished work
            await conn.execute(
                "UPDATE reminders SET status = 'cancelled' WHERE task_id = %s AND status = 'scheduled'", (task_id,)
            )
        return await _get(conn, task_id, user_id)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: int, user_id: User):
    async with get_pool().connection() as conn:
        await _get(conn, task_id, user_id)
        await conn.execute("DELETE FROM tasks WHERE id = %s", (task_id,))

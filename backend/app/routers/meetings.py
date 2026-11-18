"""Meetings: create (upload or paste), list, detail, human approval, retry, reprocess, delete."""

import re
from datetime import date
from pathlib import PurePath
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, EmailStr, Field

from app import storage
from app.agent.runner import thread_config
from app.auth import current_user
from app.config import settings
from app.db import get_checkpointer, get_pool
from app.jobs import enqueue_advance
from app.transcripts import clean_transcript, decode_text, kind_of

router = APIRouter(prefix="/meetings", tags=["meetings"])
User = Annotated[str, Depends(current_user)]

_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
MIN_TRANSCRIPT_CHARS = 20


def parse_participants(raw: str | list[str] | None) -> list[str]:
    items = raw if isinstance(raw, list) else re.split(r"[,;\s]+", raw or "")
    emails = [x.strip().lower() for x in items if x and x.strip()]
    bad = [e for e in emails if not _EMAIL.match(e)]
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Not valid email addresses: {', '.join(bad[:5])}")
    return list(dict.fromkeys(emails))[:50]


async def create_meeting(
    owner_id: str,
    title: str,
    meeting_date: date,
    participants: list[str],
    transcript: str | None,
    *,
    source: str = "upload",
    external_id: str | None = None,
    filename: str | None = None,
    content_type: str | None = None,
    storage_key: str | None = None,
    meeting_id: UUID | None = None,
) -> dict:
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            """INSERT INTO meetings (id, owner_id, title, meeting_date, participants, transcript, source, external_id,
                                     filename, content_type, storage_key)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id, title, meeting_date, status, source, created_at""",
            (
                meeting_id or uuid4(),
                owner_id,
                title[:200],
                meeting_date,
                participants,
                transcript,
                source,
                external_id,
                filename,
                content_type,
                storage_key,
            ),
        )
        return await cur.fetchone()


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload_meeting(
    user_id: User,
    background: BackgroundTasks,
    file: Annotated[UploadFile | None, File(description="Transcript (.txt .md .vtt .srt) or recording")] = None,
    transcript: Annotated[str | None, Form(description="Or paste the transcript text")] = None,
    title: Annotated[str | None, Form()] = None,
    meeting_date: Annotated[date | None, Form()] = None,
    participants: Annotated[
        str | None, Form(description="Comma-separated emails who may receive the follow-up")
    ] = None,
):
    """Creates a meeting and queues the agent. Returns 202 at once; poll GET /meetings/{id} for progress."""
    people = parse_participants(participants)
    when = meeting_date or date.today()
    meeting_id = uuid4()
    filename = content_type = key = None
    text: str | None = None

    if file is not None and file.filename:
        filename = PurePath(file.filename).name
        kind = kind_of(filename)
        if kind is None:
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                "Upload a transcript (.txt, .md, .vtt, .srt) or a recording (.mp3, .m4a, .wav, .webm, .ogg, .mp4)",
            )
        data = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
        if len(data) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"File is larger than {settings.max_upload_mb} MB")
        content_type = file.content_type
        if kind == "text":
            text = clean_transcript(decode_text(data), filename)
        else:
            key = f"{user_id}/{meeting_id}{PurePath(filename).suffix.lower()}"
            await storage.save(key, data)
    elif transcript:
        text = transcript.strip()
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Send a file or paste a transcript")

    if text is not None and len(text) < MIN_TRANSCRIPT_CHARS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The transcript is too short")

    default_title = PurePath(filename).stem.replace("_", " ") if filename else f"Meeting on {when.isoformat()}"
    meeting = await create_meeting(
        user_id,
        (title or "").strip() or default_title,
        when,
        people,
        text,
        filename=filename,
        content_type=content_type,
        storage_key=key,
        meeting_id=meeting_id,
    )
    await enqueue_advance(str(meeting["id"]), background)
    return meeting


@router.get("")
async def list_meetings(user_id: User, limit: int = 50):
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            """SELECT m.id, m.title, m.meeting_date, m.status, m.source, m.error, m.created_at, m.processing_ms,
                      (SELECT count(*) FROM tasks t WHERE t.meeting_id = m.id) AS task_count,
                      (SELECT status FROM emails e WHERE e.meeting_id = m.id) AS email_status
               FROM meetings m WHERE m.owner_id = %s ORDER BY m.created_at DESC LIMIT %s""",
            (user_id, min(limit, 200)),
        )
        return await cur.fetchall()


async def _owned(conn, meeting_id: UUID, user_id: str) -> dict:
    cur = await conn.execute("SELECT * FROM meetings WHERE id = %s AND owner_id = %s", (meeting_id, user_id))
    row = await cur.fetchone()
    if row is None:  # 404 (not 403) for other users' meetings, so ids can't be probed
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Meeting not found")
    return row


@router.get("/{meeting_id}")
async def get_meeting(meeting_id: UUID, user_id: User):
    async with get_pool().connection() as conn:
        m = await _owned(conn, meeting_id, user_id)
        cur = await conn.execute(
            """SELECT t.id, t.item_index, t.title, t.assignee, t.due_date, t.priority, t.status,
                      r.remind_at, r.status AS reminder_status
               FROM tasks t LEFT JOIN reminders r ON r.task_id = t.id
               WHERE t.meeting_id = %s ORDER BY t.item_index NULLS LAST, t.id""",
            (meeting_id,),
        )
        tasks = await cur.fetchall()
        cur = await conn.execute(
            """SELECT id, recipients, blocked_recipients, subject, body, status, error, sent_at
               FROM emails WHERE meeting_id = %s""",
            (meeting_id,),
        )
        email = await cur.fetchone()
        cur = await conn.execute(
            "SELECT step, detail, ms, created_at FROM run_steps WHERE meeting_id = %s AND run_no = %s ORDER BY id",
            (meeting_id, m["run_no"]),
        )
        steps = await cur.fetchall()
    m.pop("owner_id")
    m.pop("storage_key")
    return {**m, "tasks": tasks, "email": email, "steps": steps}


class ApprovalRequest(BaseModel):
    action: Literal["approve", "reject"]
    to: list[EmailStr] | None = Field(default=None, max_length=50)
    subject: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=20_000)


@router.post("/{meeting_id}/approval", status_code=status.HTTP_202_ACCEPTED)
async def approve_email(meeting_id: UUID, body: ApprovalRequest, user_id: User, background: BackgroundTasks):
    """The human-in-the-loop step: approve (optionally after editing) or reject the drafted follow-up email."""
    async with get_pool().connection() as conn, conn.transaction():
        await _owned(conn, meeting_id, user_id)
        # The conditional update makes double clicks safe: only one request can move it out of awaiting_approval.
        cur = await conn.execute(
            """UPDATE meetings SET status = %s, updated_at = now()
               WHERE id = %s AND status = 'awaiting_approval' RETURNING id""",
            ("sending" if body.action == "approve" else "finishing", meeting_id),
        )
        if await cur.fetchone() is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "This meeting is not waiting for approval")
        if body.action == "approve":
            await conn.execute(
                """UPDATE emails SET status = 'approved', updated_at = now(),
                          recipients = COALESCE(%s, recipients), subject = COALESCE(%s, subject),
                          body = COALESCE(%s, body)
                   WHERE meeting_id = %s""",
                ([str(e).lower() for e in body.to] if body.to else None, body.subject, body.body, meeting_id),
            )
        else:
            await conn.execute(
                "UPDATE emails SET status = 'rejected', updated_at = now() WHERE meeting_id = %s", (meeting_id,)
            )
    await enqueue_advance(str(meeting_id), background, decision={"action": body.action})
    return {"id": meeting_id, "action": body.action}


@router.post("/{meeting_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_meeting(meeting_id: UUID, user_id: User, background: BackgroundTasks):
    """Continues a failed run from the step that failed (finished steps are not repeated)."""
    async with get_pool().connection() as conn:
        m = await _owned(conn, meeting_id, user_id)
        if m["status"] != "failed":
            raise HTTPException(status.HTTP_409_CONFLICT, "Only failed meetings can be retried")
        await conn.execute("UPDATE meetings SET status = 'queued', error = NULL WHERE id = %s", (meeting_id,))
    await enqueue_advance(str(meeting_id), background)
    return {"id": meeting_id, "status": "queued"}


@router.post("/{meeting_id}/reprocess", status_code=status.HTTP_202_ACCEPTED)
async def reprocess_meeting(meeting_id: UUID, user_id: User, background: BackgroundTasks):
    """Runs the whole agent again from scratch (new notes, tasks and draft)."""
    async with get_pool().connection() as conn, conn.transaction():
        m = await _owned(conn, meeting_id, user_id)
        if m["status"] in ("queued", "transcribing", "analyzing", "sending", "finishing"):
            raise HTTPException(status.HTTP_409_CONFLICT, "The meeting is being processed right now")
        for table in ("tasks", "emails", "chunks"):
            await conn.execute(f"DELETE FROM {table} WHERE meeting_id = %s", (meeting_id,))
        await conn.execute(
            """UPDATE meetings SET status = 'queued', error = NULL, run_no = run_no + 1, summary = NULL,
                      decisions = '[]', notes = NULL, usage = '{}', processing_ms = NULL, updated_at = now()
               WHERE id = %s""",
            (meeting_id,),
        )
    await enqueue_advance(str(meeting_id), background)
    return {"id": meeting_id, "status": "queued"}


@router.delete("/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meeting(meeting_id: UUID, user_id: User):
    async with get_pool().connection() as conn:
        m = await _owned(conn, meeting_id, user_id)
        await conn.execute("DELETE FROM meetings WHERE id = %s", (meeting_id,))
    if m["storage_key"]:
        await storage.delete(m["storage_key"])
    checkpointer = get_checkpointer()
    for run_no in range(1, m["run_no"] + 1):
        await checkpointer.adelete_thread(thread_config(str(meeting_id), run_no)["configurable"]["thread_id"])

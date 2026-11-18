"""Webhook for meeting tools: "a meeting ended, here is the transcript".

Security: the sender signs `<timestamp>.<raw body>` with HMAC-SHA256 using WEBHOOK_SECRET and sends
    X-Signature: sha256=<hex digest>     X-Timestamp: <unix seconds>
Old timestamps are rejected (stops replays) and `external_id` makes deliveries idempotent: meeting tools retry
webhooks, and the same meeting must not be processed twice.
"""

import hashlib
import hmac
import time
from datetime import date
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response, status
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, EmailStr, Field, ValidationError

from app.config import settings
from app.db import get_pool
from app.jobs import enqueue_advance
from app.routers.meetings import create_meeting

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class MeetingWebhook(BaseModel):
    external_id: str = Field(min_length=1, max_length=200, description="The meeting's id in the source system")
    owner_email: EmailStr
    title: str = Field(min_length=1, max_length=200)
    transcript: str = Field(min_length=20, max_length=500_000)
    meeting_date: date | None = None
    participants: list[EmailStr] = Field(default=[], max_length=50)


def sign(body: bytes, timestamp: str, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def _verify(body: bytes, timestamp: str | None, signature: str | None) -> None:
    if not settings.webhook_secret:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhooks are disabled (set WEBHOOK_SECRET)")
    bad = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")
    if not timestamp or not signature or not timestamp.isdigit():
        raise bad
    if abs(time.time() - int(timestamp)) > settings.webhook_tolerance_seconds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Timestamp too old")
    if not hmac.compare_digest(sign(body, timestamp, settings.webhook_secret), signature):  # constant-time compare
        raise bad


@router.post("/meetings", status_code=status.HTTP_202_ACCEPTED)
async def meeting_webhook(
    request: Request,
    response: Response,
    background: BackgroundTasks,
    x_signature: Annotated[str | None, Header()] = None,
    x_timestamp: Annotated[str | None, Header()] = None,
):
    raw = await request.body()  # verify the exact bytes that were signed, before parsing anything
    _verify(raw, x_timestamp, x_signature)
    try:
        payload = MeetingWebhook.model_validate_json(raw)
    except ValidationError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, e.errors(include_url=False)) from None

    async with get_pool().connection() as conn:
        cur = await conn.execute("SELECT id FROM users WHERE email = %s", (payload.owner_email.lower(),))
        user = await cur.fetchone()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No ActionPilot user with that owner_email")
    if existing := await _existing(str(user["id"]), payload.external_id):
        response.status_code = status.HTTP_200_OK
        return existing

    try:
        meeting = await create_meeting(
            str(user["id"]),
            payload.title,
            payload.meeting_date or date.today(),
            [str(p).lower() for p in payload.participants],
            payload.transcript,
            source="webhook",
            external_id=payload.external_id,
        )
    except UniqueViolation:  # the same delivery arrived twice at the same moment
        response.status_code = status.HTTP_200_OK
        return await _existing(str(user["id"]), payload.external_id)
    await enqueue_advance(str(meeting["id"]), background)
    return {"id": meeting["id"], "status": meeting["status"], "duplicate": False}


async def _existing(owner_id: str, external_id: str) -> dict | None:
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "SELECT id, status FROM meetings WHERE owner_id = %s AND external_id = %s", (owner_id, external_id)
        )
        row = await cur.fetchone()
    return {"id": row["id"], "status": row["status"], "duplicate": True} if row else None

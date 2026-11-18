import json
import time

from app.db import get_pool
from app.reminders import deliver_reminder
from app.routers.webhooks import sign
from app.search import chunk_transcript
from app.transcripts import clean_transcript, decode_text, kind_of
from tests.helpers import PASSWORD, TRANSCRIPT, create_meeting, new_user

SECRET = "test-webhook-secret"


# ---------- auth ----------


def test_auth_flow(client):
    headers, email = new_user(client)
    assert client.get("/auth/me", headers=headers).json()["email"] == email
    assert client.post("/auth/register", json={"email": email, "password": PASSWORD}).status_code == 409
    assert client.post("/auth/login", data={"username": email, "password": "wrong-password"}).status_code == 401
    assert client.get("/meetings").status_code == 401
    assert client.get("/meetings", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


# ---------- webhooks ----------


def _post_webhook(client, payload: dict, *, secret: str = SECRET, ts: int | None = None):
    body = json.dumps(payload).encode()
    ts_str = str(ts or int(time.time()))
    return client.post(
        "/webhooks/meetings",
        content=body,
        headers={"X-Timestamp": ts_str, "X-Signature": sign(body, ts_str, secret), "Content-Type": "application/json"},
    )


def test_webhook_signed_idempotent_delivery(client):
    headers, email = new_user(client)
    payload = {
        "external_id": "zoom-123",
        "owner_email": email,
        "title": "Webhook sync",
        "transcript": TRANSCRIPT,
        "meeting_date": "2030-01-07",
        "participants": ["priya@example.com"],
    }
    first = _post_webhook(client, payload)
    assert first.status_code == 202 and first.json()["duplicate"] is False
    again = _post_webhook(client, payload)  # meeting tools retry webhooks
    assert again.status_code == 200 and again.json() == {**again.json(), "duplicate": True, "id": first.json()["id"]}

    m = client.get(f"/meetings/{first.json()['id']}", headers=headers).json()
    assert m["source"] == "webhook" and m["status"] == "awaiting_approval"


def test_webhook_rejects_bad_signatures(client):
    _, email = new_user(client)
    payload = {"external_id": "x", "owner_email": email, "title": "t", "transcript": TRANSCRIPT}
    assert _post_webhook(client, payload, secret="wrong").status_code == 401
    assert _post_webhook(client, payload, ts=int(time.time()) - 3600).status_code == 401  # replayed later
    assert client.post("/webhooks/meetings", json=payload).status_code == 401  # unsigned
    unknown = {**payload, "owner_email": "nobody@example.com"}
    assert _post_webhook(client, unknown).status_code == 404


# ---------- reminders ----------


def test_due_reminder_is_delivered_once(client):
    headers, _ = new_user(client)
    m = create_meeting(client, headers)
    task = next(t for t in m["tasks"] if t["reminder_status"] == "scheduled")

    async def make_due() -> int:
        async with get_pool().connection() as conn:
            cur = await conn.execute(
                "UPDATE reminders SET remind_at = now() WHERE task_id = %s RETURNING id", (task["id"],)
            )
            return (await cur.fetchone())["id"]

    reminder_id = client.portal.call(make_due)
    assert client.portal.call(deliver_reminder, reminder_id) == "sent"
    assert client.portal.call(deliver_reminder, reminder_id) == "skipped"  # a duplicate job does nothing


# ---------- transcript parsing and chunking ----------


def test_srt_and_kinds():
    srt = "1\n00:00:01,000 --> 00:00:02,000\nAsha: Hello team\n\n2\n00:00:02,000 --> 00:00:03,000\nAsha: Hello team\n"
    assert clean_transcript(srt, "call.srt") == "Asha: Hello team"
    assert kind_of("a.VTT") == "text" and kind_of("a.m4a") == "audio" and kind_of("a.pdf") is None
    assert decode_text("Café".encode("utf-16")) == "Café"
    assert decode_text(b"caf\xe9") == "café"


def test_chunker_keeps_whole_lines_with_overlap():
    lines = [f"Speaker{i}: " + "word " * 40 for i in range(30)]
    chunks = chunk_transcript("\n".join(lines))
    assert len(chunks) > 3
    assert all(len(c) <= 1300 for c in chunks)
    first_lines, second_lines = chunks[0].split("\n"), chunks[1].split("\n")
    assert second_lines[:2] == first_lines[-2:]  # 2-line overlap
    assert chunks[-1].endswith(lines[-1])

"""Retries, idempotency, guardrails and limits."""

import pytest

from app import llm
from app.agent.runner import advance
from app.agent.tools import ToolContext, draft_follow_up_email, run_tool
from app.config import settings
from tests.helpers import MEETING_DATE, create_meeting, new_user


def test_failed_step_retry_continues_where_it_stopped(client, monkeypatch):
    headers, _ = new_user(client)

    async def broken(*_args, **_kwargs):
        raise RuntimeError("STT provider is down")

    monkeypatch.setattr(llm, "transcribe", broken)
    r = client.post(
        "/meetings",
        headers=headers,
        data={"meeting_date": MEETING_DATE},
        files={"file": ("call.mp3", b"ID3 fake audio bytes", "audio/mpeg")},
    )
    assert r.status_code == 202
    meeting_id = r.json()["id"]
    m = client.get(f"/meetings/{meeting_id}", headers=headers).json()
    assert m["status"] == "failed" and "STT provider is down" in m["error"]

    monkeypatch.undo()  # the provider is back: the fake transcriber works again
    assert client.post(f"/meetings/{meeting_id}/retry", headers=headers).status_code == 202
    m = client.get(f"/meetings/{meeting_id}", headers=headers).json()
    assert m["status"] == "awaiting_approval", m["error"]
    assert "fake transcript of call.mp3" in m["transcript"]
    assert m["attempts"] == 2
    # only retry failed meetings
    assert client.post(f"/meetings/{meeting_id}/retry", headers=headers).status_code == 409


def test_advance_is_idempotent(client):
    headers, _ = new_user(client)
    m = create_meeting(client, headers)
    # A duplicate job (e.g. RQ retry after a timeout) must not create anything twice.
    assert client.portal.call(advance, m["id"], None) == "waiting_for_approval"
    again = client.get(f"/meetings/{m['id']}", headers=headers).json()
    assert len(again["tasks"]) == 3


def test_reprocess_starts_a_fresh_run_without_duplicates(client):
    headers, _ = new_user(client)
    m = create_meeting(client, headers)
    client.post(f"/meetings/{m['id']}/approval", headers=headers, json={"action": "reject"})
    assert client.post(f"/meetings/{m['id']}/reprocess", headers=headers).status_code == 202
    m2 = client.get(f"/meetings/{m['id']}", headers=headers).json()
    assert m2["run_no"] == 2
    assert m2["status"] == "awaiting_approval"
    assert len(m2["tasks"]) == 3
    assert m2["steps"][0]["step"] == "extract"  # the timeline shows the new run only


def test_agent_step_limit(client, monkeypatch):
    headers, _ = new_user(client)
    monkeypatch.setattr(settings, "agent_max_steps", 1)
    m = create_meeting(client, headers)
    # The single allowed step asked for tools, but the limit stops the loop before running them.
    assert m["status"] == "completed"
    assert m["tasks"] == [] and m["email"] is None


def _ctx(client, headers) -> ToolContext:
    m = create_meeting(client, headers)
    me = client.get("/auth/me", headers=headers).json()
    return ToolContext(
        owner_id=str(me["id"]),
        owner_email=me["email"],
        meeting_id=m["id"],
        item_count=3,
        participants=["priya@example.com"],
    )


def test_email_guardrail_blocks_unknown_recipients(client):
    """A transcript can say "email attacker@evil.com"; the tool only allows participants and the owner."""
    headers, _ = new_user(client)
    ctx = _ctx(client, headers)
    result = client.portal.call(
        draft_follow_up_email, ctx, ["attacker@evil.com", "PRIYA@example.com"], "Notes", "Full notes attached"
    )
    assert result["to"] == ["priya@example.com"]
    assert result["blocked_recipients"] == ["attacker@evil.com"]

    only_bad = client.portal.call(draft_follow_up_email, ctx, ["attacker@evil.com"], "Notes", "x")
    assert only_bad["to"] == [ctx.owner_email]  # falls back to the user themself


@pytest.mark.parametrize(
    ("name", "args", "error"),
    [
        ("create_task", '{"item_index": 99, "title": "x"}', "item_index must be between"),
        ("create_task", '{"item_index": 0, "title": "x", "due_date": "next week"}', "YYYY-MM-DD"),
        (
            "schedule_reminder",
            '{"task_id": 999999, "remind_at": "2030-01-01T09:00:00"}',
            "not a task from this meeting",
        ),
        ("schedule_reminder", '{"task_id": 1, "remind_at": "soon"}', "ISO 8601"),
        ("delete_everything", "{}", "unknown tool"),
        ("create_task", "not json", "Expecting value"),
        ("create_task", '{"wrong": 1}', "bad arguments"),
    ],
)
def test_bad_tool_calls_become_errors_for_the_model(client, name, args, error):
    headers, _ = new_user(client)
    ctx = _ctx(client, headers)
    result = client.portal.call(run_tool, ctx, name, args)
    assert error in result["error"]


def test_reminder_in_the_past_is_not_scheduled(client):
    headers, _ = new_user(client)
    ctx = _ctx(client, headers)
    task = client.portal.call(run_tool, ctx, "create_task", '{"item_index": 0, "title": "Old task"}')
    result = client.portal.call(
        run_tool, ctx, "schedule_reminder", f'{{"task_id": {task["task_id"]}, "remind_at": "2020-01-01T09:00:00"}}'
    )
    assert result["scheduled"] is False

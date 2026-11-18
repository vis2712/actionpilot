"""The main story: transcript in → notes, tasks, reminders, email draft → human approval → sent."""

from tests.helpers import MEETING_DATE, create_meeting, new_user


def test_meeting_runs_until_human_approval(client):
    headers, owner = new_user(client)
    m = create_meeting(client, headers)

    assert m["status"] == "awaiting_approval", m.get("error")
    assert "launch" in m["summary"].lower()
    assert m["decisions"] == ["We agreed to launch the beta on 2030-01-20."]

    tasks = {t["assignee"]: t for t in m["tasks"]}
    assert set(tasks) == {"Priya", "Rahul", "Ananya"}
    assert tasks["Priya"]["due_date"] == "2030-01-11"  # "by Friday" after Monday 7 Jan
    assert tasks["Rahul"]["due_date"] == "2030-01-09" and tasks["Rahul"]["priority"] == "high"
    assert tasks["Ananya"]["due_date"] is None
    # reminders only for tasks with a due date, the day before at 09:00
    assert tasks["Priya"]["reminder_status"] == "scheduled"
    assert tasks["Priya"]["remind_at"].startswith("2030-01-10T03:30")  # 09:00 IST in UTC
    assert tasks["Ananya"]["reminder_status"] is None

    email = m["email"]
    assert email["status"] == "draft"
    assert sorted(email["recipients"]) == sorted(["priya@example.com", "rahul@example.com", owner])
    assert "Fix the payment bug" in email["body"]

    steps = [s["step"] for s in m["steps"]]
    assert steps[:3] == ["extract", "index", "agent"]
    assert {
        "tool:create_task",
        "tool:schedule_reminder",
        "tool:search_past_meetings",
        "tool:draft_follow_up_email",
    } <= set(steps)
    assert m["usage"]["llm_calls"] >= 2
    assert m["processing_ms"] is not None


def test_approve_with_edits_sends_email(client):
    headers, _ = new_user(client)
    m = create_meeting(client, headers)
    r = client.post(
        f"/meetings/{m['id']}/approval",
        headers=headers,
        json={"action": "approve", "subject": "Edited subject", "to": ["priya@example.com"]},
    )
    assert r.status_code == 202, r.text
    m = client.get(f"/meetings/{m['id']}", headers=headers).json()
    assert m["status"] == "completed"
    assert m["email"]["status"] == "sent"
    assert m["email"]["subject"] == "Edited subject"
    assert m["email"]["recipients"] == ["priya@example.com"]
    assert [s["step"] for s in m["steps"]][-2:] == ["send_email", "finish"]

    # a second click must not resume (and send) again
    again = client.post(f"/meetings/{m['id']}/approval", headers=headers, json={"action": "approve"})
    assert again.status_code == 409


def test_reject_does_not_send(client):
    headers, _ = new_user(client)
    m = create_meeting(client, headers)
    assert client.post(f"/meetings/{m['id']}/approval", headers=headers, json={"action": "reject"}).status_code == 202
    m = client.get(f"/meetings/{m['id']}", headers=headers).json()
    assert m["status"] == "completed"
    assert m["email"]["status"] == "rejected"
    assert "send_email" not in [s["step"] for s in m["steps"]]


def test_meeting_without_action_items_completes_without_approval(client):
    headers, _ = new_user(client)
    m = create_meeting(
        client, headers, transcript="Host: Quick status round, nothing new this week.\nAsha: All good here."
    )
    assert m["status"] in ("awaiting_approval", "completed")
    assert m["tasks"] == []


def test_vtt_upload_is_cleaned(client):
    headers, _ = new_user(client)
    vtt = (
        "WEBVTT\n\n1\n00:00:01.000 --> 00:00:04.000\n<v Priya>I'll draft the press release by Friday.</v>\n\n"
        "2\n00:00:05.000 --> 00:00:07.000\n<v Rahul>We agreed to ship on Monday.</v>\n"
    )
    r = client.post(
        "/meetings",
        headers=headers,
        data={"meeting_date": MEETING_DATE},
        files={"file": ("standup.vtt", vtt.encode(), "text/vtt")},
    )
    assert r.status_code == 202, r.text
    m = client.get(f"/meetings/{r.json()['id']}", headers=headers).json()
    assert m["title"] == "standup"
    assert m["transcript"].startswith("Priya: I'll draft the press release by Friday.")
    assert m["tasks"][0]["title"] == "Draft the press release"


def test_upload_validation(client):
    headers, _ = new_user(client)
    bad_type = client.post(
        "/meetings", headers=headers, files={"file": ("x.exe", b"MZ....", "application/octet-stream")}
    )
    assert bad_type.status_code == 415
    assert client.post("/meetings", headers=headers, data={"transcript": "too short"}).status_code == 422
    assert client.post("/meetings", headers=headers, data={}).status_code == 422
    bad_people = client.post(
        "/meetings",
        headers=headers,
        data={"transcript": "Host: a long enough transcript here.", "participants": "nope"},
    )
    assert bad_people.status_code == 422


def test_tasks_api_and_reminder_cancel(client):
    headers, _ = new_user(client)
    m = create_meeting(client, headers)
    priya = next(t for t in m["tasks"] if t["assignee"] == "Priya")

    r = client.patch(f"/tasks/{priya['id']}", headers=headers, json={"status": "done"})
    assert r.status_code == 200
    assert r.json()["status"] == "done" and r.json()["reminder_status"] == "cancelled"

    open_tasks = client.get("/tasks", headers=headers, params={"status": "open"}).json()
    assert {t["assignee"] for t in open_tasks} == {"Rahul", "Ananya"}

    created = client.post("/tasks", headers=headers, json={"title": "Order snacks", "priority": "low"})
    assert created.status_code == 201 and created.json()["meeting_id"] is None
    assert client.delete(f"/tasks/{created.json()['id']}", headers=headers).status_code == 204


def test_search_past_meetings_and_stats(client):
    headers, _ = new_user(client)
    create_meeting(client, headers)
    hits = client.get("/search", headers=headers, params={"q": "payment bug"}).json()
    assert hits and hits[0]["title"] == "Q1 launch sync"

    s = client.get("/stats", headers=headers).json()
    assert s["meetings"]["meetings"] == 1 and s["meetings"]["awaiting_approval"] == 1
    assert s["tasks"]["open"] == 3
    assert s["reminders"]["scheduled"] == 2


def test_users_cannot_see_each_others_meetings(client):
    alice, _ = new_user(client)
    bob, _ = new_user(client)
    m = create_meeting(client, alice)
    task_id = m["tasks"][0]["id"]

    assert client.get(f"/meetings/{m['id']}", headers=bob).status_code == 404
    assert client.post(f"/meetings/{m['id']}/approval", headers=bob, json={"action": "approve"}).status_code == 404
    assert client.patch(f"/tasks/{task_id}", headers=bob, json={"status": "done"}).status_code == 404
    assert client.get("/meetings", headers=bob).json() == []
    assert client.get("/search", headers=bob, params={"q": "payment bug"}).json() == []
    assert client.delete(f"/meetings/{m['id']}", headers=bob).status_code == 404


def test_delete_meeting(client):
    headers, _ = new_user(client)
    m = create_meeting(client, headers)
    assert client.delete(f"/meetings/{m['id']}", headers=headers).status_code == 204
    assert client.get(f"/meetings/{m['id']}", headers=headers).status_code == 404
    assert client.get("/tasks", headers=headers).json() == []

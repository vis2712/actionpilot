"""ActionPilot as an MCP server: lets Claude Desktop, Claude Code, Cursor or any MCP client use your meetings
and tasks as tools ("what did we decide about the beta?", "mark the venue task done").

It's a thin client of the REST API, so the same auth and tenant isolation apply. Run it over stdio:

    ACTIONPILOT_API_URL=http://localhost:8001 ACTIONPILOT_EMAIL=you@example.com ACTIONPILOT_PASSWORD=... \
        uv run python -m mcp_server.server
"""

import os
from typing import Any, Literal

import httpx
from mcp.server.mcpserver import MCPServer

mcp = MCPServer(
    "ActionPilot",
    instructions="Tools for the user's meetings, decisions and action items stored in ActionPilot. "
    "Search past meetings before answering questions about what was decided.",
)


class Api:
    """Logs in with email + password on first use, and again if the token expires."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = os.environ.get("ACTIONPILOT_API_URL", "http://localhost:8001").rstrip("/")
        self.transport = transport
        self.token: str | None = None

    async def _login(self, http: httpx.AsyncClient) -> None:
        email, password = os.environ.get("ACTIONPILOT_EMAIL"), os.environ.get("ACTIONPILOT_PASSWORD")
        if not email or not password:
            raise RuntimeError("Set ACTIONPILOT_EMAIL and ACTIONPILOT_PASSWORD for the MCP server")
        r = await http.post("/auth/login", data={"username": email, "password": password})
        r.raise_for_status()
        self.token = r.json()["access_token"]

    async def request(self, method: str, path: str, **kwargs) -> Any:
        async with httpx.AsyncClient(base_url=self.base_url, transport=self.transport, timeout=30) as http:
            for _ in range(2):
                if self.token is None:
                    await self._login(http)
                r = await http.request(method, path, headers={"Authorization": f"Bearer {self.token}"}, **kwargs)
                if r.status_code != 401:
                    break
                self.token = None  # expired: log in again once
            if r.status_code >= 400:
                raise RuntimeError(f"ActionPilot API error {r.status_code}: {r.text[:300]}")
            return r.json() if r.content else None


api = Api()


@mcp.tool()
async def search_meetings(query: str, limit: int = 5) -> list[dict]:
    """Search summaries and transcripts of past meetings. Returns matching excerpts with meeting title and date."""
    hits = await api.request("GET", "/search", params={"q": query, "k": limit})
    return [{k: h[k] for k in ("meeting_id", "title", "meeting_date", "kind", "content")} for h in hits]


@mcp.tool()
async def list_meetings(limit: int = 10) -> list[dict]:
    """List recent meetings with their processing status and number of tasks."""
    rows = await api.request("GET", "/meetings", params={"limit": limit})
    return [{k: m[k] for k in ("id", "title", "meeting_date", "status", "task_count", "email_status")} for m in rows]


@mcp.tool()
async def get_meeting(meeting_id: str) -> dict:
    """Get one meeting's summary, decisions, action items and follow-up email status."""
    m = await api.request("GET", f"/meetings/{meeting_id}")
    return {
        "title": m["title"],
        "date": m["meeting_date"],
        "status": m["status"],
        "summary": m["summary"],
        "decisions": m["decisions"],
        "tasks": [{k: t[k] for k in ("id", "title", "assignee", "due_date", "status")} for t in m["tasks"]],
        "follow_up_email": (m["email"] or {}).get("status"),
    }


@mcp.tool()
async def list_tasks(status: Literal["open", "done", "all"] = "open") -> list[dict]:
    """List action items. Overdue open tasks are flagged."""
    rows = await api.request("GET", "/tasks", params={"status": status})
    keys = ("id", "title", "assignee", "due_date", "priority", "status", "overdue", "meeting_title")
    return [{k: t[k] for k in keys} for t in rows]


@mcp.tool()
async def create_task(
    title: str, assignee: str | None = None, due_date: str | None = None, priority: str = "medium"
) -> dict:
    """Create a task. due_date is YYYY-MM-DD; priority is low, medium or high."""
    body = {"title": title, "assignee": assignee, "due_date": due_date or None, "priority": priority}
    return await api.request("POST", "/tasks", json=body)


@mcp.tool()
async def complete_task(task_id: int) -> dict:
    """Mark a task as done (this also cancels its reminder)."""
    t = await api.request("PATCH", f"/tasks/{task_id}", json={"status": "done"})
    return {"id": t["id"], "title": t["title"], "status": t["status"]}


def main() -> None:
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()

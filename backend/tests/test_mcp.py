"""The MCP server, tested in-process: an MCP client calls its tools, which call the real API."""

import asyncio
import json

import httpx
from mcp import Client

from mcp_server import server
from tests.helpers import PASSWORD, create_meeting, new_user


def _bridge(client) -> httpx.MockTransport:
    """Routes the MCP server's HTTP calls into the FastAPI TestClient (no network, same test database)."""

    def handler(request: httpx.Request) -> httpx.Response:
        r = client.request(
            request.method,
            request.url.raw_path.decode(),
            headers={k: v for k, v in request.headers.items() if k.lower() in ("authorization", "content-type")},
            content=request.content,
        )
        return httpx.Response(
            r.status_code, headers={"content-type": r.headers.get("content-type", "")}, content=r.content
        )

    return httpx.MockTransport(handler)


def _data(result):
    assert not result.is_error, result.content
    if result.structured_content is not None:
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    return json.loads(result.content[0].text)


def test_mcp_tools(client, monkeypatch):
    headers, email = new_user(client)
    meeting = create_meeting(client, headers)
    monkeypatch.setenv("ACTIONPILOT_EMAIL", email)
    monkeypatch.setenv("ACTIONPILOT_PASSWORD", PASSWORD)
    monkeypatch.setattr(server, "api", server.Api(transport=_bridge(client)))

    async def scenario():
        async with Client(server.mcp) as mcp:
            names = {t.name for t in (await mcp.list_tools()).tools}
            assert names == {
                "search_meetings",
                "list_meetings",
                "get_meeting",
                "list_tasks",
                "create_task",
                "complete_task",
            }

            hits = _data(await mcp.call_tool("search_meetings", {"query": "payment bug"}))
            assert hits[0]["title"] == "Q1 launch sync"

            got = _data(await mcp.call_tool("get_meeting", {"meeting_id": meeting["id"]}))
            assert got["follow_up_email"] == "draft" and len(got["tasks"]) == 3

            created = _data(
                await mcp.call_tool("create_task", {"title": "Prepare demo script", "due_date": "2030-02-01"})
            )
            done = _data(await mcp.call_tool("complete_task", {"task_id": created["id"]}))
            assert done["status"] == "done"

            open_tasks = _data(await mcp.call_tool("list_tasks", {"status": "open"}))
            assert len(open_tasks) == 3

    asyncio.run(scenario())

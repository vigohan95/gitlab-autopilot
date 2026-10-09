import asyncio
import json

import httpx
import pytest

from mcp_server import pulse_mcp


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("PULSE_ENV_PRODUCTION_URL", "https://prod.test/")
    monkeypatch.setenv("PULSE_ENV_STAGING_URL", "https://staging.test")
    monkeypatch.delenv("PULSE_GITLAB_PROJECT", raising=False)

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/healthz":
            return httpx.Response(
                503 if req.url.host == "staging.test" else 200,
                json={"status": "ok" if req.url.host == "prod.test" else "degraded"},
            )
        if p == "/version":
            return httpx.Response(200, json={"name": "pulse", "version": "0.1.0"})
        if p == "/api/summary":
            return httpx.Response(200, json=[{"name": "gitlab", "uptime_pct": 100.0}])
        if p == "/api/checks":
            return httpx.Response(200, json=[{"id": 1, "ok": True, "site_id": int(req.url.params.get("site_id", 0))}])
        if p == "/metrics":
            return httpx.Response(200, text="pulse_info 1\n")
        return httpx.Response(404)

    monkeypatch.setattr(
        pulse_mcp,
        "_get",
        lambda url, params=None: httpx.Client(transport=httpx.MockTransport(handler)).get(url, params=params),
    )


def test_environments_discovery(env):
    assert pulse_mcp.environments() == {"production": "https://prod.test", "staging": "https://staging.test"}
    assert pulse_mcp.list_environments() == pulse_mcp.environments()


def test_tools_are_read_only_views(env):
    h = pulse_mcp.pulse_health("production")
    assert h["healthy"] is True and h["/version"]["body"]["version"] == "0.1.0"
    assert pulse_mcp.pulse_health("staging")["healthy"] is False
    assert pulse_mcp.pulse_summary("production")[0]["name"] == "gitlab"
    assert pulse_mcp.pulse_recent_checks("production", site_id=4, limit=5)[0]["site_id"] == 4
    assert "pulse_info 1" in pulse_mcp.pulse_metrics("production")
    assert pulse_mcp.gitlab_releases() == []
    with pytest.raises(ValueError):
        pulse_mcp.pulse_health("nope")


def test_mcp_protocol_lists_tools_and_calls(env):
    """Drive the server through a real in-memory MCP client session."""
    from mcp.shared.memory import create_connected_server_and_client_session

    async def run():
        async with create_connected_server_and_client_session(pulse_mcp.mcp._mcp_server) as session:
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            result = await session.call_tool("pulse_health", {"env": "production"})
            return names, result

    names, result = asyncio.run(run())
    assert names == [
        "gitlab_releases",
        "list_environments",
        "pulse_health",
        "pulse_metrics",
        "pulse_recent_checks",
        "pulse_summary",
    ]
    assert not result.isError
    payload = json.loads(result.content[0].text)
    assert payload["healthy"] is True

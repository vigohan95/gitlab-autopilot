"""pulse-readonly — a Model Context Protocol server for the Incident Responder agent.

Every tool is a GET against a deployed pulse environment or the public GitLab
releases API. Nothing here can change state, which is why the agent is allowed
to call it without approval (AUTOPILOT §6, §9).

Run locally (stdio, for `.gitlab/duo/mcp.json` / IDE / Duo CLI):

    python -m mcp_server.pulse_mcp --transport stdio

Run as a remote server (streamable HTTP, for the AI Catalog MCP registration):

    python -m mcp_server.pulse_mcp --transport streamable-http --port 8765

Environments are discovered from ``PULSE_ENV_<NAME>_URL`` variables, e.g.
``PULSE_ENV_PRODUCTION_URL=https://user-pulse-prod.hf.space``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("pulse-readonly", instructions="Read-only observability for pulse deployments and releases.")

_TIMEOUT = 15.0


def environments() -> dict[str, str]:
    envs = {}
    for key, value in os.environ.items():
        if key.startswith("PULSE_ENV_") and key.endswith("_URL") and value:
            envs[key[len("PULSE_ENV_") : -len("_URL")].lower()] = value.rstrip("/")
    return envs


def _base(env: str) -> str:
    envs = environments()
    if env not in envs:
        raise ValueError(f"unknown environment {env!r}; known: {sorted(envs) or 'none configured'}")
    return envs[env]


def _get(url: str, params: dict | None = None) -> httpx.Response:
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as c:
        return c.get(url, params=params)


@mcp.tool()
def list_environments() -> dict[str, str]:
    """List the pulse environments this server can read (name -> base URL)."""
    return environments()


@mcp.tool()
def pulse_health(env: str) -> dict:
    """Probe /healthz and /version of an environment. Returns status codes, bodies and latency in ms."""
    base = _base(env)
    out: dict = {"env": env, "url": base}
    for path in ("/healthz", "/version"):
        try:
            r = _get(base + path)
            out[path] = {
                "status": r.status_code,
                "body": _json_or_text(r),
                "latency_ms": round(r.elapsed.total_seconds() * 1000, 1),
            }
        except httpx.HTTPError as exc:
            out[path] = {"status": None, "error": type(exc).__name__}
    out["healthy"] = out["/healthz"].get("status") == 200 and (out["/healthz"].get("body") or {}).get("status") == "ok"
    return out


@mcp.tool()
def pulse_summary(env: str) -> list[dict]:
    """Per-site uptime and latency summary from /api/summary of an environment."""
    r = _get(_base(env) + "/api/summary")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def pulse_recent_checks(env: str, site_id: int | None = None, limit: int = 20) -> list[dict]:
    """Most recent probe results from /api/checks (newest first)."""
    params: dict = {"limit": max(1, min(limit, 200))}
    if site_id is not None:
        params["site_id"] = site_id
    r = _get(_base(env) + "/api/checks", params)
    r.raise_for_status()
    return r.json()


@mcp.tool()
def pulse_metrics(env: str) -> str:
    """Raw Prometheus text from /metrics of an environment."""
    r = _get(_base(env) + "/metrics")
    r.raise_for_status()
    return r.text


@mcp.tool()
def gitlab_releases(limit: int = 10) -> list[dict]:
    """Recent releases of the pulse project (public GitLab API), newest first: tag, name, released_at."""
    project = os.environ.get("PULSE_GITLAB_PROJECT", "")
    if not project:
        return []
    api = os.environ.get("CI_API_V4_URL", "https://gitlab.com/api/v4")
    r = _get(f"{api}/projects/{project.replace('/', '%2F')}/releases", {"per_page": max(1, min(limit, 50))})
    r.raise_for_status()
    return [{"tag": x["tag_name"], "name": x["name"], "released_at": x["released_at"]} for x in r.json()]


def _json_or_text(r: httpx.Response):
    try:
        return r.json()
    except json.JSONDecodeError:
        return r.text[:500]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http", "sse"])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    if args.transport != "stdio":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
    mcp.run(transport=args.transport)
    return 0


if __name__ == "__main__":
    sys.exit(main())

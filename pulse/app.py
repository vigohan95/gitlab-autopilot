"""FastAPI application for pulse.

Environment variables
---------------------
PULSE_DB        SQLite path (default ``pulse.db``; ``:memory:`` for tests)
PULSE_SITES     comma-separated ``name=url`` pairs seeded at start-up
PULSE_INTERVAL  seconds between background probe rounds (0 disables; default 300)
PULSE_VERSION   version string shown on /version (defaults to the package version)
PULSE_CHAOS     demo-only fault injection: ``healthz`` makes /healthz return 503
                so that the monitor stage can be shown opening an incident and
                rolling back. Governed by AUTOPILOT.md rule 7.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import os
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, HttpUrl, field_validator

from . import __version__
from .probe import probe_many
from .store import Store

log = logging.getLogger("pulse")


class Settings:
    def __init__(self) -> None:
        self.db = os.environ.get("PULSE_DB", "pulse.db")
        self.sites = self._parse_sites(os.environ.get("PULSE_SITES", ""))
        self.interval = float(os.environ.get("PULSE_INTERVAL", "300"))
        self.version = os.environ.get("PULSE_VERSION", __version__)
        self.chaos = os.environ.get("PULSE_CHAOS", "")

    @staticmethod
    def _parse_sites(raw: str) -> list[tuple[str, str]]:
        out = []
        for item in filter(None, (s.strip() for s in raw.split(","))):
            name, _, url = item.partition("=")
            if name and url:
                out.append((name.strip(), url.strip()))
        return out


class SiteIn(BaseModel):
    name: str
    url: HttpUrl

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 64:
            raise ValueError("name must be 1-64 characters")
        return v


async def run_probe_round(store: Store, client: httpx.AsyncClient | None = None) -> list[dict]:
    sites = store.list_sites()
    results = await probe_many([s.url for s in sites], client=client)
    out = []
    for site, res in zip(sites, results):
        chk = store.record_check(site.id, res.ok, res.status_code, res.latency_ms, res.error)
        out.append({"site": site.name, **chk.__dict__})
    return out


async def _scheduler(app: FastAPI) -> None:
    interval = app.state.settings.interval
    while True:
        try:
            await run_probe_round(app.state.store)
        except Exception:  # pragma: no cover - defensive
            log.exception("probe round failed")
        await asyncio.sleep(interval)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    store = Store(settings.db)
    store.seed(settings.sites)
    app.state.store = store
    task = None
    if settings.interval > 0:
        task = asyncio.create_task(_scheduler(app))
    try:
        yield
    finally:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        store.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="pulse", version=__version__, lifespan=lifespan)
    app.state.settings = settings or Settings()

    @app.get("/healthz")
    async def healthz(request: Request) -> JSONResponse:
        if request.app.state.settings.chaos == "healthz":
            return JSONResponse({"status": "degraded", "reason": "PULSE_CHAOS=healthz"}, status_code=503)
        return JSONResponse({"status": "ok"})

    @app.get("/version")
    async def version(request: Request) -> dict:
        return {"name": "pulse", "version": request.app.state.settings.version}

    @app.get("/api/sites")
    async def list_sites(request: Request) -> list[dict]:
        return [s.__dict__ for s in request.app.state.store.list_sites()]

    @app.post("/api/sites", status_code=201)
    async def add_site(body: SiteIn, request: Request) -> dict:
        store: Store = request.app.state.store
        if store.get_site_by_name(body.name):
            raise HTTPException(409, "site name already exists")
        return store.add_site(body.name, str(body.url)).__dict__

    @app.delete("/api/sites/{site_id}", status_code=204)
    async def delete_site(site_id: int, request: Request) -> None:
        if not request.app.state.store.delete_site(site_id):
            raise HTTPException(404, "no such site")

    @app.post("/api/check")
    async def check_now(request: Request) -> list[dict]:
        return await run_probe_round(request.app.state.store)

    @app.get("/api/checks")
    async def checks(request: Request, site_id: int | None = None, limit: int = 50) -> list[dict]:
        limit = max(1, min(limit, 1000))
        return [c.__dict__ for c in request.app.state.store.recent_checks(site_id, limit)]

    @app.get("/api/summary")
    async def summary(request: Request) -> list[dict]:
        return request.app.state.store.summary()

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics(request: Request) -> str:
        lines = [
            "# HELP pulse_site_up 1 if the last probe of the site succeeded.",
            "# TYPE pulse_site_up gauge",
            "# HELP pulse_site_latency_ms Mean latency of recent successful probes.",
            "# TYPE pulse_site_latency_ms gauge",
            "# HELP pulse_info Build information.",
            "# TYPE pulse_info gauge",
            f'pulse_info{{version="{request.app.state.settings.version}"}} 1',
        ]
        for row in request.app.state.store.summary():
            label = f'site="{row["name"]}"'
            if row["last_ok"] is not None:
                lines.append(f"pulse_site_up{{{label}}} {int(bool(row['last_ok']))}")
            if row["mean_latency_ms"] is not None:
                lines.append(f"pulse_site_latency_ms{{{label}}} {row['mean_latency_ms']}")
        return "\n".join(lines) + "\n"

    @app.get("/", response_class=HTMLResponse)
    async def status_page(request: Request) -> str:
        rows = request.app.state.store.summary()
        body = "".join(
            "<tr><td>{name}</td><td><a href='{url}'>{url}</a></td><td class='{cls}'>{state}</td>"
            "<td>{uptime}</td><td>{lat}</td><td>{ts}</td></tr>".format(
                name=html.escape(r["name"]),
                url=html.escape(r["url"]),
                cls="up" if r["last_ok"] else "down",
                state="UP" if r["last_ok"] else ("DOWN" if r["last_ok"] is not None else "n/a"),
                uptime=f"{r['uptime_pct']}%" if r["uptime_pct"] is not None else "n/a",
                lat=f"{r['mean_latency_ms']} ms" if r["mean_latency_ms"] is not None else "n/a",
                ts=html.escape(r["last_ts"] or "n/a"),
            )
            for r in rows
        )
        return (
            "<!doctype html><html><head><meta charset='utf-8'><title>pulse</title>"
            "<style>body{font-family:system-ui;margin:2rem;background:#0b0f19;color:#e6e6e6}"
            "table{border-collapse:collapse}td,th{padding:.4rem .8rem;border-bottom:1px solid #333}"
            ".up{color:#4ade80}.down{color:#f87171}</style></head><body>"
            f"<h1>pulse <small>v{html.escape(request.app.state.settings.version)}</small></h1>"
            "<table><tr><th>site</th><th>url</th><th>state</th><th>uptime</th><th>latency</th><th>last check</th></tr>"
            f"{body}</table></body></html>"
        )

    return app


app = create_app()

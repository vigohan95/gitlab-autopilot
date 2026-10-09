"""Outbound HTTP probes."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

DEFAULT_TIMEOUT = 10.0
USER_AGENT = "pulse-probe/1.0 (+https://gitlab.com)"


@dataclass(frozen=True)
class ProbeResult:
    url: str
    ok: bool
    status_code: int | None
    latency_ms: float | None
    error: str | None = None


async def probe(client: httpx.AsyncClient, url: str, timeout: float = DEFAULT_TIMEOUT) -> ProbeResult:
    """GET ``url`` once. 2xx/3xx count as up; anything else or an exception is down."""
    start = time.perf_counter()
    try:
        resp = await client.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    except httpx.HTTPError as exc:  # timeouts, DNS, connection refused, ...
        latency = (time.perf_counter() - start) * 1000
        return ProbeResult(url=url, ok=False, status_code=None, latency_ms=round(latency, 1), error=type(exc).__name__)
    latency = (time.perf_counter() - start) * 1000
    ok = 200 <= resp.status_code < 400
    return ProbeResult(
        url=url,
        ok=ok,
        status_code=resp.status_code,
        latency_ms=round(latency, 1),
        error=None if ok else f"HTTP {resp.status_code}",
    )


async def probe_many(
    urls: list[str], timeout: float = DEFAULT_TIMEOUT, client: httpx.AsyncClient | None = None
) -> list[ProbeResult]:
    owned = client is None
    client = client or httpx.AsyncClient()
    try:
        return list(await asyncio.gather(*(probe(client, u, timeout) for u in urls)))
    finally:
        if owned:
            await client.aclose()

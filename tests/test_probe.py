import asyncio

import httpx

from pulse.probe import probe, probe_many


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_probe_ok_and_redirect():
    def handler(req):
        return httpx.Response(302 if req.url.host == "r.test" else 200)

    async def run():
        async with _client(handler) as c:
            return await probe(c, "https://ok.test/"), await probe(c, "https://r.test/")

    ok, redirect = asyncio.run(run())
    assert ok.ok and ok.status_code == 200 and ok.error is None
    assert redirect.ok and redirect.status_code == 302
    assert ok.latency_ms is not None and ok.latency_ms >= 0


def test_probe_http_error():
    async def run():
        async with _client(lambda r: httpx.Response(503)) as c:
            return await probe(c, "https://x.test/")

    res = asyncio.run(run())
    assert res.ok is False and res.status_code == 503 and res.error == "HTTP 503"


def test_probe_exception_is_down_not_raised():
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    async def run():
        async with _client(handler) as c:
            return await probe(c, "https://slow.test/")

    res = asyncio.run(run())
    assert res.ok is False and res.status_code is None and res.error == "ReadTimeout"


def test_probe_many_preserves_order():
    def handler(req):
        return httpx.Response(200 if req.url.host == "a.test" else 500)

    async def run():
        async with _client(handler) as c:
            return await probe_many(["https://a.test/", "https://b.test/"], client=c)

    a, b = asyncio.run(run())
    assert a.url.endswith("a.test/") and a.ok
    assert b.url.endswith("b.test/") and not b.ok

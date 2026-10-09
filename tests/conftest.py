import httpx
import pytest
from fastapi.testclient import TestClient

from pulse.app import Settings, create_app


def _mock_transport(status_by_host: dict[str, int]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "boom.test":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(status_by_host.get(host, 200), text="ok")

    return httpx.MockTransport(handler)


@pytest.fixture
def transport() -> httpx.MockTransport:
    return _mock_transport({"ok.test": 200, "redirect.test": 302, "err.test": 500})


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("PULSE_DB", ":memory:")
    monkeypatch.setenv("PULSE_INTERVAL", "0")
    monkeypatch.setenv("PULSE_SITES", "ok=https://ok.test/,err=https://err.test/")
    monkeypatch.delenv("PULSE_CHAOS", raising=False)
    return Settings()


@pytest.fixture
def client(settings: Settings, transport: httpx.MockTransport, monkeypatch: pytest.MonkeyPatch):
    # Route all outbound probes through the mock transport.
    import pulse.app as app_mod

    real_probe_many = app_mod.probe_many

    async def patched(urls, timeout=10.0, client=None):
        async with httpx.AsyncClient(transport=transport) as c:
            return await real_probe_many(urls, timeout, client=c)

    monkeypatch.setattr(app_mod, "probe_many", patched)
    app = create_app(settings)
    with TestClient(app) as c:
        yield c

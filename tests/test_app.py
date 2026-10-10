import pytest

from pulse import __version__
from pulse.app import Settings, create_app


def test_healthz_and_version(client):
    assert client.get("/livez").json() == {"status": "alive"}
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/version").json() == {"name": "pulse", "version": __version__}


def test_seeded_sites(client):
    names = [s["name"] for s in client.get("/api/sites").json()]
    assert names == ["ok", "err"]


def test_add_site_validation_and_conflict(client):
    r = client.post("/api/sites", json={"name": "new", "url": "https://ok.test/x"})
    assert r.status_code == 201 and r.json()["name"] == "new"
    assert client.post("/api/sites", json={"name": "new", "url": "https://ok.test/"}).status_code == 409
    assert client.post("/api/sites", json={"name": "bad", "url": "not a url"}).status_code == 422
    assert client.post("/api/sites", json={"name": "   ", "url": "https://ok.test/"}).status_code == 422


def test_delete_site(client):
    site_id = client.get("/api/sites").json()[0]["id"]
    assert client.delete(f"/api/sites/{site_id}").status_code == 204
    assert client.delete(f"/api/sites/{site_id}").status_code == 404


def test_check_round_records_results(client):
    rows = client.post("/api/check").json()
    by_name = {r["site"]: r for r in rows}
    assert by_name["ok"]["ok"] is True and by_name["ok"]["status_code"] == 200
    assert by_name["err"]["ok"] is False and by_name["err"]["error"] == "HTTP 500"

    checks = client.get("/api/checks", params={"limit": 10}).json()
    assert len(checks) == 2
    err_id = by_name["err"]["site_id"]
    only_err = client.get("/api/checks", params={"site_id": err_id}).json()
    assert len(only_err) == 1 and only_err[0]["ok"] is False


def test_summary_and_metrics(client):
    client.post("/api/check")
    summary = {r["name"]: r for r in client.get("/api/summary").json()}
    assert summary["ok"]["uptime_pct"] == 100.0
    assert summary["err"]["uptime_pct"] == 0.0

    text = client.get("/metrics").text
    assert 'pulse_site_up{site="ok"} 1' in text
    assert 'pulse_site_up{site="err"} 0' in text
    assert f'pulse_info{{version="{__version__}"}} 1' in text
    assert 'pulse_site_latency_ms{site="ok"}' in text


def test_status_page_html(client):
    client.post("/api/check")
    page = client.get("/")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "<td class='up'>UP</td>" in page.text
    assert "<td class='down'>DOWN</td>" in page.text


def test_chaos_switch_degrades_healthz(monkeypatch):
    monkeypatch.setenv("PULSE_DB", ":memory:")
    monkeypatch.setenv("PULSE_INTERVAL", "0")
    monkeypatch.setenv("PULSE_CHAOS", "healthz")
    from fastapi.testclient import TestClient

    with TestClient(create_app(Settings())) as c:
        r = c.get("/healthz")
        assert r.status_code == 503 and r.json()["status"] == "degraded"
        assert c.get("/livez").status_code == 200  # liveness unaffected


def test_settings_parse_sites():
    s = Settings._parse_sites(" a=https://a.test , bad , b=https://b.test/x ")
    assert s == [("a", "https://a.test"), ("b", "https://b.test/x")]


@pytest.mark.parametrize("path", ["/livez", "/healthz", "/version", "/metrics", "/", "/api/summary"])
def test_read_endpoints_are_get_only(client, path):
    assert client.post(path).status_code == 405

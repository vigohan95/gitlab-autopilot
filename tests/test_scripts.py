import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import codequality  # noqa: E402
import incident  # noqa: E402
import journey_card  # noqa: E402
import monitor  # noqa: E402
from deploy_hf import space_url  # noqa: E402


def test_pick_rollback_target_never_rolls_forward():
    releases = [{"tag_name": "v0.3.0"}, {"tag_name": "v0.2.0"}, {"tag_name": "v0.1.0"}]
    assert incident.pick_rollback_target(releases, "0.3.0") == "v0.2.0"
    assert incident.pick_rollback_target(releases, "v0.2.0") == "v0.1.0"
    assert incident.pick_rollback_target(releases, "0.1.0") is None
    assert incident.pick_rollback_target(releases, "abc1234") == "v0.3.0"
    assert incident.pick_rollback_target([], "0.1.0") is None


def test_incident_description_mentions_rollback_and_rules():
    report = {"ts": "2026-10-20T10:00:00+00:00", "targets": []}
    failed = [
        {"name": "production", "url": "https://p", "version": "0.3.0", "checks": [{"path": "/healthz", "status": 503}]}
    ]
    text = incident.build_description(report, failed, "v0.2.0", "https://gitlab.com/p/-/pipelines/1")
    assert "`v0.2.0`" in text and "autopilot: rules=[§6.1, §6.2, §6.3]" in text and "/healthz→503" in text


def test_journey_card_render_counts_stages():
    jobs = [
        {"name": "test", "stage": "test", "status": "success", "duration": 30, "retried": False},
        {"name": "sast", "stage": "secure", "status": "success", "duration": 60, "retried": True},
        {"name": "build-image", "stage": "build", "status": "success", "duration": 90, "retried": False},
        {"name": "release", "stage": "release", "status": "success", "duration": 10, "retried": False},
        {
            "name": "deploy-production",
            "stage": "deploy-production",
            "status": "success",
            "duration": 120,
            "retried": False,
        },
        {"name": "monitor", "stage": "monitor", "status": "success", "duration": 5, "retried": False},
        {"name": "audit-trail", "stage": "govern", "status": "success", "duration": 5, "retried": False},
    ]
    mr = {"iid": 7, "title": "Add latency percentiles"}
    issue = {"iid": 3}
    card = journey_card.render(jobs, mr, issue, "https://x/-/pipelines/9")
    assert "**9/9 stages touched**" in card
    assert "1 retried jobs" in card
    assert "| ✅ plan |" in card and "issue #3" in card
    assert "| ✅ create |" in card and "!7" in card


def test_journey_card_render_partial():
    card = journey_card.render([], None, None, "")
    assert "**0/9 stages touched**" in card


def test_monitor_probe_target(monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"name": "pulse", "version": "0.1.0"})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    res = monitor.probe_target("staging", "https://s.test/", retries=1)
    assert res["ok"] and res["version"] == "0.1.0" and res["attempts"] == 1


def test_monitor_probe_target_down(monkeypatch):
    monkeypatch.setattr(monitor.time, "sleep", lambda s: None)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/healthz":
            return httpx.Response(503, json={"status": "degraded"})
        return httpx.Response(200, json={"version": "0.1.0"})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    res = monitor.probe_target("production", "https://p.test", retries=2)
    assert not res["ok"] and res["attempts"] == 2
    assert res["checks"][0]["status"] == 503


def test_monitor_main_writes_report_and_exit_code(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        monitor,
        "probe_target",
        lambda n, u, retries=3: {
            "name": n,
            "url": u,
            "ok": n == "staging",
            "version": "1",
            "checks": [],
            "attempts": 1,
        },
    )
    rc = monitor.main(["--target", "staging=https://s", "--target", "production=https://p", "--report", "r.json"])
    assert rc == 1
    report = json.loads((tmp_path / "r.json").read_text())
    assert report["ok"] is False and [t["ok"] for t in report["targets"]] == [True, False]
    assert monitor.main(["--report", "empty.json"]) == 0


def test_codequality_conversion(tmp_path):
    items = [
        {
            "code": "F401",
            "message": "unused import",
            "filename": str(tmp_path / "pulse" / "x.py"),
            "location": {"row": 3},
        },
        {"code": "UP006", "message": "use list", "filename": "pulse/y.py", "location": {"row": 10}},
    ]
    out = codequality.convert(items, tmp_path)
    assert out[0]["location"]["path"] == "pulse/x.py" and out[0]["severity"] == "critical"
    assert out[1]["severity"] == "minor" and len(out[1]["fingerprint"]) == 40


@pytest.mark.parametrize(
    "space,url",
    [
        ("han/pulse-staging", "https://han-pulse-staging.hf.space"),
        ("Han_V/pulse_prod", "https://han-v-pulse-prod.hf.space"),
    ],
)
def test_space_url(space, url):
    assert space_url(space) == url

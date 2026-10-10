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
    mr = {
        "iid": 7,
        "title": "Add latency percentiles",
        "author": {"username": "ai-f1-group"},
        "labels": ["autopilot", "autopilot::approved"],
    }
    issue = {"iid": 3, "author": {"username": "hanwg666"}}
    card = journey_card.render(jobs, mr, issue, "https://x/-/pipelines/9")
    assert "**9/9 stages touched**" in card
    assert "1 retried jobs" in card
    assert "| ✅ plan |" in card and "issue #3 opened by @hanwg666" in card
    assert "| ✅ create |" in card and "!7 opened by @ai-f1-group (GitLab Duo flow service account)" in card
    assert "policy-check (autopilot::approved)" in card


def test_journey_card_monitor_row_and_human_actor():
    jobs = [{"name": "test", "stage": "test", "status": "success", "duration": 1, "retried": False}]
    mr = {"iid": 2, "title": "t", "author": {"username": "project_87419642_bot"}, "labels": ["autopilot"]}
    card = journey_card.render(
        jobs, mr, None, "", monitor={"id": 55, "status": "success", "web_url": "https://x/-/pipelines/55"}
    )
    assert "| ✅ monitor | scheduled monitor pipeline 55 (success)" in card
    assert "!2 opened by @project_87419642_bot, reviewed by no approval label" in card


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
        lambda n, u, retries=3, backoff=5.0: {
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


def test_deploy_hf_402_is_dry_run(monkeypatch, tmp_path, capsys):
    import types

    import deploy_hf

    class Resp:
        status_code = 402

    class FakeErr(Exception):
        def __init__(self):
            super().__init__("402")
            self.response = Resp()

    class FakeApi:
        def __init__(self, token=None):
            pass

        def create_repo(self, *a, **kw):
            raise FakeErr()

    fake_hub = types.SimpleNamespace(HfApi=FakeApi)
    fake_errors = types.SimpleNamespace(HfHubHTTPError=FakeErr)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
    monkeypatch.setitem(sys.modules, "huggingface_hub.errors", fake_errors)
    monkeypatch.setenv("HF_TOKEN", "hf_x")
    monkeypatch.chdir(tmp_path)
    assert deploy_hf.main(["--env", "staging", "--space", "u/pulse-staging", "--version", "0.1.1", "--wait", "0"]) == 0
    assert "HTTP 402" in capsys.readouterr().out
    assert "DEPLOY_URL=https://u-pulse-staging.hf.space" in (tmp_path / "deploy.env").read_text()


def test_clear_chaos_switch_uses_render_and_legacy_variable(monkeypatch):
    import deploy_render

    calls = []
    monkeypatch.setattr(deploy_render, "set_chaos", lambda env, on: calls.append((env, on)) or "https://p")
    monkeypatch.setattr(deploy_render, "wait_status", lambda url, want, timeout=180: True)

    class FakeGL:
        can_write = True
        project = "1"

        def get_all(self, path, **kw):
            return [{"key": "PULSE_CHAOS", "environment_scope": "production"}]

        class _C:
            def delete(self, url, params=None):
                calls.append(("delete", url))
                return type("R", (), {"status_code": 204})()

        _client = _C()

    assert incident.clear_chaos_switch(FakeGL()) is True
    assert ("production", False) in calls and any(c[0] == "delete" for c in calls)

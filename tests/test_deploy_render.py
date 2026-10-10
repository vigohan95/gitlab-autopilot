import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import deploy  # noqa: E402
import deploy_render  # noqa: E402


class FakeRender:
    """Records calls and answers like api.render.com."""

    def __init__(self):
        self.calls = []
        self.polls = 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path, req.url.params.get("name")))
        if req.method == "GET" and req.url.path == "/v1/services":
            return httpx.Response(
                200,
                json=[
                    {"service": {"id": "srv-other", "name": "other"}},
                    {
                        "service": {
                            "id": "srv-1",
                            "name": req.url.params["name"],
                            "serviceDetails": {"url": "https://pulse-staging-abcd.onrender.com/"},
                        }
                    },
                ],
            )
        if req.method == "PUT" and "/env-vars/" in req.url.path:
            return httpx.Response(200, json={"key": req.url.path.rsplit("/", 1)[-1]})
        if req.method == "DELETE":
            return httpx.Response(404)
        if req.method == "POST" and req.url.path.endswith("/deploys"):
            return httpx.Response(201, json={"id": "dep-9", "status": "created"})
        if req.method == "GET" and "/deploys/" in req.url.path:
            self.polls += 1
            return httpx.Response(200, json={"status": "build_in_progress" if self.polls < 2 else "live"})
        return httpx.Response(404)


def test_render_client_flow(monkeypatch):
    fake = FakeRender()
    r = deploy_render.Render("key", transport=httpx.MockTransport(fake.handler))
    s = r.find_service("pulse-staging")
    assert s["id"] == "srv-1" and deploy_render.service_url(s) == "https://pulse-staging-abcd.onrender.com"
    r.upsert_env("srv-1", "PULSE_VERSION", "0.1.1")
    r.delete_env("srv-1", "PULSE_CHAOS")
    dep = r.deploy("srv-1", "abc123")
    assert dep["id"] == "dep-9"
    monkeypatch.setattr(deploy_render.time, "sleep", lambda s: None)
    assert deploy_render.wait_live(r, "srv-1", "dep-9", timeout=60, poll=0) == "live"
    assert ("POST", "/v1/services/srv-1/deploys", None) in fake.calls
    assert any(m == "PUT" and p.endswith("/env-vars/PULSE_PORT") for m, p, _ in fake.calls) is False  # only in main()


def test_render_main_end_to_end(monkeypatch, tmp_path):
    fake = FakeRender()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RENDER_API_KEY", "key")
    monkeypatch.setenv("PULSE_CHAOS", "")
    monkeypatch.setattr(
        deploy_render,
        "Render",
        lambda key, _real=deploy_render.Render: _real(key, transport=httpx.MockTransport(fake.handler)),
    )
    monkeypatch.setattr(deploy_render.time, "sleep", lambda s: None)
    import deploy_hf

    monkeypatch.setattr(deploy_hf, "wait_healthy", lambda url, version, timeout: True)
    rc = deploy_render.main(["--env", "staging", "--version", "0.1.1", "--commit", "abc123", "--wait", "30"])
    assert rc == 0
    env = (tmp_path / "deploy.env").read_text()
    assert "DEPLOY_URL=https://pulse-staging-abcd.onrender.com" in env and "DEPLOY_MODE=live" in env
    puts = [p.rsplit("/", 1)[-1] for m, p, _ in fake.calls if m == "PUT"]
    assert {"PULSE_VERSION", "PULSE_SITES", "PULSE_INTERVAL", "PULSE_PORT"} <= set(puts)
    assert ("DELETE", "/v1/services/srv-1/env-vars/PULSE_CHAOS", None) in fake.calls


def test_render_main_skips_without_key(monkeypatch, capsys):
    monkeypatch.delenv("RENDER_API_KEY", raising=False)
    assert deploy_render.main(["--env", "staging", "--version", "1"]) == 0
    assert "skipped: RENDER_API_KEY not set" in capsys.readouterr().out


def test_dispatcher_dry_run(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in ("RENDER_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PULSE_STAGING_URL", "https://s.example")
    assert deploy.main(["--env", "staging", "--version", "0.1.1"]) == 0
    env = (tmp_path / "deploy.env").read_text()
    assert "DEPLOY_MODE=dry-run" in env and "DEPLOY_URL=https://s.example" in env


def test_dispatcher_resolves_release_tag(monkeypatch):
    import gitlab_api

    class FakeGL:
        def get(self, path, **params):
            assert path == "releases/v0.1.0"
            return {"commit": {"id": "deadbeef"}}

    monkeypatch.setattr(gitlab_api, "GitLab", lambda: FakeGL())
    assert deploy.resolve_ref("v0.1.0") == "deadbeef"
    assert deploy.resolve_ref("0123456789abcdef") == "0123456789abcdef"


def test_set_chaos_toggles_env_and_restarts(monkeypatch):
    fake = FakeRender()

    def handler(req: httpx.Request) -> httpx.Response:
        fake.calls.append((req.method, req.url.path, None))
        if req.method == "POST" and req.url.path.endswith("/restart"):
            return httpx.Response(200, json={})
        return fake.handler(req)

    monkeypatch.setenv("RENDER_API_KEY", "key")
    monkeypatch.setattr(
        deploy_render,
        "Render",
        lambda key, _real=deploy_render.Render: _real(key, transport=httpx.MockTransport(handler)),
    )
    assert deploy_render.set_chaos("production", True) == "https://pulse-staging-abcd.onrender.com"
    assert ("PUT", "/v1/services/srv-1/env-vars/PULSE_CHAOS", None) in fake.calls
    assert ("POST", "/v1/services/srv-1/restart", None) in fake.calls
    deploy_render.set_chaos("production", False)
    assert ("DELETE", "/v1/services/srv-1/env-vars/PULSE_CHAOS", None) in fake.calls
    monkeypatch.delenv("RENDER_API_KEY")
    assert deploy_render.set_chaos("production", True) is None


def test_wait_status(monkeypatch):
    codes = iter([200, 200, 503])
    monkeypatch.setattr(deploy_render.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda _real=httpx.Client, **kw: _real(transport=httpx.MockTransport(lambda r: httpx.Response(next(codes)))),
    )
    assert deploy_render.wait_status("https://p.test", 503, timeout=30, poll=0) is True

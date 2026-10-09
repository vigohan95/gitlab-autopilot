import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import package_exists  # noqa: E402

INDEX = (
    "<html><body><h1>Links for pulse-monitor</h1>"
    '<a href="https://gitlab.com/api/v4/projects/1/packages/pypi/files/abc/pulse_monitor-0.1.0-py3-none-any.whl#sha256=x">'
    "pulse_monitor-0.1.0-py3-none-any.whl</a><br/>"
    '<a href="https://gitlab.com/api/v4/projects/1/packages/pypi/files/def/pulse_monitor-0.1.1.tar.gz#sha256=y">'
    "pulse_monitor-0.1.1.tar.gz</a><br/></body></html>"
)


def test_version_listed():
    assert package_exists.version_listed(INDEX, "0.1.0")
    assert package_exists.version_listed(INDEX, "0.1.1")
    assert not package_exists.version_listed(INDEX, "0.1.10")
    assert not package_exists.version_listed(INDEX, "0.2.0")


def _patch(monkeypatch, status, text=""):
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/packages/pypi/simple/pulse-monitor")
        assert req.headers.get("authorization", "").startswith("Basic ")
        return httpx.Response(status, text=text)

    real_get = httpx.get
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **kw: httpx.Client(transport=httpx.MockTransport(handler)).get(
            url, **{k: v for k, v in kw.items() if k != "timeout"}
        ),
    )
    monkeypatch.setenv("CI_API_V4_URL", "https://gitlab.com/api/v4")
    monkeypatch.setenv("CI_PROJECT_ID", "1")
    monkeypatch.setenv("CI_JOB_TOKEN", "job-token")
    return real_get


def test_exit_codes(monkeypatch):
    _patch(monkeypatch, 200, INDEX)
    assert package_exists.main(["x", "0.1.1"]) == 0
    assert package_exists.main(["x", "0.3.0"]) == 1
    _patch(monkeypatch, 403)
    assert package_exists.main(["x", "0.1.1"]) == 0  # cannot verify -> warn, do not block
    assert package_exists.main(["x"]) == 2

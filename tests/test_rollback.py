"""Rollback without pipeline variables (finding 3 of the 2026-10-14 drill, docs/DRILL-2026-10-14.md).

The judged project refused `ROLLBACK_TO` as a pipeline variable ("Insufficient permissions to set pipeline
variables": the minimum role is above Developer). The rollback now runs on `rollback/<tag>/incident-<iid>`.
"""

import json
import re
import shlex
import sys
from pathlib import Path

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import incident  # noqa: E402
import rollback_ref  # noqa: E402


def test_branch_name_round_trip():
    assert rollback_ref.branch_name("v0.1.2", 3) == "rollback/v0.1.2/incident-3"
    assert rollback_ref.parse("rollback/v0.1.2/incident-3") == ("v0.1.2", "3")
    assert rollback_ref.branch_name("v0.1.2", None, 2945603793) == "rollback/v0.1.2/pipeline-2945603793"
    assert rollback_ref.parse("rollback/v0.1.2/pipeline-2945603793") == ("v0.1.2", None)
    for other in ("main", "rollback/main", "rollback/v0.1.2", "autopilot/issue-2", "", None):
        assert rollback_ref.parse(other) is None
    with pytest.raises(ValueError):
        rollback_ref.branch_name("main", 3)


def test_current_prefers_explicit_variables_then_branch():
    branch = {"CI_COMMIT_BRANCH": "rollback/v0.1.1/incident-7", "ROLLBACK_TO": "", "INCIDENT_IID": ""}
    assert rollback_ref.current(branch) == ("v0.1.1", "7")
    assert rollback_ref.current({**branch, "ROLLBACK_TO": "v0.1.0", "INCIDENT_IID": "9"}) == ("v0.1.0", "9")
    assert rollback_ref.current({"CI_COMMIT_BRANCH": "main"}) == ("", "")
    assert rollback_ref.shell_exports({"CI_COMMIT_BRANCH": "main"}) == ""
    out = rollback_ref.shell_exports(branch)
    assert shlex.split(out) == ["export", "ROLLBACK_TO=v0.1.1", "INCIDENT_IID=7"]


class FakeGL:
    def __init__(self, can_write=True, branch_status=201):
        self.can_write = can_write
        self.branch_status = branch_status
        self.calls: list[tuple] = []
        self.notes: list[str] = []

    def post(self, path, json=None, data=None):
        self.calls.append(("post", path, json))
        if path == "repository/branches" and self.branch_status != 201:
            req = httpx.Request("POST", "https://gitlab.test/api/v4/projects/1/repository/branches")
            resp = httpx.Response(self.branch_status, request=req, text='{"message":"Branch already exists"}')
            raise httpx.HTTPStatusError("400 Bad Request", request=req, response=resp)
        if path == "issues":
            return {"iid": 3, "web_url": "https://gitlab.test/p/-/issues/3"}
        return {}

    def trigger_pipeline(self, ref, variables):
        self.calls.append(("trigger", ref, dict(variables)))
        return {"id": 99, "web_url": "https://gitlab.test/p/-/pipelines/99"}

    def get_all(self, path, **kw):
        return []

    def releases(self):
        return [{"tag_name": "v0.1.2"}, {"tag_name": "v0.1.1"}]

    def issue_note(self, iid, body):
        self.notes.append(body)


def test_start_rollback_uses_a_branch_and_no_variables():
    gl = FakeGL()
    pipe, ref = incident.start_rollback(gl, "v0.1.1", 3, "abc123", 42)
    assert ref == "rollback/v0.1.1/incident-3" and pipe["id"] == 99
    assert ("post", "repository/branches", {"branch": ref, "ref": "abc123"}) in gl.calls
    assert ("trigger", ref, {}) in gl.calls  # no pipeline variables at all


def test_start_rollback_reuses_an_existing_branch():
    gl = FakeGL(branch_status=400)
    _, ref = incident.start_rollback(gl, "v0.1.1", 3, "abc123", 42)
    assert ("trigger", ref, {}) in gl.calls


def test_start_rollback_raises_other_branch_errors():
    with pytest.raises(httpx.HTTPStatusError):
        incident.start_rollback(FakeGL(branch_status=403), "v0.1.1", 3, "abc123", 42)


def test_start_rollback_without_token_falls_back_to_variable_call():
    gl = FakeGL(can_write=False)
    _, ref = incident.start_rollback(gl, "v0.1.1", None, "main", 42)
    assert ref == "main" and gl.calls == [("trigger", "main", {"ROLLBACK_TO": "v0.1.1"})]


def test_incident_main_replays_the_drill_without_pipeline_variables(tmp_path, monkeypatch):
    """The 2026-10-14 drill: production 503 on both probes, /version unreadable -> newest release."""
    report = {
        "ts": "2026-10-14T07:02:30+00:00",
        "targets": [
            {
                "name": "production",
                "url": "https://httpbin.org/status/503?probe=",
                "ok": False,
                "version": None,
                "checks": [{"path": "/healthz", "status": 503}, {"path": "/version", "status": 503}],
            }
        ],
    }
    (tmp_path / "monitor-report.json").write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CI_COMMIT_SHA", "e501b1b")
    monkeypatch.setenv("CI_PIPELINE_ID", "2945603793")
    gl = FakeGL()
    monkeypatch.setattr(incident, "GitLab", lambda: gl)
    monkeypatch.setattr(incident, "clear_chaos_switch", lambda _gl: False)

    assert incident.main([]) == 0
    created = next(c for c in gl.calls if c[:2] == ("post", "issues"))[2]
    assert set(created["labels"].split(",")) == {"autopilot", "incident", "env::production"}  # F4 finds it by these
    triggers = [c for c in gl.calls if c[0] == "trigger"]
    assert triggers == [("trigger", "rollback/v0.1.2/incident-3", {})]
    assert any("started on `rollback/v0.1.2/incident-3`" in n for n in gl.notes)
    env = (tmp_path / "incident.env").read_text(encoding="utf-8")
    assert "ROLLBACK_REF=rollback/v0.1.2/incident-3" in env


# ------------------------------------------------------------------ .gitlab-ci.yml wiring
def _ci() -> dict:
    return yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8"))


def _branch_rules(rules: list[dict]) -> list[dict]:
    return [r for r in rules if "rollback\\/v[0-9]" in str(r.get("if", ""))]


def test_ci_regex_matches_the_branch_convention():
    ci = _ci()
    rule = _branch_rules(ci["workflow"]["rules"])[-1]["if"]
    pattern = re.search(r"=~ /(.+)/", rule).group(1).replace("\\/", "/")
    assert re.search(pattern, rollback_ref.branch_name("v0.1.2", 3))
    assert re.search(pattern, rollback_ref.branch_name("v1.0.0", None, 7))
    assert not re.search(pattern, "main") and not re.search(pattern, "autopilot/issue-2-x")


def test_ci_runs_rollback_branches_once_and_deploys_from_them():
    ci = _ci()
    wf = _branch_rules(ci["workflow"]["rules"])
    assert wf[0].get("when") == "never" and 'CI_PIPELINE_SOURCE == "push"' in wf[0]["if"]  # no duplicate pipeline
    assert "when" not in wf[1]
    for job in ("deploy-staging", "verify-staging", "deploy-production", "rollback-note", "audit-trail"):
        assert _branch_rules(ci[job]["rules"]), f"{job} does not run on rollback branches"
    # every job that runs on ROLLBACK_TO also runs on a rollback branch: no rule depends on a pipeline variable alone
    for name, job in ci.items():
        rules = job.get("rules") if isinstance(job, dict) else None
        if isinstance(rules, list) and any(r.get("if") == "$ROLLBACK_TO" and "when" not in r for r in rules):
            assert _branch_rules(rules), f"{name}: runs on $ROLLBACK_TO but not on rollback/<tag> branches"
    assert any("rollback_ref.py" in line for line in ci[".deploy"]["before_script"])
    assert "rollback_ref import current" in ci["rollback-note"]["script"][0]

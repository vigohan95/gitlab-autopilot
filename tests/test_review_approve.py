"""Verdict parsing for scripts/review_approve.py (M3: the agent's verdict becomes a GitLab approval) and the
request body scripts/start_flow.py sends for foundational vs AI Catalog flows."""

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import review_approve  # noqa: E402
import start_flow  # noqa: E402

SIG = "\n\nautopilot: rules=[section 2.1, section 3.4]"


def _note(body, created, system=False, nid=1):
    return {"id": nid, "body": body, "created_at": created, "system": system, "author": {"username": "ai-f2"}}


def test_parse_verdict_latest_signed_note_wins():
    notes = [
        _note("Autopilot review: APPROVED\n- 2.1 pass" + SIG, "2026-10-14T10:00:00Z", nid=1),
        _note("Autopilot review: HUMAN REQUIRED\n- 2.1 fail" + SIG, "2026-10-14T10:05:00Z", nid=2),
    ]
    assert review_approve.parse_verdict(notes) == ("HUMAN-REQUIRED", notes[1])
    assert review_approve.parse_verdict(list(reversed(notes)))[0] == "HUMAN-REQUIRED"


def test_parse_verdict_ignores_unsigned_system_and_quoted_notes():
    notes = [
        _note("added label autopilot::approved", "2026-10-14T10:00:00Z", system=True),
        _note("Autopilot review: APPROVED (no signature)", "2026-10-14T10:01:00Z", nid=2),
        _note("**Autopilot review: APPROVED**\n\n- section 2.1 pass" + SIG, "2026-10-14T10:02:00Z", nid=3),
        _note("Policy check: auto-merge allowed\n\nautopilot: rules=[§1.1]", "2026-10-14T10:03:00Z", nid=4),
    ]
    verdict, note = review_approve.parse_verdict(notes)
    assert verdict == "APPROVED" and note["id"] == 3
    assert review_approve.parse_verdict([]) == (None, None)
    assert review_approve.parse_verdict([notes[0], notes[1]]) == (None, None)
    assert review_approve.parse_verdict([_note("Autopilot review: NOT-AUTOPILOT" + SIG, "x")])[0] == "NOT-AUTOPILOT"


class _GL:
    def __init__(self, status=200):
        self.status = status
        self.calls = []

    def project_web_url(self):
        return "https://gl/p"

    def post(self, path, json=None, data=None):
        self.calls.append(("post", path))
        if self.status != 200:
            req = httpx.Request("POST", "https://gl" + path)
            raise httpx.HTTPStatusError("x", request=req, response=httpx.Response(self.status, request=req))
        return {}

    def mr_note(self, iid, body):
        self.calls.append(("note", iid, body))


def test_approve_posts_approval_and_honest_note():
    gl = _GL()
    assert review_approve.approve(gl, 7, {"id": 99}) == "approved"
    assert gl.calls[0] == ("post", "merge_requests/7/approve")
    body = gl.calls[1][2]
    assert "performed by the CI token" in body and "#note_99" in body and body.endswith("autopilot: rules=[§3.2, §9.2]")


def test_approve_is_idempotent_on_401_and_reports_other_errors():
    gl = _GL(401)
    assert review_approve.approve(gl, 7, {"id": 1}).startswith("already approved")
    assert all(c[0] != "note" for c in gl.calls)
    gl = _GL(403)
    assert review_approve.approve(gl, 7, {"id": 1}) == "approve call failed: HTTP 403"


def test_main_without_verdict_does_not_approve(monkeypatch, capsys):
    monkeypatch.setenv("AUTOPILOT_TOKEN", "t")

    class GL:
        can_write = True

        def get_all(self, path, **kw):
            return []

    monkeypatch.setattr(review_approve, "GitLab", lambda: GL())
    assert review_approve.main(["--mr", "3", "--wait", "0"]) == 0
    assert "no F2 verdict note" in capsys.readouterr().out


def test_start_flow_bodies():
    assert start_flow.build_body(1, "g", definition="code_review/v1") == {
        "project_id": 1,
        "goal": "g",
        "start_workflow": True,
        "workflow_definition": "code_review/v1",
    }
    body = start_flow.build_body("1", "g", consumer_id=55, merge_request=7)
    assert body["ai_catalog_item_consumer_id"] == 55 and body["merge_request_id"] == 7
    assert body["environment"] == "ambient" and "workflow_definition" not in body
    with pytest.raises(ValueError):
        start_flow.build_body(1, "g")

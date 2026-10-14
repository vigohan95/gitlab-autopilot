"""AUTOPILOT §3.7: unresolved review threads (GitLab Duo Code Review findings first) hold auto-merge.

Found in the 2026-10-14 run 4: on MR !4 GitLab Duo Code Review left a valid finding on tests/test_app.py
(note 4010364289) at 07:37, and auto-merge merged at 07:39 because findings were advisory. The fixture has the
shape of ``GET /projects/:id/merge_requests/4/discussions`` (individual plain notes, a system note, a Duo DiffNote,
a resolved human thread); the ids of the F2 verdict and the Duo finding are the real ones, the other ids, the line
number and all bodies are illustrative.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import automerge  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "mr4_discussions.json"
MR_URL = "https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43190989/showcase/-/merge_requests/4"


def _discussions():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parser_finds_only_the_open_duo_thread():
    threads = automerge.unresolved_threads(_discussions())
    assert threads == [
        {
            "id": "9b8a7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b",
            "note_id": 4010364289,
            "author": "GitLabDuo",
            "duo": True,
            "path": "tests/test_app.py",
            "line": 112,
        }
    ]


def test_gate_holds_on_the_run4_finding_and_cites_the_rule():
    ok, reason = automerge.threads_gate(automerge.unresolved_threads(_discussions()), MR_URL)
    assert ok is False
    assert reason.startswith("§3.7 1 unresolved review thread(s), 1 from GitLab Duo Code Review")
    assert "on `tests/test_app.py` line 112" in reason and f"{MR_URL}#note_4010364289" in reason
    assert "releases the gate" in reason


def test_resolving_the_thread_releases_the_gate():
    data = _discussions()
    for d in data:
        for n in d["notes"]:
            if n.get("resolvable"):
                n["resolved"] = True
    assert automerge.unresolved_threads(data) == []
    assert automerge.threads_gate([], MR_URL) == (True, "§3.7 no unresolved review threads")


def test_plain_and_system_notes_never_hold():
    plain = [d for d in _discussions() if d["individual_note"]]
    assert automerge.unresolved_threads(plain) == []


def test_any_unresolved_human_thread_also_holds_and_duo_is_listed_first():
    data = _discussions()
    data[-1]["notes"][1]["resolved"] = False  # the human thread is re-opened by its reply
    threads = automerge.unresolved_threads(data)
    assert [t["author"] for t in threads] == ["GitLabDuo", "hanwg666"]
    ok, reason = automerge.threads_gate(list(reversed(threads)), "")
    assert ok is False and reason.startswith("§3.7 2 unresolved review thread(s), 1 from GitLab Duo Code Review")
    assert reason.index("GitLab Duo Code Review on") < reason.index("@hanwg666")
    assert "note 4010350001" in reason


def test_wait_for_duo_review_returns_when_duo_has_posted():
    class GL:
        def __init__(self):
            self.calls = 0

        def get_all(self, path, **kw):
            self.calls += 1
            return [n for d in _discussions() for n in d["notes"]]

    gl = GL()
    assert automerge.wait_for_duo_review(gl, 4, 0) is True and gl.calls == 1

    class Empty:
        def get_all(self, path, **kw):
            return [{"author": {"username": "hanwg666"}}]

    assert automerge.wait_for_duo_review(Empty(), 4, 0) is False

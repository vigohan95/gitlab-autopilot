"""Arithmetic of scripts/sci.py (documented in docs/SCI.md) and the journey card's lead-time / human-touch lines."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import journey_card  # noqa: E402
import sci  # noqa: E402


def test_runner_watts_follow_the_ccf_model():
    # 2 vCPU x (0.71 + 0.5 x (4.26 - 0.71)) W + 8 GB x 0.392 W/GB = 4.97 + 3.136 = 8.106 W
    assert sci.watts() == pytest.approx(8.106, abs=1e-6)


def test_one_runner_hour_energy_and_carbon():
    # 8.106 W for 1 h = 8.106 Wh, x PUE 1.1 = 8.9166 Wh = 0.0089166 kWh; x 576 g/kWh = 5.136 g
    assert sci.energy_kwh(3600) == pytest.approx(0.0089166, abs=1e-7)
    est = sci.estimate([{"duration": 3600}])
    assert est["ci_minutes"] == 60.0
    assert est["g_co2e"] == pytest.approx(5.136, abs=1e-3)
    assert est["g_co2e_cfe_adjusted"] == pytest.approx(0.0089166 * 397.4, abs=1e-3)


def test_estimate_ignores_jobs_without_duration_and_writes_json(tmp_path):
    jobs = [{"duration": 30}, {"duration": None}, {"duration": 90}, {}]
    est = sci.estimate(jobs)
    assert est["jobs"] == 4 and est["ci_seconds"] == 120.0 and est["ci_minutes"] == 2.0
    assert "gCO2e" in sci.card_line(est) and "docs/SCI.md" in sci.card_line(est)
    src = tmp_path / "jobs.json"
    src.write_text(json.dumps(jobs), encoding="utf-8")
    out = tmp_path / "sci.json"
    assert sci.main(["--jobs-json", str(src), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["ci_minutes"] == 2.0


def test_monitor_cadence_arithmetic():
    # measured on the history project: a healthy scheduled run = monitor 30 s + semgrep-sast 31 s + audit-trail 32 s
    assert sci.schedule_minutes_per_day(96, 93) == 148.8  # every 15 min, three jobs
    assert sci.schedule_minutes_per_day(12, 30) == 6.0  # every 2 h, monitor only


def test_lead_time_issue_to_production():
    issue = {"iid": 2, "created_at": "2026-10-10T13:19:20.995Z", "author": {"username": "hanwg666"}}
    jobs = [
        {"name": "test", "stage": "test", "status": "success", "duration": 30, "finished_at": "2026-10-10T14:11:38Z"},
        {
            "name": "deploy-production",
            "stage": "deploy-production",
            "status": "success",
            "duration": 32,
            "finished_at": "2026-10-10T14:16:23.956Z",
        },
        {
            "name": "audit-trail",
            "stage": "govern",
            "status": "success",
            "duration": 34,
            "finished_at": "2026-10-10T14:17:00Z",
        },
    ]
    seconds, start, end = journey_card.lead_time(issue, jobs)
    assert round(seconds / 60) == 57 and start == "13:19Z" and end == "14:16Z"
    assert journey_card.fmt_duration(seconds) == "57 min"
    assert journey_card.fmt_duration(3600 * 1.5) == "1 h 30 min"
    assert journey_card.lead_time(None, jobs)[0] is None
    # without a production deploy the last finished job ends the measurement
    seconds, _, end = journey_card.lead_time(issue, jobs[:1])
    assert end == "14:11Z"


def test_human_touches_counts_only_humans_after_filing():
    issue = {"iid": 2, "created_at": "2026-10-10T13:19:20Z"}
    issue_notes = [
        {
            "system": True,
            "author": {"username": "hanwg666"},
            "created_at": "2026-10-10T13:19:21Z",
            "body": "added label",
        },
        {  # the plan posted by the flow under composite identity: carries the signature -> not a human action
            "system": False,
            "author": {"username": "hanwg666"},
            "created_at": "2026-10-10T13:21:00Z",
            "body": "Plan ...\n\nautopilot: rules=[section 1.1]",
        },
        {
            "system": False,
            "author": {"username": "hanwg666"},
            "created_at": "2026-10-10T13:40:00Z",
            "body": "looks good",
        },
        {"system": False, "author": {"username": "hanwg666"}, "created_at": "2026-10-10T13:00:00Z", "body": "earlier"},
    ]
    mr_notes = [
        {
            "system": False,
            "author": {"username": "project_87424108_bot_1"},
            "created_at": "2026-10-10T14:00:00Z",
            "body": "x",
        },
        {"system": False, "author": {"username": "ai-f2-reviewer"}, "created_at": "2026-10-10T14:01:00Z", "body": "y"},
        {
            "system": False,
            "author": {"username": "judge1", "bot": False},
            "created_at": "2026-10-10T14:02:00Z",
            "body": "?",
        },
    ]
    approvals = {"approved_by": [{"user": {"username": "project_87424108_bot_1"}}, {"user": {"username": "judge1"}}]}
    commits = [
        {
            "author_name": "f1-planner-developer",
            "author_email": "service_account_group_1@noreply.gitlab.com",
            "created_at": "2026-10-10T14:05:52Z",
        },
        {"author_name": "Jane Human", "author_email": "jane@example.com", "created_at": "2026-10-10T14:06:00Z"},
    ]
    count, detail = journey_card.human_touches(issue, issue_notes, mr_notes, approvals, commits)
    assert count == 4
    assert detail == [
        "issue note by @hanwg666",
        "MR note by @judge1",
        "approval by @judge1",
        "commit by Jane Human",
    ]
    assert journey_card.human_touches(issue, [], [], {"approved_by": []}, []) == (0, [])


def test_journey_card_has_lead_time_touches_and_sci_lines():
    issue = {"iid": 2, "created_at": "2026-10-10T13:19:20Z", "author": {"username": "hanwg666"}}
    mr = {"iid": 1, "title": "t", "author": {"username": "hanwg666"}, "labels": ["autopilot", "autopilot::approved"]}
    jobs = [
        {
            "name": "deploy-production",
            "stage": "deploy-production",
            "status": "success",
            "duration": 600,
            "finished_at": "2026-10-10T14:16:23Z",
        }
    ]
    card = journey_card.render(jobs, mr, issue, "https://x/-/pipelines/1", touches=(0, []))
    assert "**Lead time:** issue created 13:19Z → production deployed 14:16Z = **57 min**." in card
    assert "**Human actions after filing:** **0** (none;" in card
    assert "**CI footprint (estimate):** 10.0 runner-minutes" in card and "gCO2e" in card
    assert card.rstrip().endswith("autopilot: rules=[§1.2, §9.2]")
    # no issue -> no lead-time line, card still renders
    assert "Lead time" not in journey_card.render([], None, None, "")

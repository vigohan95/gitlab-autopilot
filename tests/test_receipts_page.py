"""Offline rendering of the GitLab Pages receipts page (scripts/receipts_page.py)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import receipts_page  # noqa: E402

CARD = (
    "## Autopilot journey card\n\n| stage | covered by |\n\n"
    "**9/9 stages touched**, 24 jobs, 9.0 min of CI time, 0 retried jobs.\n"
    "**Lead time:** issue created 13:19Z → production deployed 14:16Z = **57 min**.\n"
    "**Human actions after filing:** **0** (none; counted: ...).\n"
    "**CI footprint (estimate):** 9.02 runner-minutes ≈ 0.0013 kWh ≈ **0.77 gCO2e** per journey (0.53 g ...)\n\n"
    "autopilot: rules=[§1.2, §9.2]"
)


def test_card_numbers_extracts_the_comparable_fields():
    assert receipts_page.card_numbers(CARD) == {
        "lead_time": "57 min",
        "human_actions": 0,
        "runner_minutes": "9.02",
        "g_co2e": "0.77",
        "stages": 9,
    }
    assert receipts_page.card_numbers("nothing here") == {}


def test_render_lists_journeys_incidents_and_links(tmp_path):
    data = {
        "project": "https://gl/p",
        "generated_at": "2026-10-14T03:00:00+00:00",
        "journeys": [
            {
                "iid": 2,
                "title": "Add <p95>",
                "url": "https://gl/p/-/issues/2",
                "author": "hanwg666",
                "created_at": "2026-10-10T13:19:20Z",
                "mr": {"iid": 1, "url": "https://gl/p/-/merge_requests/1", "merged_at": "2026-10-10T14:10:32Z"},
                "card": {"url": "https://gl/p/-/issues/2#note_1", **receipts_page.card_numbers(CARD)},
                "narrative_url": "https://gl/p/-/issues/2#note_2",
            },
            {"iid": 3, "title": "no card yet", "url": "https://gl/p/-/issues/3", "notes_error": "HTTPStatusError"},
        ],
        "security_fixes": [{"iid": 4, "title": "bump", "url": "https://gl/p/-/merge_requests/4", "state": "merged"}],
        "incidents": [
            {
                "iid": 5,
                "title": "Production unhealthy",
                "url": "https://gl/p/-/issues/5",
                "state": "opened",
                "labels": ["rca::done"],
            }
        ],
    }
    page = receipts_page.render(data)
    assert "2 journeys" in page and "Add &lt;p95&gt;" in page
    assert 'href="https://gl/p/-/issues/2#note_1"' in page and ">narrative<" in page
    assert "<td>57 min</td>" in page and "<td>0</td>" in page and "9.02 min · 0.77 g" in page
    assert "notes need sign-in" in page and "!4 bump" in page and "#5 Production unhealthy" in page
    src = tmp_path / "receipts.json"
    src.write_text(json.dumps(data), encoding="utf-8")
    assert receipts_page.main(["--from-json", str(src), "--out", str(tmp_path / "public")]) == 0
    assert (tmp_path / "public" / "index.html").exists() and (tmp_path / "public" / "receipts.json").exists()
    assert "no autopilot issues yet" in receipts_page.render({"journeys": []})

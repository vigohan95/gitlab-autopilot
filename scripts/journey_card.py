"""Post the "journey card" on the originating issue after a main pipeline (AUTOPILOT §1.2).

The card lists the nine DevSecOps stages, which pipeline jobs covered each,
how long they took and how many retries happened. It is the deterministic twin
of the Closer flow: the flow writes the narrative, this script writes the facts.
Always writes ``journey-card.md`` as an artifact; posts to the issue and closes
it only when ``AUTOPILOT_TOKEN`` is available and ``--close`` is given.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

from gitlab_api import GitLab, warn

# CI stage -> DevSecOps lifecycle stage
STAGE_MAP = {
    "plan": "plan",
    "test": "verify",
    "quality": "verify",
    "secure": "secure",
    "build": "package",
    "package": "package",
    "release": "release",
    "deploy-staging": "configure",
    "verify-staging": "verify",
    "deploy-production": "configure",
    "monitor": "monitor",
    "govern": "govern",
    "create": "create",
}
LIFECYCLE = ["plan", "create", "verify", "package", "secure", "release", "configure", "monitor", "govern"]


def render(jobs: list[dict], mr: dict | None, issue: dict | None, pipeline_url: str) -> str:
    covered: dict[str, list[str]] = defaultdict(list)
    retries = 0
    total = 0.0
    for j in jobs:
        life = STAGE_MAP.get(j["stage"], j["stage"])
        covered[life].append(f"{j['name']} ({j['status']}, {round(j.get('duration') or 0)}s)")
        total += j.get("duration") or 0
        if j.get("retried"):
            retries += 1
    # plan and create are covered by the issue and the merge request, not by jobs
    if issue:
        covered["plan"].insert(0, f"issue #{issue['iid']} planned by Planner flow")
    if mr:
        covered["create"].insert(0, f"!{mr['iid']} opened by Developer flow, reviewed by autopilot-reviewer")

    lines = ["## Autopilot journey card", ""]
    if mr:
        lines.append(f"Merge request: !{mr['iid']} — {mr['title']}")
    lines += [f"Pipeline: {pipeline_url}", "", "| stage | covered by |", "|---|---|"]
    for life in LIFECYCLE:
        items = covered.get(life, [])
        mark = "✅" if items else "⬜"
        lines.append(f"| {mark} {life} | {'<br>'.join(items) if items else '—'} |")
    n = sum(1 for s in LIFECYCLE if covered.get(s))
    lines += [
        "",
        f"**{n}/9 stages touched**, {len(jobs)} jobs, {round(total / 60, 1)} min of CI time, {retries} retried jobs.",
        "",
        "autopilot: rules=[§1.2, §9.2]",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pipeline", default=os.environ.get("CI_PIPELINE_ID"))
    ap.add_argument("--sha", default=os.environ.get("CI_COMMIT_SHA"))
    ap.add_argument("--close", action="store_true", help="close the originating issue (§1.2)")
    args = ap.parse_args(argv)

    gl = GitLab()
    pipeline_url = os.environ.get("CI_PIPELINE_URL", "")
    jobs: list[dict] = []
    mr = issue = None
    try:
        jobs = gl.get_all(f"pipelines/{args.pipeline}/jobs", include_retried="true")
        mrs = gl.get(f"repository/commits/{args.sha}/merge_requests")
        mr = next((m for m in mrs if m.get("state") == "merged"), mrs[0] if mrs else None)
        if mr:
            closes = gl.get(f"merge_requests/{mr['iid']}/closes_issues")
            issue = closes[0] if closes else None
    except Exception as exc:  # noqa: BLE001
        warn(f"could not read pipeline/MR data ({type(exc).__name__}); writing a partial card")

    card = render(jobs, mr, issue, pipeline_url)
    Path("journey-card.md").write_text(card, encoding="utf-8")
    print(card)

    if issue and gl.can_write:
        gl.issue_note(issue["iid"], card)
        if args.close:
            gl.put(f"issues/{issue['iid']}", json={"state_event": "close"})
            print(f"closed issue #{issue['iid']}")
    elif not gl.can_write:
        warn("AUTOPILOT_TOKEN not set; card written as artifact only")
    return 0


if __name__ == "__main__":
    sys.exit(main())

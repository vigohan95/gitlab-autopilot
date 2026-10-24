"""Post the "journey card" on the originating issue after a main pipeline (AUTOPILOT §1.2).

The card lists the nine DevSecOps stages, which pipeline jobs covered each,
how long they took and how many retries happened, plus three numbers judges
compare across entries: the **lead time** (issue created → production deployed),
the **human actions after filing** and the **CI footprint** (``scripts/sci.py``,
an estimate). It is the deterministic twin of the Closer flow: the flow writes
the narrative, this script writes the facts.
Always writes ``journey-card.md`` as an artifact; posts to the issue and closes
it only when ``AUTOPILOT_TOKEN`` is available and ``--close`` is given.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    from . import sci
    from .gitlab_api import GitLab, warn
except ImportError:
    import sci
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
# Accounts that are not humans: project/group access-token bots, Duo flow service accounts.
_BOT_RE = re.compile(r"^(project|group)_\d+_bot|^ai-|^duo-|duo|^f\d-[a-z]|service_account", re.I)
_AGENT_SIGNATURE = "autopilot: rules="


def _actor(obj: dict | None) -> str:
    user = ((obj or {}).get("author") or {}).get("username")
    if not user:
        return "unknown actor"
    if user.startswith(("ai-", "duo-")) or "duo" in user:
        return f"@{user} (GitLab Duo flow service account)"
    return f"@{user}"


def is_bot(user: dict | None) -> bool:
    user = user or {}
    return bool(user.get("bot")) or bool(_BOT_RE.search(user.get("username") or ""))


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    m = int(round(seconds / 60))
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60:02d} min"


def lead_time(issue: dict | None, jobs: list[dict]) -> tuple[float | None, str, str]:
    """(seconds from issue creation to the end of deploy-production, start label, end label)."""
    start = _ts((issue or {}).get("created_at"))
    ends = [
        _ts(j.get("finished_at"))
        for j in jobs
        if j.get("name") == "deploy-production" and j.get("status") == "success" and j.get("finished_at")
    ]
    end = max((e for e in ends if e), default=None)
    if not end:  # no production deploy in this pipeline: fall back to the last finished job
        end = max((_ts(j.get("finished_at")) for j in jobs if j.get("finished_at")), default=None)
    if not start or not end:
        return None, "", ""
    return (end - start).total_seconds(), start.strftime("%H:%MZ"), end.strftime("%H:%MZ")


def human_touches(
    issue: dict | None,
    issue_notes: list[dict] | None = None,
    mr_notes: list[dict] | None = None,
    approvals: dict | None = None,
    commits: list[dict] | None = None,
) -> tuple[int, list[str]]:
    """Count human actions after the issue was filed: non-system notes by human accounts that do not carry the
    agent signature, human approvals, and merge-request commits by human authors. Filing the issue itself is
    not counted. Composite identity may attribute a flow's note or commit to the human who filed the issue;
    the signature check catches agent notes, the service-account e-mail catches flow commits."""
    filed = _ts((issue or {}).get("created_at"))
    found: list[str] = []
    for kind, notes in (("issue", issue_notes or []), ("MR", mr_notes or [])):
        for n in notes:
            if n.get("system") or is_bot(n.get("author")):
                continue
            if _AGENT_SIGNATURE in (n.get("body") or ""):
                continue
            when = _ts(n.get("created_at"))
            if filed and when and when <= filed:
                continue
            found.append(f"{kind} note by @{(n.get('author') or {}).get('username', '?')}")
    for a in (approvals or {}).get("approved_by", []):
        if not is_bot(a.get("user")):
            found.append(f"approval by @{(a.get('user') or {}).get('username', '?')}")
    for c in commits or []:
        email = (c.get("author_email") or "").lower()
        if "service_account" in email or email.endswith("@noreply.gitlab.com"):
            continue
        if _BOT_RE.search(c.get("author_name") or ""):
            continue
        when = _ts(c.get("created_at"))
        if filed and when and when <= filed:
            continue
        found.append(f"commit by {c.get('author_name', '?')}")
    return len(found), found


def render(
    jobs: list[dict],
    mr: dict | None,
    issue: dict | None,
    pipeline_url: str,
    monitor: dict | None = None,
    touches: tuple[int, list[str]] | None = None,
) -> str:
    covered: dict[str, list[str]] = defaultdict(list)
    retries = 0
    total = 0.0
    for j in jobs:
        life = STAGE_MAP.get(j["stage"], j["stage"])
        covered[life].append(f"{j['name']} ({j['status']}, {round(j.get('duration') or 0)}s)")
        total += j.get("duration") or 0
        if j.get("retried"):
            retries += 1
    # plan and create are covered by the issue and the merge request, not by jobs; report who really did it
    if issue:
        covered["plan"].insert(0, f"issue #{issue['iid']} opened by {_actor(issue)}, label autopilot")
    if mr:
        labels = mr.get("labels") or []
        review = "policy-check (autopilot::approved)" if "autopilot::approved" in labels else "no approval label"
        covered["create"].insert(0, f"!{mr['iid']} opened by {_actor(mr)}, reviewed by {review}")
    if monitor:
        covered["monitor"].insert(
            0, f"scheduled monitor pipeline {monitor.get('id')} ({monitor.get('status')}) {monitor.get('web_url', '')}"
        )

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
    ]
    seconds, start, end = lead_time(issue, jobs)
    if seconds is not None:
        lines.append(f"**Lead time:** issue created {start} → production deployed {end} = **{fmt_duration(seconds)}**.")
    if touches is not None:
        count, detail = touches
        what = "; ".join(detail[:6]) if detail else "none"
        lines.append(
            f"**Human actions after filing:** **{count}** ({what}; counted: non-system notes, approvals and MR "
            "commits by human accounts without the `autopilot: rules=` signature)."
        )
    lines.append(sci.card_line(sci.estimate(jobs)))
    lines += ["", "autopilot: rules=[§1.2, §9.2]"]
    return "\n".join(lines)


def supersede_old_cards(gl: GitLab, issue_iid: int) -> int:
    """Prefix earlier journey-card notes with a superseded banner (notes were posted by the same token)."""
    n = 0
    for note in gl.get_all(f"issues/{issue_iid}/notes"):
        body = note.get("body") or ""
        if "Autopilot journey card" in body and "SUPERSEDED" not in body:
            banner = (
                "> **SUPERSEDED** — this card credited Planner/Developer flows that had not run "
                "(GitLab Duo was not enabled on this project at the time). See the corrected card below.\n\n"
            )
            try:
                gl.put(f"issues/{issue_iid}/notes/{note['id']}", json={"body": banner + body})
                n += 1
            except Exception as exc:  # noqa: BLE001
                warn(f"could not edit note {note['id']}: {type(exc).__name__}")
    print(f"superseded {n} earlier card(s)")
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pipeline", default=os.environ.get("CI_PIPELINE_ID"))
    ap.add_argument("--sha", default=os.environ.get("CI_COMMIT_SHA"))
    ap.add_argument("--close", action="store_true", help="close the originating issue (§1.2)")
    ap.add_argument(
        "--supersede",
        action="store_true",
        help="mark earlier journey-card notes on the issue as superseded before posting (used to correct a wrong card)",
    )
    args = ap.parse_args(argv)

    gl = GitLab()
    # link the pipeline the card describes (may differ from the job's own pipeline when re-posting a card)
    pipeline_url = (
        f"{gl.project_web_url()}/-/pipelines/{args.pipeline}"
        if args.pipeline
        else os.environ.get("CI_PIPELINE_URL", "")
    )
    jobs: list[dict] = []
    mr = issue = monitor = None
    touches: tuple[int, list[str]] | None = None
    try:
        jobs = gl.get_all(f"pipelines/{args.pipeline}/jobs", include_retried="true")
        scheduled = gl.get("pipelines", source="schedule", per_page=1)
        monitor = scheduled[0] if scheduled else None
        mrs = gl.get(f"repository/commits/{args.sha}/merge_requests")
        mr = next((m for m in mrs if m.get("state") == "merged"), mrs[0] if mrs else None)
        if mr:
            closes = gl.get(f"merge_requests/{mr['iid']}/closes_issues")
            issue = closes[0] if closes else None
    except Exception as exc:  # noqa: BLE001
        warn(f"could not read pipeline/MR data ({type(exc).__name__}); writing a partial card")
    if issue and mr:
        try:  # notes need a token on GitLab.com; without one the count is simply omitted
            touches = human_touches(
                issue,
                gl.get_all(f"issues/{issue['iid']}/notes"),
                gl.get_all(f"merge_requests/{mr['iid']}/notes"),
                gl.get(f"merge_requests/{mr['iid']}/approvals"),
                gl.get_all(f"merge_requests/{mr['iid']}/commits"),
            )
        except Exception as exc:  # noqa: BLE001
            warn(f"could not read notes/approvals ({type(exc).__name__}); human-touch count omitted")

    card = render(jobs, mr, issue, pipeline_url, monitor, touches)
    Path("journey-card.md").write_text(card, encoding="utf-8")
    print(card)

    if issue and gl.can_write:
        if args.supersede:
            supersede_old_cards(gl, issue["iid"])
            card = (
                "**Corrected journey card.** The earlier card on this issue credited Planner/Developer flows that had "
                "not run; this one reports the actors GitLab recorded.\n\n" + card
            )
        gl.issue_note(issue["iid"], card)
        if args.close:
            gl.put(f"issues/{issue['iid']}", json={"state_event": "close"})
            print(f"closed issue #{issue['iid']}")
    elif not gl.can_write:
        warn("AUTOPILOT_TOKEN not set; card written as artifact only")
    return 0


if __name__ == "__main__":
    sys.exit(main())

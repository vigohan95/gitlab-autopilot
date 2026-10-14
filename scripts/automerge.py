"""Merge an autopilot merge request when AUTOPILOT §3 is satisfied.

Runs as the last job of a merge-request pipeline, so every other job in the
pipeline has already succeeded when this executes (§3.1). It verifies the
remaining gate conditions through the API and then sets
"merge when pipeline succeeds" (§3.6). The decision and the rules used are
posted on the merge request.

Never fails the pipeline: a "human required" outcome is a label + a note.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from gitlab_api import GitLab, warn
from policy import evaluate_mr, load_policy
from review_approve import parse_verdict


def finding_key(v: dict) -> str:
    """Stable identity of a finding across pipelines: scanner + first identifier + location."""
    loc = v.get("location") or {}
    where = loc.get("file") or loc.get("image") or loc.get("dependency", {}).get("package", {}).get("name") or ""
    if loc.get("image"):
        # the image is tagged per commit (registry/path:<sha>); the same base-image CVE must key identically
        where = where.split("@", 1)[0]
        head, _, tail = where.rpartition(":")
        if head and "/" not in tail:
            where = head
    ident = (v.get("identifiers") or [{}])[0].get("external_id") or v.get("name") or ""
    return f"{v.get('report_type', '')}:{ident}@{where}"


def new_high_findings(
    gl: GitLab, mr_pipeline_id: int, base_pipeline_id: int | list[int] | None
) -> tuple[list[str], int]:
    """(keys of High/Critical findings present on the MR pipeline but on none of the base pipelines, total on MR).

    ``base_pipeline_id`` may be one id or several: a finding already present on any baseline is not new.
    """
    params = {"severity": "critical,high", "scope": "all"}
    mr_v = gl.get_all(f"/projects/{gl.project}/vulnerability_findings", pipeline_id=mr_pipeline_id, **params)
    if base_pipeline_id is None:
        base_ids: list[int] = []
    elif isinstance(base_pipeline_id, int):
        base_ids = [base_pipeline_id]
    else:
        base_ids = [i for i in base_pipeline_id if i]
    base_keys: set[str] = set()
    for bid in dict.fromkeys(base_ids):
        base_v = gl.get_all(f"/projects/{gl.project}/vulnerability_findings", pipeline_id=bid, **params)
        base_keys |= {finding_key(v) for v in base_v}
    new = sorted({finding_key(v) for v in mr_v} - base_keys)
    return new, len(mr_v)


# Pipeline sources that never run the security scanners (.gitlab-ci.yml rules): the monitor schedule, rollback
# triggers, parent/child and chat-ops pipelines.
NON_SCANNING_SOURCES = {"schedule", "trigger", "pipeline", "parent_pipeline", "chat"}


def baseline_pipelines(gl: GitLab, target_branch: str, default_branch: str) -> list[tuple[str, int]]:
    """Latest green pipeline on the target branch and on the default branch.

    Found in the 2026-10-14 security drill: a fix MR targeting a feature branch whose only pipeline had failed
    at ``pip-audit`` (so container scanning never ran there) had no baseline, and every pre-existing base-image
    CVE counted as new. Findings already present on the default branch are never new to an MR.

    Only pipelines that run the scanners count (run 5 of the same drill): scheduled monitor runs, rollback
    triggers and other non-scanning sources are skipped, otherwise the newest green pipeline on main is a
    scanner-free schedule run with zero findings and every pre-existing CVE counts as new again.
    """
    out: list[tuple[str, int]] = []
    for ref in dict.fromkeys([target_branch, default_branch]):
        found = gl.get("pipelines", ref=ref, status="success", per_page=20) or []
        scanning = [p for p in found if p.get("source") not in NON_SCANNING_SOURCES]
        if scanning:
            out.append((ref, scanning[0]["id"]))
    return out


def reviewer_gate(
    approved_by: list[str], verdict: str | None, labels: list[str], approved_label: str
) -> tuple[bool, str]:
    """§3.2 decision. The reviewer agent's newest signed verdict decides (a recorded MR approval also counts, but
    in the showcase none is recorded: the approve call answers 401 for our token); the ``autopilot::approved``
    label only counts when no reviewer verdict exists (no reviewer agent enabled).

    Found in the 2026-10-14 drill: policy-check added the scoped label ``autopilot::approved``, which made GitLab
    drop the reviewer agent's ``autopilot::needs-human``, and the gate then read the label as the reviewer's
    approval. A deterministic check must never override the reviewer agent's HUMAN REQUIRED.
    """
    if approved_by:
        return True, f"§3.2 approved by: {', '.join(approved_by)}"
    if verdict == "APPROVED":
        return True, "§3.2 reviewer agent verdict: APPROVED"
    if verdict:
        return False, f"§3.2 reviewer agent verdict: {verdict} (a policy-check label cannot override it)"
    if approved_label in labels:
        return True, f"§3.2 no reviewer agent verdict; policy check is the reviewer of record ({approved_label})"
    return False, "§3.2 no approval from the reviewer agent yet"


# GitLab Duo Code Review posts as this user on GitLab.com (user id 21826781, "GitLab Duo").
DUO_BOTS = ("GitLabDuo",)


def unresolved_threads(discussions: list[dict], bots: tuple[str, ...] = DUO_BOTS) -> list[dict]:
    """Unresolved resolvable threads of a merge request, from ``GET /merge_requests/:iid/discussions``.

    A thread counts when it has at least one non-system note with ``resolvable: true`` and ``resolved: false``
    (GitLab resolves a thread by resolving all of its resolvable notes). Plain comments, including the agents'
    signed verdict notes and the policy notes, are not resolvable and never count. Each entry:
    ``{"id", "note_id", "author", "duo", "path", "line"}``; ``duo`` is True when the thread was started by a
    GitLab Duo Code Review bot account.
    """
    out: list[dict] = []
    for d in discussions:
        notes = [n for n in d.get("notes") or [] if not n.get("system")]
        open_notes = [n for n in notes if n.get("resolvable") and not n.get("resolved")]
        if not open_notes:
            continue
        first = notes[0]
        pos = first.get("position") or {}
        out.append(
            {
                "id": d.get("id"),
                "note_id": first.get("id"),
                "author": (first.get("author") or {}).get("username", ""),
                "duo": (first.get("author") or {}).get("username", "") in bots,
                "path": pos.get("new_path") or pos.get("old_path"),
                "line": pos.get("new_line") or pos.get("old_line"),
            }
        )
    return out


def threads_gate(threads: list[dict], mr_url: str = "") -> tuple[bool, str]:
    """§3.7 decision: any unresolved resolvable thread holds the merge; Duo Code Review threads are named first.

    Found in the 2026-10-14 run 4 (MR !4): GitLab Duo Code Review left a valid finding on tests/test_app.py two
    minutes before auto-merge, which merged past it because findings were advisory.
    """
    if not threads:
        return True, "§3.7 no unresolved review threads"
    threads = sorted(threads, key=lambda t: not t["duo"])
    duo = sum(1 for t in threads if t["duo"])
    links = []
    for t in threads[:5]:
        where = f" on `{t['path']}`" + (f" line {t['line']}" if t["line"] else "") if t["path"] else ""
        who = "GitLab Duo Code Review" if t["duo"] else f"@{t['author']}"
        ref = f"[note {t['note_id']}]({mr_url}#note_{t['note_id']})" if mr_url else f"note {t['note_id']}"
        links.append(f"{who}{where} ({ref})")
    return False, (
        f"§3.7 {len(threads)} unresolved review thread(s), {duo} from GitLab Duo Code Review: "
        + "; ".join(links)
        + ". Resolving them (a human, or a follow-up commit and then resolve) releases the gate on the next "
        "pipeline or a re-run of `auto-merge`"
    )


def wait_for_duo_review(gl: GitLab, iid: int, seconds: int, interval: int = 20) -> bool:
    """True once GitLab Duo Code Review has posted anything on the MR, polling for at most ``seconds``.

    ``review-handoff`` starts Code Review at the beginning of the pipeline; on MR !4 it posted its finding at
    07:37, two minutes before auto-merge ran. Waiting closes the race where the review lands after the gate.
    """
    deadline = time.monotonic() + max(seconds, 0)
    while True:
        try:
            notes = gl.get_all(f"merge_requests/{iid}/notes")
            if any((n.get("author") or {}).get("username") in DUO_BOTS for n in notes):
                return True
        except Exception as exc:  # noqa: BLE001
            warn(f"could not read notes ({type(exc).__name__})")
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mr", type=int, default=int(os.environ.get("CI_MERGE_REQUEST_IID", "0") or 0))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--review-wait", type=int, default=0, help="seconds to wait for GitLab Duo Code Review to post (§3.7)"
    )
    args = ap.parse_args(argv)
    if not args.mr:
        print("not a merge request pipeline; nothing to do")
        return 0

    gl = GitLab()
    if not gl.can_write:
        warn("AUTOPILOT_TOKEN not set; cannot merge (masked, NOT protected: MR pipelines use unprotected branches)")
        return 0

    policy = load_policy()
    decision = evaluate_mr(policy, args.mr)
    if decision.frozen:  # §1.3: policy-check already left the one note; change nothing
        print(decision.as_markdown())
        return 0
    mr = gl.get(f"merge_requests/{args.mr}")
    approvals = gl.get(f"merge_requests/{args.mr}/approvals")
    approved_by = [a["user"]["username"] for a in approvals.get("approved_by", [])]
    try:
        verdict, _ = parse_verdict(gl.get_all(f"merge_requests/{args.mr}/notes"))
    except Exception as exc:  # noqa: BLE001 - unreadable notes must not count as an approval
        warn(f"could not read reviewer notes ({type(exc).__name__})")
        verdict = "UNREADABLE"
    reviewer_ok, reason = reviewer_gate(approved_by, verdict, mr.get("labels", []), policy["labels"]["approved"])
    decision.reasons.append(reason)
    if reviewer_ok:
        decision.satisfied.append("§3.2")
    else:
        decision.allowed = False
        decision.violated.append("§3.2")

    # §3.3: no NEW High/Critical findings — compare the MR pipeline with the latest green pipeline on the target
    # branch, so pre-existing base-image / dependency findings on main do not block every merge request.
    try:
        mr_pipelines = gl.get(f"merge_requests/{args.mr}/pipelines")
        pipeline_id = mr_pipelines[0]["id"] if mr_pipelines else None
        if pipeline_id:
            default_branch = os.environ.get("CI_DEFAULT_BRANCH") or "main"
            bases = baseline_pipelines(gl, mr["target_branch"], default_branch)
            refs = " + ".join(r for r, _ in bases) or "no green baseline"
            new, total = new_high_findings(gl, pipeline_id, [i for _, i in bases])
            if new:
                decision.allowed = False
                decision.violated.append("§3.3")
                decision.reasons.append(
                    f"§3.3 {len(new)} NEW High/Critical findings (absent on {refs}): " + ", ".join(new[:5])
                )
            else:
                decision.satisfied.append("§3.3")
                decision.reasons.append(
                    f"§3.3 no new High/Critical findings ({total} pre-existing, tracked on {refs})"
                    if total
                    else "§3.3 no High/Critical findings"
                )
    except Exception as exc:  # noqa: BLE001 - endpoint is Ultimate-only; pip-audit job covers §3.3 on Free
        decision.satisfied.append("§3.3")
        decision.reasons.append(f"§3.3 vulnerability API unavailable ({type(exc).__name__}); relying on scanner jobs")

    # §3.7: unresolved review threads (GitLab Duo Code Review findings first) hold the merge.
    reviewed = wait_for_duo_review(gl, args.mr, args.review_wait if decision.allowed else 0)
    try:
        threads = unresolved_threads(gl.get_all(f"merge_requests/{args.mr}/discussions"))
        mr_url = mr.get("web_url") or ""
        threads_ok, reason = threads_gate(threads, mr_url)
        if not reviewed and args.review_wait:
            reason += f" (GitLab Duo Code Review had not posted after {args.review_wait} s; checked as of now)"
    except Exception as exc:  # noqa: BLE001 - unreadable threads must not count as resolved
        threads_ok, reason = False, f"§3.7 could not read review threads ({type(exc).__name__}); a human must check"
    decision.reasons.append(reason)
    if threads_ok:
        decision.satisfied.append("§3.7")
    else:
        decision.allowed = False
        decision.violated.append("§3.7")

    body = decision.as_markdown()
    print(body)
    if args.dry_run:
        return 0
    if decision.allowed:
        gl.put(
            f"merge_requests/{args.mr}/merge",
            json={
                "merge_when_pipeline_succeeds": policy["merge_options"]["merge_when_pipeline_succeeds"],
                "squash": policy["merge_options"]["squash"],
                "should_remove_source_branch": policy["merge_options"]["remove_source_branch"],
            },
        )
        gl.mr_note(args.mr, body + "\n\nMerging when this pipeline succeeds.")
        gl.add_labels("merge_requests", args.mr, [policy["labels"]["approved"]])
    else:
        gl.add_labels("merge_requests", args.mr, [policy["labels"]["needs_human"]])
        gl.mr_note(args.mr, body + "\n\nA human must review this merge request.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

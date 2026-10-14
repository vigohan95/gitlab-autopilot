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


def baseline_pipelines(gl: GitLab, target_branch: str, default_branch: str) -> list[tuple[str, int]]:
    """Latest green pipeline on the target branch and on the default branch.

    Found in the 2026-10-14 security drill: a fix MR targeting a feature branch whose only pipeline had failed
    at ``pip-audit`` (so container scanning never ran there) had no baseline, and every pre-existing base-image
    CVE counted as new. Findings already present on the default branch are never new to an MR.
    """
    out: list[tuple[str, int]] = []
    for ref in dict.fromkeys([target_branch, default_branch]):
        found = gl.get("pipelines", ref=ref, status="success", per_page=1)
        if found:
            out.append((ref, found[0]["id"]))
    return out


def reviewer_gate(
    approved_by: list[str], verdict: str | None, labels: list[str], approved_label: str
) -> tuple[bool, str]:
    """§3.2 decision. A real MR approval or the reviewer agent's newest signed verdict decides; the
    ``autopilot::approved`` label only counts when no reviewer verdict exists (no reviewer agent enabled).

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mr", type=int, default=int(os.environ.get("CI_MERGE_REQUEST_IID", "0") or 0))
    ap.add_argument("--dry-run", action="store_true")
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

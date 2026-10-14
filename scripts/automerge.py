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


def new_high_findings(gl: GitLab, mr_pipeline_id: int, base_pipeline_id: int | None) -> tuple[list[str], int]:
    """(keys of High/Critical findings present on the MR pipeline but not on the base pipeline, total on MR)."""
    params = {"severity": "critical,high", "scope": "all"}
    mr_v = gl.get_all(f"/projects/{gl.project}/vulnerability_findings", pipeline_id=mr_pipeline_id, **params)
    base_keys: set[str] = set()
    if base_pipeline_id:
        base_v = gl.get_all(f"/projects/{gl.project}/vulnerability_findings", pipeline_id=base_pipeline_id, **params)
        base_keys = {finding_key(v) for v in base_v}
    new = sorted({finding_key(v) for v in mr_v} - base_keys)
    return new, len(mr_v)


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
    reviewer_ok = bool(approved_by) or policy["labels"]["approved"] in mr.get("labels", [])
    if reviewer_ok:
        decision.satisfied.append("§3.2")
        decision.reasons.append(f"§3.2 approved by: {', '.join(approved_by) or policy['labels']['approved']}")
    else:
        decision.allowed = False
        decision.violated.append("§3.2")
        decision.reasons.append("§3.2 no approval from the reviewer agent yet")

    # §3.3: no NEW High/Critical findings — compare the MR pipeline with the latest green pipeline on the target
    # branch, so pre-existing base-image / dependency findings on main do not block every merge request.
    try:
        mr_pipelines = gl.get(f"merge_requests/{args.mr}/pipelines")
        pipeline_id = mr_pipelines[0]["id"] if mr_pipelines else None
        if pipeline_id:
            base = gl.get("pipelines", ref=mr["target_branch"], status="success", per_page=1)
            base_id = base[0]["id"] if base else None
            new, total = new_high_findings(gl, pipeline_id, base_id)
            if new:
                decision.allowed = False
                decision.violated.append("§3.3")
                decision.reasons.append(
                    f"§3.3 {len(new)} NEW High/Critical findings (absent on {mr['target_branch']}): "
                    + ", ".join(new[:5])
                )
            else:
                decision.satisfied.append("§3.3")
                decision.reasons.append(
                    f"§3.3 no new High/Critical findings ({total} pre-existing, tracked on {mr['target_branch']})"
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

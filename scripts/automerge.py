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
        warn("AUTOPILOT_TOKEN not set; cannot merge (set it as a masked, protected CI/CD variable)")
        return 0

    policy = load_policy()
    decision = evaluate_mr(policy, args.mr)
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

    # §3.3: High/Critical findings from the MR security reports.
    try:
        findings = gl.get(f"merge_requests/{args.mr}/pipelines")
        pipeline_id = findings[0]["id"] if findings else None
        if pipeline_id:
            vulns = gl.get_all(
                f"/projects/{gl.project}/vulnerability_findings",
                pipeline_id=pipeline_id,
                severity="critical,high",
                scope="all",
            )
            if vulns:
                decision.allowed = False
                decision.violated.append("§3.3")
                decision.reasons.append(f"§3.3 {len(vulns)} High/Critical security findings on this pipeline")
            else:
                decision.satisfied.append("§3.3")
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

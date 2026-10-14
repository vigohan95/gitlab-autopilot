"""Turn the reviewer agent's verdict into a real GitLab approval (AUTOPILOT §3.2).

The F2 Reviewer flow posts a summary note on the merge request:

    Autopilot review: APPROVED            (or: Autopilot review: HUMAN REQUIRED)
    - section 2.1 ... pass
    ...
    autopilot: rules=[section 2.1, section 2.2, section 3.4, section 3.5, section 9.1]

The flow is also told to approve with ``submit_mr_review``; whether a flow service account's approval
counts against an approval rule is not something we could verify (Itemator's and GitDone's write-ups say
the same). So this job — ``review-approve`` in the merge-request pipeline — reads the **latest** verdict
note, and when it is APPROVED calls ``POST /projects/:id/merge_requests/:iid/approve`` with the CI token
(``AUTOPILOT_TOKEN``, a project access token). The approval GitLab records is therefore **performed by
the CI token on the agent's verdict**; the note it leaves says exactly that. With no verdict (F2 did not
run, or has not finished within ``--wait`` seconds) nothing is approved and ``auto-merge`` falls back to
the ``policy-check`` label, as §3.2 allows. Never fails the pipeline.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time

import httpx
from gitlab_api import GitLab, warn

VERDICT_RE = re.compile(r"Autopilot review:\s*\**\s*(APPROVED|HUMAN[ -]REQUIRED|NOT-AUTOPILOT)\b", re.I)
SIGNATURE = "autopilot: rules="


def parse_verdict(notes: list[dict]) -> tuple[str | None, dict | None]:
    """(verdict, note) from the newest non-system note that carries both the verdict line and the rules
    signature (§9.2). Verdicts are normalised to APPROVED / HUMAN-REQUIRED / NOT-AUTOPILOT."""
    best: tuple[str, dict] | None = None
    for n in notes:
        if n.get("system"):
            continue
        body = n.get("body") or ""
        m = VERDICT_RE.search(body)
        if not m or SIGNATURE not in body:
            continue
        if best is None or (n.get("created_at") or "") >= (best[1].get("created_at") or ""):
            best = (m.group(1).upper().replace(" ", "-"), n)
    return (best[0], best[1]) if best else (None, None)


def approve(gl: GitLab, iid: int, verdict_note: dict) -> str:
    """Approve the MR with the CI token; returns a short outcome string. Idempotent across re-runs."""
    note_url = f"{gl.project_web_url()}/-/merge_requests/{iid}#note_{verdict_note.get('id')}"
    try:
        gl.post(f"merge_requests/{iid}/approve")
        outcome = "approved"
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 401:  # GitLab answers 401 when this user already approved (or the MR cannot be approved)
            outcome = "already approved by this token (or approval not permitted)"
        else:
            outcome = f"approve call failed: HTTP {code}"
            warn(outcome)
            return outcome
    if outcome == "approved":
        gl.mr_note(
            iid,
            "**§3.2 approval recorded.** The reviewer agent's verdict is "
            f"[Autopilot review: APPROVED]({note_url}); this approval was performed by the CI token "
            "(`AUTOPILOT_TOKEN`, project access token) on that verdict, because a flow service account's own "
            "approval cannot be relied on to satisfy the approval rule. The policy remains the agent's; the click "
            "is CI's.\n\nautopilot: rules=[§3.2, §9.2]",
        )
    return outcome


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mr", type=int, default=int(os.environ.get("CI_MERGE_REQUEST_IID", "0") or 0))
    ap.add_argument("--wait", type=int, default=0, help="seconds to keep polling for a verdict note")
    ap.add_argument("--interval", type=int, default=30)
    args = ap.parse_args(argv)
    if not args.mr:
        print("not a merge request pipeline; nothing to do")
        return 0
    gl = GitLab()
    if not gl.can_write:
        warn("AUTOPILOT_TOKEN not set; cannot approve")
        return 0

    deadline = time.monotonic() + max(args.wait, 0)
    verdict = note = None
    while True:
        try:
            verdict, note = parse_verdict(gl.get_all(f"merge_requests/{args.mr}/notes"))
        except Exception as exc:  # noqa: BLE001
            warn(f"could not read notes ({type(exc).__name__})")
        if verdict or time.monotonic() >= deadline:
            break
        print(f"no reviewer verdict yet on !{args.mr}; waiting {args.interval}s")
        time.sleep(args.interval)

    if verdict == "APPROVED":
        print(f"verdict APPROVED (note {note.get('id')}): {approve(gl, args.mr, note)}")
    elif verdict:
        print(f"verdict {verdict}: no approval; the agent's labels stand")
    else:
        print("no F2 verdict note; §3.2 falls back to the policy-check label (auto-merge decides)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

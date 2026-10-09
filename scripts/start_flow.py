"""Fallback: start a Duo Agent Platform flow from CI through the REST API.

GitLab triggers require a *human* actor (bots, service accounts and other flows
cannot fire a trigger — docs.gitlab.com/user/duo_agent_platform/triggers/).
When a step in the Autopilot chain is performed by a service account (for
example the Developer flow opening the merge request), the next trigger may
not fire. This script lets a CI job start the next flow explicitly, using the
project owner's token, so the chain stays hands-off.

    python scripts/start_flow.py --definition .gitlab/duo/flows/f2-reviewer.yaml --goal "Review !12"

Endpoint (documented for GitLab Duo Workflow; [VERIFY-IN-UI] current field names):
    POST {CI_API_V4_URL}/ai/duo_workflows/workflows
    Authorization: Bearer <PAT with api scope>  (PRIVATE-TOKEN returns 401 for flow tokens)
    {"project_id": <id>, "goal": "...", "workflow_definition": "...", "start_workflow": true}
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--goal", required=True)
    ap.add_argument(
        "--definition",
        required=True,
        help="flow id from the AI Catalog, a foundational id such as code_review/v1, or a .gitlab/duo/flows path",
    )
    ap.add_argument("--project", default=os.environ.get("CI_PROJECT_ID"))
    args = ap.parse_args(argv)

    token = os.environ.get("AUTOPILOT_TOKEN", "")
    if not token:
        print("AUTOPILOT_TOKEN not set; flow not started (set the token or rely on UI triggers)")
        return 0
    base = os.environ.get("CI_API_V4_URL", "https://gitlab.com/api/v4")
    body = {
        "project_id": int(args.project),
        "goal": args.goal,
        "workflow_definition": args.definition,
        "start_workflow": True,
    }
    r = httpx.post(
        f"{base}/ai/duo_workflows/workflows",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    if r.status_code >= 400:
        print(f"flow start failed: HTTP {r.status_code} {r.text[:300]}", file=sys.stderr)
        return 0  # advisory: never fail the pipeline because a Duo call failed
    print(f"flow started: {r.json()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

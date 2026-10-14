"""Fallback: start a Duo Agent Platform flow from CI through the REST API.

GitLab triggers require a *human* actor (bots, service accounts and other flows
cannot fire a trigger — docs.gitlab.com/user/duo_agent_platform/triggers/).
When a step in the Autopilot chain is performed by a service account (for
example the Developer flow opening the merge request), the next trigger may
not fire. This script lets a CI job start the next flow explicitly, using the
project's token, so the chain stays hands-off.

Two kinds of flow, two request shapes (ee/lib/api/ai/duo_workflows/workflows.rb):

* a **foundational** flow is named by ``workflow_definition`` — e.g. ``code_review/v1``::

      python scripts/start_flow.py --definition code_review/v1 --goal "$MR_URL"

* a **custom AI Catalog** flow (our F2 reviewer) is named by the id of its *consumer* — the record GitLab
  creates when the flow is enabled in the project (``ai_catalog_item_consumer_id``; find it as the numeric
  part of ``project { aiCatalogItemConsumerForItem(id: "gid://gitlab/Ai::Catalog::Item/1016498") { id } }``
  in https://gitlab.com/-/graphql-explorer while signed in, and store it as the CI variable
  ``AUTOPILOT_F2_CONSUMER_ID``)::

      python scripts/start_flow.py --consumer-id 123 --merge-request 7 --goal "Review !7 against AUTOPILOT.md"

    POST {CI_API_V4_URL}/ai/duo_workflows/workflows
    Authorization: Bearer <token with api scope>  (PRIVATE-TOKEN returns 401 for flow tokens)

Advisory: a failed call never fails the pipeline (the CI gate enforces the policy regardless).
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx


def build_body(
    project: int | str,
    goal: str,
    definition: str | None = None,
    consumer_id: int | None = None,
    merge_request: int | None = None,
    version_id: int | None = None,
) -> dict:
    body: dict = {"project_id": int(project), "goal": goal, "start_workflow": True}
    if consumer_id:
        body["ai_catalog_item_consumer_id"] = int(consumer_id)
        body["environment"] = "ambient"
        if version_id:
            body["ai_catalog_item_version_id"] = int(version_id)
    elif definition:
        body["workflow_definition"] = definition
    else:
        raise ValueError("give --definition (foundational flow) or --consumer-id (AI Catalog flow)")
    if merge_request:
        body["merge_request_id"] = int(merge_request)
    return body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--goal", required=True)
    ap.add_argument("--definition", help="foundational flow id such as code_review/v1")
    ap.add_argument("--consumer-id", type=int, help="AI Catalog item consumer id of an enabled custom flow")
    ap.add_argument("--version-id", type=int, help="optional AI Catalog item version id")
    ap.add_argument("--merge-request", type=int, help="merge request IID the flow is about")
    ap.add_argument("--project", default=os.environ.get("CI_PROJECT_ID"))
    args = ap.parse_args(argv)

    token = os.environ.get("AUTOPILOT_TOKEN", "")
    if not token:
        print("AUTOPILOT_TOKEN not set; flow not started (set the token or rely on UI triggers)")
        return 0
    if not args.definition and not args.consumer_id:
        print("neither --definition nor --consumer-id given; flow not started")
        return 0
    base = os.environ.get("CI_API_V4_URL", "https://gitlab.com/api/v4")
    body = build_body(args.project, args.goal, args.definition, args.consumer_id, args.merge_request, args.version_id)
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

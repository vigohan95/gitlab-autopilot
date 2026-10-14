# Flow triggers (created in **AI > Triggers > New flow trigger**)

Created in the hackathon project https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43190989/showcase (group `gitlab-ai-hackathon/transcend-october-2026/43190989`, Ultimate, Duo Agent Platform enabled).

Triggers have no file format; they are created in the UI. Each row is one trigger.
Service account: the one GitLab creates when the flow is enabled in the project
(`ai-<flow>-<group>`), or a single `ai-autopilot-<group>` account chosen for all.
Triggers are **Premium/Ultimate** (the hackathon onboarding group is Ultimate).

| # | Description | Event (Conditions > On an event) | Run when | Configuration source |
|---|---|---|---|---|
| T1 | Autopilot: plan and implement new issues | **Work item** | Created | AI Catalog → `f1-planner-developer` |
| T2 | Autopilot: policy review of new merge requests | **Merge request** | Created | AI Catalog → `f2-reviewer` |
| T3 | Autopilot: fix security failures | **Pipeline events** | Failed | AI Catalog → `f3-security-fixer` |
| T4 | Autopilot: respond to monitor incidents | **Pipeline events** | Failed | AI Catalog → `f4-incident-responder` |
| T5 | Autopilot: close the loop after production | **Pipeline events** | Passed | AI Catalog → `f5-closer` |

Notes from the docs (docs.gitlab.com/user/duo_agent_platform/triggers/):

- "Every trigger requires a human user to perform the triggering action. Bot users, service
  accounts, and other flows cannot activate a trigger." T1 is fired by the human creating the
  issue. T2 may **not** fire if the MR was created by the Developer flow's service account
  (composite identity attributes the MR to the human, so it may fire) — `[VERIFY-IN-UI]`.
  **Confirmed on MR !1 (2026-10-10): T2 did not fire for the flow-created MR.** Fallback in place: the
  `review-handoff` CI job starts the Code Review flow *and* F2 via the API (`scripts/start_flow.py`; F2 is
  addressed by its AI Catalog consumer id, CI variable `AUTOPILOT_F2_CONSUMER_ID`), the `review-approve`
  job turns F2's `Autopilot review: APPROVED` note into a GitLab approval made by the CI token
  (`scripts/review_approve.py`, honest note), and the `auto-merge` CI job enforces AUTOPILOT §3 regardless.
- T3/T4/T5 fire on pipeline state changes of pipelines that a human caused (merge, schedule
  owner). Scheduled pipelines run as the schedule owner (a human) `[VERIFY-IN-UI]`.
- Only one flow per trigger; T3 and T4 are two triggers on the same event. Each flow triages
  first and ends in seconds when the failure is not its kind (credits are charged per flow run).
- **Configuration path** (`.gitlab/duo/flows/<file>.yaml`) instead of AI Catalog requires the
  `ai_catalog_create_third_party_flows` feature flag (disabled by default on GitLab.com); use the
  AI Catalog and paste the YAML.

## Status (2026-10-10) and the exact API shapes

- Agents: all four created **and enabled** in the hackathon project (AI > Agents; catalog items 1016493–1016496, v1.0.0).
- Flows: `f2-reviewer` created (`aiCatalogFlowCreate`, catalog item 1016498, the server accepted the YAML as is);
  f1/f3/f4/f5 and the *enable* step (`aiCatalogItemConsumerCreate`) and the triggers (`aiFlowTriggerCreate`)
  still have to be done by the project owner in the UI (AI > Flows > New flow → paste YAML → Create flow → Enable;
  AI > Triggers > New flow trigger).
- GraphQL equivalents (for a Maintainer/Owner token): `aiCatalogFlowCreate(name, description, projectId,
  visibility, release, definition)`; `aiCatalogItemConsumerCreate(itemId, target: {projectId})`;
  `aiFlowTriggerCreate(projectPath, userId, description, eventTypes, filter, goals, aiCatalogItemConsumerId)` where
  `eventTypes` is a bitmask of MENTION, ASSIGN, ASSIGN_REVIEWER, PIPELINE_HOOKS, MERGE_REQUEST_READY,
  MERGE_REQUEST_CODE_CONFLICT, MERGE_REQUEST, WORK_ITEM, COMMIT_TO_DEFAULT_BRANCH, SCHEDULED.
- Tool ids: verified against `aiCatalogBuiltInTools` (100 ids). Not available: `gitlab_api_get`, `gitlab_graphql`,
  `search_code` — the flows now use `gitlab_blob_search`, `list_releases`, `list_pipelines`, `get_pipeline`,
  `get_commit`, `list_merge_requests`, `run_glql_query` instead. `tests/test_duo_yaml.py` guards the lists.
- Project Settings > General is not visible to the Developer role; project-level Duo toggles are preset by the
  onboarding group owner.
- Hackathon project: https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43190989/showcase

## Foundational flows (turned on at the top-level group: Settings > GitLab Duo > Allow foundational flows)

| flow | role in Autopilot | how it is invoked |
|---|---|---|
| Developer | alternative to F1's developer step: assign `duo developer` / **Implement** button on the issue | manual or F1 |
| Code Review | code-quality review of every autopilot MR (F2 does the policy review) | automatic reviews on + `review-handoff` job (`code_review/v1`) |
| Fix CI/CD Pipeline | non-security pipeline failures (F3 ends with OTHER) | **Fix pipeline with Duo** button on the failed pipeline |

Also: `.gitlab/duo/mr-review-instructions.yaml` customises Code Review; `AGENTS.md` is read by
Developer and by the custom flows through `context:inputs.user_rule`.

## MCP (read-only `pulse-readonly` server)

- IDE / Duo CLI: `.gitlab/duo/mcp.json` (stdio transport), tools pre-approved because every tool is a GET.
- Flows and agents in the GitLab UI: the agent form's **MCP servers** multiselect only lists GitLab.com's
  curated catalog (Linear, Atlassian, Context7 on 2026-10-10); a self-hosted server cannot be attached, so the
  `pulse-readonly` server is an IDE / Duo CLI capability only (`.gitlab/duo/mcp.json`), not a UI-flow one.

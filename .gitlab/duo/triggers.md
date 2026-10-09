# Flow triggers (created in **AI > Triggers > New flow trigger**)

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
  Fallback already in place: the `review-handoff` CI job starts the Code Review flow via API,
  and the `auto-merge` CI job enforces AUTOPILOT §3 regardless.
- T3/T4/T5 fire on pipeline state changes of pipelines that a human caused (merge, schedule
  owner). Scheduled pipelines run as the schedule owner (a human) `[VERIFY-IN-UI]`.
- Only one flow per trigger; T3 and T4 are two triggers on the same event. Each flow triages
  first and ends in seconds when the failure is not its kind (credits are charged per flow run).
- **Configuration path** (`.gitlab/duo/flows/<file>.yaml`) instead of AI Catalog requires the
  `ai_catalog_create_third_party_flows` feature flag (disabled by default on GitLab.com); use the
  AI Catalog and paste the YAML.

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
- Flows and agents in the GitLab UI: MCP servers come from the **AI Catalog > MCP** tab (HTTP transport
  only, OAuth or none; experiment behind `ai_catalog_mcp_servers`; on GitLab.com the catalog is
  curated by GitLab, "arbitrary URLs are not allowed"). Run the server with
  `python -m mcp_server.pulse_mcp --transport streamable-http --port 8765` on a public host (free
  Hugging Face Space or the user's machine over a tunnel) and ask in `#transcend-hackathon` whether
  hackathon servers can be added `[VERIFY-IN-UI]`. Attach it to `incident-responder` under
  **MCP servers** in the agent editor. Group setting: **Settings > GitLab Duo > Allow external MCP tools**.

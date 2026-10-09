# .gitlab/duo — GitLab Duo Agent Platform configuration

Everything Autopilot needs from the Agent Platform, kept in the repository so
that judges and reviewers can read it next to the pipeline that uses it.

| path | what | created how |
|---|---|---|
| `flows/f1-planner-developer.yaml` … `flows/f5-closer.yaml` | 5 custom flows (flow registry v1, `environment: ambient`) | pasted into **AI > Flows > New flow > Configuration** (or **Configuration path** when the `ai_catalog_create_third_party_flows` flag is on) |
| `agents/*.yaml` | 4 custom agents (display name, description, system prompt, tools) | typed into **AI > Agents > New agent** — agents have no file format, the YAML here is the archive of the form fields |
| `triggers.md` | the 5 flow triggers (event → flow → service account) | **AI > Triggers > New flow trigger** |
| `mcp.json` | MCP client config for the read-only `pulse-readonly` server | read by IDE / Duo CLI; AI Catalog registration for UI flows is in `triggers.md` §MCP |
| `agent-config.yml` | execution environment for flows run as CI jobs (`setup_script`, cache) | read from the default branch automatically |
| `mr-review-instructions.yaml` | custom instructions for the Code Review foundational flow | read automatically |
| `../issue_templates/autopilot.md` | issue template that carries the `autopilot` label | **New issue > Choose a template** |

Official references used (docs.gitlab.com, fetched 2026-10-08):

- Custom flows: `/user/duo_agent_platform/flows/custom/`
- Custom flow YAML schema: `/user/duo_agent_platform/flows/custom_flows_schema/` (ambient only; `coding_environment: full|none`; no `model`, no top-level `name/description/product_group`)
- Flow Registry v1 spec: `gitlab.com/gitlab-org/modelops/applied-ml/code-suggestions/ai-assist/-/blob/main/docs/flow_registry/v1.md`
- Triggers: `/user/duo_agent_platform/triggers/` (events: Mention, Assign, Assign reviewer, Pipeline events, Merge request, Work item; Premium/Ultimate)
- Custom agents: `/user/duo_agent_platform/agents/custom/`; tools: `/user/duo_agent_platform/agents/tools/`
- AGENTS.md: `/user/duo_agent_platform/customize/agents_md/` (custom flows map `context:inputs.user_rule`)
- Flow execution / agent-config.yml: `/user/duo_agent_platform/flows/execution/`
- MCP clients: `/user/gitlab_duo/model_context_protocol/mcp_clients/`; AI Catalog MCP servers: `/user/gitlab_duo/model_context_protocol/ai_catalog_mcp_servers/`
- Code Review flow: `/user/duo_agent_platform/flows/foundational_flows/code_review/`; Developer flow: `/user/project/merge_requests/developer/`; Fix pipeline: `/ci/pipelines/fix_pipeline/`

Items marked `[VERIFY-IN-UI]` in these files could not be confirmed offline and
must be checked in the flow editor (it validates against the live schema).

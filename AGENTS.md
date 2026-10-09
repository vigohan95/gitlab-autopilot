# AGENTS.md — repository context for GitLab Duo flows and agents

Read `AUTOPILOT.md` first: it defines what you may do without a human and
requires every note you write to end with `autopilot: rules=[...]`.

## What this repository is

`pulse` is a small FastAPI service that probes a list of URLs, stores the
results in SQLite and serves a JSON API, an HTML status page and Prometheus
metrics. The interesting part is not the app but the pipeline and the flows
that operate it: see `README.md` (architecture) and `.gitlab/duo/` (flows,
agents, triggers).

## Layout

- `pulse/` — the service: `app.py` (FastAPI routes), `store.py` (SQLite), `probe.py` (HTTP probes), `__main__.py` (uvicorn entry). Version in `pulse/__init__.py`.
- `tests/` — pytest; `conftest.py` routes outbound probes through `httpx.MockTransport`. Never call real networks in tests.
- `scripts/` — CI helpers (policy engine, monitor, incident, deploy, auto-merge, journey card). Plain Python 3.11+, only `httpx`, `pyyaml`, `huggingface_hub`.
- `mcp_server/` — read-only MCP server (`pulse_mcp.py`).
- `.gitlab/duo/` — flow and agent definitions, triggers, MCP and agent config.

## How to work here

- Install: `pip install -r requirements-dev.txt`
- Test: `python -m pytest -q` (must stay green; add tests for every behaviour change)
- Lint/format: `ruff check . && ruff format .` (line length 120, rules E F I B UP S)
- Run locally: `PULSE_DB=:memory: PULSE_INTERVAL=0 python -m pulse` then open http://127.0.0.1:7860/
- Validate CI changes: `python scripts/check_ci_schema.py`

## Conventions

- Python 3.11+, type hints everywhere, `from __future__ import annotations`.
- Keep endpoints read-only unless the issue says otherwise; GET endpoints must stay side-effect free.
- New API fields are additive; never rename or remove JSON keys (the monitor and MCP server depend on them).
- Commit messages: `<area>: <imperative summary>` and reference the issue (`#12`).
- Merge requests: one issue per MR, description must contain `Closes #<iid>`, label `autopilot` is inherited from the issue.
- Do not touch `Dockerfile`, `.gitlab-ci.yml`, `.gitlab/`, `AUTOPILOT.md`, `AGENTS.md`, `LICENSE` in an automated MR (AUTOPILOT §2.1).
- Dependency bumps: patch/minor only, keep `requirements.txt` pinned with `==` (AUTOPILOT §2.2).
- Never print or commit secrets. Configuration comes from `PULSE_*` environment variables.

## When tests fail

Read the failing assertion, fix the code (not the test) unless the issue asked
for a behaviour change, re-run the full suite, and summarise what changed in
the MR description.

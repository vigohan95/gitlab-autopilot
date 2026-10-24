# Contributing

Thanks for looking. Autopilot is small on purpose: a policy file, a dozen CI scripts, a sample service to
operate. Changes that keep it that way are welcome.

## Before you open a pull request

```bash
pip install -r requirements-dev.txt
ruff format . && ruff check .
python -m pytest -q
python scripts/check_ci_schema.py .gitlab-ci.yml
python scripts/check_ci_schema.py templates/Autopilot.gitlab-ci.yml
```

Every behaviour change comes with a test. Tests never call a real network: `tests/conftest.py` routes HTTP
through `httpx.MockTransport`, and the GitLab API is mocked with recorded JSON in `tests/fixtures/`.

## What goes where

- `scripts/` — the CI scripts; installed as the `gitlab_autopilot` package. Each one must keep running two ways:
  as `python scripts/<name>.py` and as the `autopilot-<name>` console command (relative import first,
  top-level import as the fallback; see `scripts/automerge.py` for the pattern).
- `templates/` — what other projects copy or include. Keep them free of anything specific to this repository.
- `AUTOPILOT.md` — the policy of this repository. Rule numbers (§3.7 etc.) are cited by scripts, notes and
  tests; do not renumber them. The YAML block is the source of truth.
- `.gitlab/duo/` — GitLab Duo flows, agents and triggers. They are optional; the CI gate works without them.
- `pulse/`, `mcp_server/` — the sample service. Keep it tiny.
- `docs/DRILL-*.md` — live drill logs. Findings go there with the fix commit, failures included.

## Pull requests

- One change per pull request, with the "why" in the description. Link the issue if there is one.
- Keep `CHANGELOG.md` current under *Unreleased*.
- Changes to `.gitlab-ci.yml`, `AUTOPILOT.md` or anything under `.gitlab/` are protected paths (AUTOPILOT
  §2.1): they are reviewed by a human in every project that runs Autopilot, this one included.
- Bug reports with a reproducible pipeline (a public project and a pipeline URL) get fixed first.

## Conduct

Be direct and kind. Reviews discuss the change, not the person.

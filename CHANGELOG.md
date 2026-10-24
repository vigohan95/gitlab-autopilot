# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-10-28

The first release meant to be installed by someone else.

### Added
- `gitlab-autopilot` on PyPI: the CI scripts as the `gitlab_autopilot` package with console entry points
  (`autopilot-policy`, `autopilot-automerge`, `autopilot-review-approve`, `autopilot-release-notes`,
  `autopilot-journey-card`, `autopilot-receipts`, `autopilot-monitor`, `autopilot-incident`,
  `autopilot-rollback-ref`, `autopilot-deploy`, `autopilot-sci`, `autopilot-start-flow`, `autopilot-fetch-ref`,
  `autopilot-check-ci`, `autopilot-demo`). The modules still run as `python scripts/<name>.py` from a checkout.
- `templates/Autopilot.gitlab-ci.yml`: one `include:` adds the policy gate, auto-merge, journey card, receipts
  page, scheduled monitor, incident + rollback and audit trail to any GitLab project, using only the reserved
  `.pre` / `.post` stages.
- `templates/AUTOPILOT.md`: a starter policy file with a generic `yaml autopilot-policy` block.
- `docs/QUICKSTART.md`: five minutes from an empty project to a gated merge; works on GitLab Free without Duo.
- `AUTOPILOT_POLICY_FILE` (default: `AUTOPILOT.md` in the working directory) so the installed package finds
  the policy of the project it runs in.
- `autopilot-release-notes --version` / `AUTOPILOT_VERSION` / a `VERSION` file as version sources for
  projects that are not the bundled `pulse` service.
- GitHub Actions: tests and lint on every push and pull request; PyPI publishing on `v*` tags (trusted
  publishing).
- `CONTRIBUTING.md`, `SECURITY.md`.

### Changed
- Distribution renamed from `pulse-monitor` to `gitlab-autopilot`; `pulse` and `mcp_server` stay in the
  wheel as the sample service (`pip install "gitlab-autopilot[app]"`). Core dependencies are `httpx` and
  `pyyaml` only.
- The GitHub → GitLab mirror workflow reads its targets from repository variables (`GITLAB_MIRROR_URL`,
  `GITLAB_SHOWCASE_URL`) instead of hard-coded project paths.
- `.gitlab/duo/mcp.json` carries placeholder service URLs instead of the hackathon deployment's.

### Removed
- The one-off `demo:recard` job that re-posted a journey card on the hackathon project's issue #1.

## [0.1.3] - 2026-10-16
- `pulse`: per-site uptime gauges on `/metrics` (the release used for the live rollback drill, run 6).

## [0.1.2] - 2026-10-10
- Rollback pipelines run on `rollback/<tag>/incident-<iid>` with no pipeline variables (drill finding 3).
- `auto-merge` holds on unresolved review threads (AUTOPILOT §3.7, finding 6) and uses a scanner-aware
  baseline (findings 1 and 7); registry-bound jobs retry infrastructure failures (finding 8).

## [0.1.1] - 2026-10-09
- First hands-off journey: issue #2 → MR !1 → release → staging → production → journey card.

## [0.1.0] - 2026-10-09
- Initial pipeline, policy engine, Duo flows and the `pulse` sample service.

[Unreleased]: https://github.com/vigohan95/gitlab-autopilot/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/vigohan95/gitlab-autopilot/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/vigohan95/gitlab-autopilot/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/vigohan95/gitlab-autopilot/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/vigohan95/gitlab-autopilot/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/vigohan95/gitlab-autopilot/releases/tag/v0.1.0

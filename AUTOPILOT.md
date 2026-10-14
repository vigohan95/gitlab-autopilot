# AUTOPILOT.md — autonomy policy (policy as code)

This file is the contract between the humans who own this repository and the
GitLab Duo agents and flows that operate it. Every agent action that changes
state (a merge request, an approval, a merge, a rollback, an incident) must cite
the rule numbers it relied on, in the form `[AUTOPILOT §3]`, so that a reviewer
or a judge can see *why the agent was allowed to act*.

The human-readable rules are in sections 1–9. The machine-readable copy that
`scripts/policy.py` enforces in CI is in the fenced `yaml` block at the end.
If the two ever disagree, the YAML wins and the prose is a bug.

## §1 Scope — what is automated

1.1 Only issues carrying the label `autopilot` are picked up by the Planner /
Developer flow. Any other issue is ignored by every flow and agent. Incidents
(issue type `incident`, opened by the monitor under §6.1) carry the label too,
but belong to the Incident Responder only; the Planner / Developer flow skips
them before doing anything else.

1.2 An issue is "done" when its merge request is merged, the main pipeline is
green through `deploy-production`, and the journey card has been posted on the
issue. Only then may the Closer flow close the issue.

1.3 **Judging freeze.** After `freeze_after` (2026-10-27T13:00Z, the hackathon
submission deadline) Autopilot makes no change at all: a new `autopilot` issue
or merge request receives one polite "frozen for judging" note and is left as
it is — no plan, no branch, no merge, no label change. The `policy-check` job
enforces this on merge requests; the Planner flow checks it before planning an
issue (`python scripts/policy.py --freeze-check`). The freeze is a policy value,
not a code constant: lifting it is an edit to this file, reviewed by a human.

1.4 Agents never touch issues, merge requests or branches outside this project.

## §2 Protected paths — what always needs a human

2.1 Changes to any of the following paths are **never merged automatically**.
The reviewer agent must label the merge request `autopilot::needs-human` and
stop; the `policy-check` CI job fails on the merge request until a human adds
`autopilot::human-approved`:

- `Dockerfile`, `.dockerignore`
- `.gitlab-ci.yml`, anything under `.gitlab/`
- `AUTOPILOT.md`, `AGENTS.md`, `LICENSE`
- `pyproject.toml` build-system section

2.2 Dependency files (`requirements.txt`, `requirements-dev.txt`,
`pyproject.toml` dependencies) may be changed automatically **only** for
patch or minor version bumps. Major bumps, added packages and removed packages
require a human (`autopilot::human-approved`).

## §3 Merge gate — when an agent may merge

3.1 The merge request pipeline is fully green (no failed required jobs).

3.2 The merge request has been approved by the reviewer agent, which must have
posted a review that cites this file. When no reviewer agent is enabled on the
project, the `policy-check` CI job is the reviewer of record: it applies the
same rules and labels the merge request `autopilot::approved` (or
`autopilot::needs-human`).

3.3 Security scanners (SAST, Secret Detection, Dependency Scanning, Container
Scanning, `pip-audit`) report **no new High or Critical** findings on the
merge request. "New" means: present on the merge request pipeline and absent
from the latest green pipeline of the target branch (pre-existing base-image or
dependency findings are tracked on `main`, not re-litigated on every merge
request). Medium and below are logged, not blocking.

3.4 Size limits: at most 400 changed lines and 20 changed files. Larger merge
requests are split or handed to a human.

3.5 The merge request is not a draft and has no merge conflicts.

3.6 Merging is done with "merge when pipeline succeeds", squash on, source
branch deleted. No force-pushes, ever.

## §4 Security remediation

4.1 When a security job fails on `main` or on an `autopilot` merge request, the
Security Fixer may open a fix merge request labelled `autopilot` and
`security-fix` without being asked.

4.2 Fixes are limited to: bumping a vulnerable dependency within §2.2; removing
a hard-coded secret and replacing it with an environment variable; applying a
SAST finding's suggested change when it is local to one function.

4.3 A secret that was committed is treated as compromised: the fix MR must say
so and the journey card must list it, so that a human rotates it.

## §5 Release and promotion

5.1 Every merge to `main` builds an image and a wheel. A release (tag
`vX.Y.Z` + release notes via `release-cli`) is cut when `pulse/__version__`
changes.

5.2 Promotion order is fixed: staging → verify-staging → production. Production
is deployed only from a commit whose staging verification passed in the same
pipeline.

5.3 The Release Scribe writes the release notes from merged merge requests and
their closed issues; it does not invent changes.

## §6 Monitoring and self-healing

6.1 The scheduled monitor probes `/healthz` and `/version` of staging and
production. A failed production probe opens a GitLab **Incident** automatically.

6.2 Rollback is automatic and goes to the **last successful release** (the
newest release tag older than the one currently deployed). The Incident
Responder never rolls *forward*. The rollback pipeline runs on a branch named
`rollback/<tag>/incident-<iid>`, cut from the commit the monitor ran on; the
deploy jobs build the release tag's commit. The target and the incident are in
the branch name, so starting a rollback needs no pipeline variables and no role
above Developer.

6.3 The Incident Responder writes the RCA in the incident, links the rollback
pipeline, and leaves the incident open for a human to close.

## §7 Chaos switch (demo only)

7.1 `PULSE_CHAOS=healthz` makes the service report 503 on `/healthz` (readiness).
The platform health check is `/version` (present in every release, so any release
can be rolled back to, and never affected by the switch); `/livez` is the explicit
liveness endpoint. It exists
so that the self-healing path can be demonstrated and tested. It is flipped on
the *running* production service only (`demo.py chaos on`, i.e. an operator
action, never by a deployment: every deployment removes it), and the first
remediation step of the incident path is to clear it before rolling back.

## §8 Budgets and retries

8.1 At most 2 automatic retries per issue (a retry is a new fix commit after a
red pipeline). On the third failure the merge request is labelled
`autopilot::needs-human` and the flow stops.

8.2 Agents do not start flows that would re-trigger themselves. Every flow checks
the originating actor and exits if it is a service account.

## §9 Secrets and audit

9.1 Agents never print, log, or commit tokens. All credentials live in masked,
protected CI/CD variables (`HF_TOKEN`, `AUTOPILOT_TOKEN`).

9.2 Every agent note ends with a line `autopilot: rules=[...]` listing the rule
numbers relied on. The `audit-trail` CI job and GitLab audit events are the
record of what happened; nothing is deleted.

---

```yaml autopilot-policy
autopilot_policy: 1
scope_label: autopilot
freeze_after: "2026-10-27T13:00:00Z"   # §1.3 — no automatic change after this instant (UTC)
labels:
  approved: "autopilot::approved"
  needs_human: "autopilot::needs-human"
  human_approved: "autopilot::human-approved"
  security_fix: "security-fix"
protected_paths:            # §2.1 — never auto-merged without human_approved
  - "Dockerfile"
  - ".dockerignore"
  - ".gitlab-ci.yml"
  - ".gitlab/**"
  - "AUTOPILOT.md"
  - "AGENTS.md"
  - "LICENSE"
dependency_files:           # §2.2 — only patch/minor bumps auto-merged
  - "requirements.txt"
  - "requirements-dev.txt"
  - "pyproject.toml"
dependency_bumps:
  auto: ["patch", "minor"]
  human: ["major", "add", "remove"]
limits:                     # §3.4, §8.1
  max_changed_lines: 400
  max_changed_files: 20
  max_retries_per_issue: 2
merge_requirements:         # §3
  - pipeline_green
  - reviewer_approved
  - no_new_high_or_critical_findings
  - not_draft
  - no_conflicts
merge_options:              # §3.6
  merge_when_pipeline_succeeds: true
  squash: true
  remove_source_branch: true
promotion: ["staging", "verify-staging", "production"]   # §5.2
rollback:                   # §6.2
  auto: true
  target: last_release
  never_roll_forward: true
chaos_env_var: PULSE_CHAOS  # §7
```

# AUTOPILOT.md — autonomy policy (policy as code)

Copy this file to the root of your project and edit the `yaml autopilot-policy` block at the end. The prose
is for the humans and the agents that read it; the YAML is what `autopilot-policy` and `autopilot-automerge`
enforce in CI. If the two disagree, the YAML wins and the prose is a bug.

Every agent action that changes state (a merge request, an approval, a merge, a rollback, an incident) must
cite the rule numbers it relied on, in the form `[AUTOPILOT §3]`, so that a reviewer can see *why the agent
was allowed to act*.

## §1 Scope — what is automated

1.1 Only issues and merge requests carrying the label `autopilot` are handled. Anything else is ignored.

1.2 An issue is done when its merge request is merged, the default-branch pipeline is green through
`deploy-production`, and the journey card has been posted on the issue. Only then is the issue closed.

1.3 Freeze. When `freeze_after` is set, Autopilot makes no automatic change after that instant: a new
`autopilot` issue or merge request receives one polite "frozen" note and nothing else happens. Lifting the
freeze is an edit to this file, reviewed by a human.

1.4 Agents never touch issues, merge requests or branches outside this project.

## §2 Protected paths — what always needs a human

2.1 Changes to the `protected_paths` below are never merged automatically. The merge request is labelled
`autopilot::needs-human` and waits until a human adds `autopilot::human-approved`.

2.2 Dependency files may be changed automatically only for patch or minor version bumps. Major bumps, added
packages and removed packages need `autopilot::human-approved`.

## §3 Merge gate — when an agent may merge

3.1 The merge request pipeline is fully green.

3.2 The reviewer of record has approved. With a reviewer agent enabled, its newest signed verdict note
(`Autopilot review: APPROVED`, ending with `autopilot: rules=[…]`) decides; without one, `autopilot-policy`
applies the same rules and labels the merge request `autopilot::approved` or `autopilot::needs-human`.

3.3 Security scanners report no new High or Critical findings. "New" means present on the merge request
pipeline and absent from the latest green pipeline of the target branch.

3.4 Size limits: at most `max_changed_lines` changed lines and `max_changed_files` changed files.

3.5 The merge request is not a draft and has no conflicts.

3.6 Merging uses "merge when pipeline succeeds", with the `merge_options` below. No force-pushes.

3.7 No unresolved review threads. Any unresolved, resolvable discussion thread (a human's or a review
agent's) holds the merge; resolving it releases the hold.

## §4 Security remediation

4.1 When a security job fails, a fix merge request labelled `autopilot` and `security-fix` may be opened
without being asked. 4.2 Fixes are limited to dependency bumps within §2.2, replacing a hard-coded secret
with an environment variable, and local SAST suggestions. 4.3 A committed secret is treated as compromised
and listed on the journey card so that a human rotates it.

## §5 Release and promotion

5.1 A release (`vX.Y.Z` tag + notes) is cut when the project version changes (`AUTOPILOT_VERSION`, a
`VERSION` file, or your package's version; see `autopilot-release-notes --help`).

5.2 Promotion order is fixed (`promotion` below). Production is deployed only from a commit whose staging
verification passed in the same pipeline.

## §6 Monitoring and self-healing

6.1 The scheduled monitor probes the targets in `AUTOPILOT_MONITOR_TARGETS`. A failed production probe opens
a GitLab Incident.

6.2 Rollback is automatic and goes to the last successful release (the newest release tag older than the one
deployed), never forward. The rollback pipeline runs on a branch named `rollback/<tag>/incident-<iid>`; the
target and the incident are in the branch name, so no pipeline variables and no role above Developer are
needed. When nothing names the deployed release there is no automatic rollback and the incident says so.

6.3 The incident stays open for a human to close.

## §7 Chaos switch (optional)

7.1 `chaos_env_var`, when set on the running production service, makes its readiness probe fail so the
self-healing path can be exercised. It is cleared as the first remediation step of an incident.

## §8 Budgets and retries

8.1 At most `max_retries_per_issue` automatic retries per issue; after that, a human.

8.2 Registry- and network-bound jobs retry infrastructure failures only, never a red test.

## §9 Transparency

9.1 Every automatic decision is a note on the merge request or issue, citing this file. 9.2 The journey card
reports lead time, human touches after filing, and the CI footprint. 9.3 The audit-trail artifact keeps the
pipeline facts for a year.

```yaml autopilot-policy
autopilot_policy: 1
scope_label: autopilot
freeze_after: null            # §1.3 — e.g. "2026-12-31T23:59:00Z"; null = never frozen
labels:
  approved: "autopilot::approved"
  needs_human: "autopilot::needs-human"
  human_approved: "autopilot::human-approved"
  security_fix: "security-fix"
protected_paths:              # §2.1 — never auto-merged without human_approved
  - "Dockerfile"
  - ".dockerignore"
  - ".gitlab-ci.yml"
  - ".gitlab/**"
  - "AUTOPILOT.md"
  - "AGENTS.md"
  - "LICENSE"
dependency_files:             # §2.2 — only patch/minor bumps auto-merged
  - "requirements.txt"
  - "requirements-dev.txt"
  - "pyproject.toml"
  - "package.json"
dependency_bumps:
  auto: ["patch", "minor"]
  human: ["major", "add", "remove"]
limits:                       # §3.4, §8.1
  max_changed_lines: 400
  max_changed_files: 20
  max_retries_per_issue: 2
merge_requirements:           # §3
  - pipeline_green
  - reviewer_approved
  - no_new_high_or_critical_findings
  - not_draft
  - no_conflicts
  - no_unresolved_review_threads
merge_options:                # §3.6
  merge_when_pipeline_succeeds: true
  squash: true
  remove_source_branch: true
promotion: ["staging", "verify-staging", "production"]   # §5.2
rollback:                     # §6.2
  auto: true
  target: last_release
  never_roll_forward: true
chaos_env_var: APP_CHAOS      # §7 — rename to whatever your service reads, or leave unused
```

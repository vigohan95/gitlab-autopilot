# Quickstart: a policy-gated merge in five minutes

Autopilot adds four things to a GitLab project: a **merge gate** that enforces a written policy
(`AUTOPILOT.md`), **auto-merge** when the policy holds, a **scheduled monitor** that opens an Incident and
starts a **rollback** to the last good release when production is unhealthy, and a **journey card** on the
originating issue with lead time, human touches and CI footprint. All of it runs on GitLab Free with hosted
runners. GitLab Duo flows and agents (the parts that write the code and the RCA) are optional and come later.

## 1. Include the template

Add to your `.gitlab-ci.yml`:

```yaml
include:
  - remote: https://raw.githubusercontent.com/vigohan95/gitlab-autopilot/v0.2.0/templates/Autopilot.gitlab-ci.yml
```

That is the whole CI change. The jobs it adds live in the reserved `.pre` and `.post` stages, so your
`stages:` list stays as it is. (Pin the tag; the template pins the matching `gitlab-autopilot` package.)

## 2. Add the policy

Copy [`templates/AUTOPILOT.md`](../templates/AUTOPILOT.md) to the root of your project. Open the
`yaml autopilot-policy` block at the end and set:

- `protected_paths`: what is never merged without a human (CI config, Dockerfiles, this file, ...);
- `dependency_files`: where patch/minor bumps may merge on their own;
- `limits.max_changed_lines` / `max_changed_files`.

Everything else works as shipped. The prose above the block is the contract your reviewers (human or agent)
cite by rule number.

## 3. Give it a token

Settings > Access tokens > **Add new token**: scope `api`, role **Maintainer**. Then Settings > CI/CD >
Variables > **Add variable**: key `AUTOPILOT_TOKEN`, the token as the value, **Masked**, **not Protected**
(merge request pipelines run on unprotected branches).

Without the token every job still runs and prints what it would do; nothing is written to GitLab.

## 4. Try the gate

Open a merge request, add the label `autopilot` (create it the first time; any colour), and watch the MR
pipeline:

| job | what you see |
|---|---|
| `autopilot:policy-check` | a note on the MR: the rules evaluated, the verdict, and the label `autopilot::approved` or `autopilot::needs-human` |
| `autopilot:auto-merge` | in `.post`, after every other job: merges with "merge when pipeline succeeds", or a note citing the rule that held it (§2.1 protected path, §3.3 new High/Critical finding, §3.7 unresolved review thread, ...) |
| `autopilot:audit-trail` | `audit/pipeline.json`, kept a year |

Touch a protected path (say `.gitlab-ci.yml`) in a second MR to see the hold, then add
`autopilot::human-approved` to release it.

## 5. Monitor, incident, rollback

1. Settings > CI/CD > Variables: `AUTOPILOT_MONITOR_TARGETS` = `production=https://your.app` (space-separated
   `name=url` pairs; add `staging=...` if you have one). The monitor expects `GET /healthz` to answer 200 and,
   if present, `GET /version` to return `{"version": "..."}`.
2. Build > Pipeline schedules > **New schedule**: cron `0 */2 * * *`, target branch `main`, description
   `monitor`.
3. Name your production deployment job `deploy-production` and make it deploy `ROLLBACK_TO` when that
   variable is set. The one-liner that reads it from the branch name:

   ```yaml
   deploy-production:
     stage: deploy
     before_script:
       - pip install --quiet "$AUTOPILOT_PACKAGE"        # defined by the template
       - eval "$(autopilot-rollback-ref)"          # on rollback/<tag>/incident-<iid>: exports ROLLBACK_TO, INCIDENT_IID
     script:
       - ./deploy.sh "${ROLLBACK_TO:-$CI_COMMIT_SHA}"
     rules:
       - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
       - if: $CI_COMMIT_BRANCH =~ /^rollback\/v[0-9]/
   ```

   Releases are GitLab releases with tags `vX.Y.Z`; `autopilot:version` writes `RELEASE_NOTES.md` and
   `version.env` (`VERSION`, `RELEASE_TAG`, `RELEASE_NEEDED`) for a `release-cli` job of yours. The version
   comes from `AUTOPILOT_VERSION`, a `VERSION` file, or `--version`.

When the scheduled monitor fails on production, `autopilot:incident` opens a GitLab Incident with the probe
report, resolves the deployed release and the previous one, creates the branch `rollback/<tag>/incident-<iid>`
and starts a pipeline on it. Your `deploy-production` runs with `ROLLBACK_TO` set. No pipeline variables, no
role above Developer. The live drill in this repository measured 2 min 8 s from monitor failure to production
rolled back ([docs/DRILL-2026-10-14.md](DRILL-2026-10-14.md), run 6).

## 6. Journey cards and the receipts page

After a default-branch pipeline whose MR body says `Closes #<issue>`, `autopilot:journey-card` posts on the
issue: issue → merge → production lead time, human actions after filing, the CI footprint estimate, and which
lifecycle stages ran, each number linking to its job. `autopilot:receipts` publishes the same for every
journey as a GitLab Pages table (Deploy > Pages for the URL).

## Optional: GitLab Duo Agent Platform

With Duo (Premium/Ultimate or the free trial), `.gitlab/duo/` holds five flows (planner + developer, reviewer,
security fixer, incident responder, closer), four agents and five triggers that make the loop fully
hands-off: an issue in, a merge request, a review verdict citing `AUTOPILOT.md`, a fix for a red scanner, an
RCA on the incident. The README's *Reproduce in your own group* section lists the clicks. The CI gate does not
depend on any of it: a verdict note from the reviewer agent is honoured when present (§3.2), and
`autopilot:policy-check` is the reviewer of record when it is not.

## Running the scripts yourself

```bash
pip install https://github.com/vigohan95/gitlab-autopilot/releases/download/v0.2.0/gitlab_autopilot-0.2.0-py3-none-any.whl
# or, once it is on PyPI: pip install gitlab-autopilot
autopilot-policy --files src/app.py --labels autopilot --lines 12 --json   # offline check against ./AUTOPILOT.md; exit 0 = allowed, 2 = held
AUTOPILOT_TOKEN=... CI_PROJECT_ID=123 autopilot-policy --mr 7   # live, no note unless --comment
autopilot-monitor --target production=https://your.app --retries 2 --backoff 5
```

Every command has `--help`. The same modules run from a checkout as `python scripts/<name>.py`.

# RUNBOOK — M1 (Security Fixer for real) and M2 (Incident Responder for real) in the showcase project

Project: https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43190989/showcase (id 87424108, Ultimate group;
flows, triggers and pipelines run here). Everything below is clicks on GitLab.com by a **human member** of the project —
Duo triggers only fire for actions a human performed, which is exactly why these two scenarios need a person to open the
MR and press "Run" on the schedule. Nothing costs money; no secret is typed anywhere.

Time: M1 ≈ 25 min mostly waiting (two MR pipelines of ~6 min each + the F3 session), M2 ≈ 10 min.
Do M1 first; M2 reuses nothing from it.

Why these exact triggers fire: T3 and T4 are both "Pipeline events → Failed" triggers (`.gitlab/duo/triggers.md`). A
merge-request pipeline started by a human-opened MR, or a scheduled pipeline started with the schedule's **Run** button
(the pipeline runs as the schedule owner), is a human-caused pipeline. Both flows triage first: F3 continues only for
security-job failures, F4 only for a failed `monitor` job on a scheduled pipeline; the other one ends in seconds.

---

## Before you start (2 min) — Settings > CI/CD

URL: https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43190989/showcase/-/settings/ci_cd
(left sidebar **Settings > CI/CD**; needs Maintainer/Owner, the page is 404 for everyone else).

1. **Variables** section → expand. Confirm `AUTOPILOT_TOKEN` exists (masked, **not** protected). It is what `policy-check`,
   `review-approve`, `auto-merge`, `incident` and the journey card write with. If it is missing, nothing in this runbook
   can post notes or merge — stop and say so.
2. ~~Minimum role to use pipeline variables → Developer~~ — **no longer needed.** The 2026-10-14 drill showed the
   rollback trigger refused with 400 "Insufficient permissions to set pipeline variables" (and a Developer cannot change
   that setting). Since then `incident.py` starts the rollback on the branch `rollback/<tag>/incident-<iid>` with no
   variables (`scripts/rollback_ref.py`, AUTOPILOT §6.2; `docs/DRILL-2026-10-14.md`, finding 3).
3. Optional but recommended (M3): add variable `AUTOPILOT_F2_CONSUMER_ID` = the numeric id of the `f2-reviewer`
   flow's consumer in this project. Find it signed in at https://gitlab.com/-/graphql-explorer with

   ```graphql
   { project(fullPath: "gitlab-ai-hackathon/transcend-october-2026/43190989/showcase") {
       aiCatalogItemConsumerForItem(id: "gid://gitlab/Ai::Catalog::Item/1016498") { id enabled } } }
   ```

   and take the number at the end of `gid://gitlab/Ai::Catalog::ItemConsumer/<number>`. With it set, every
   `autopilot` MR pipeline starts F2 from CI, and F2's newest signed verdict note decides §3.2 in `auto-merge`.
   (`review-approve` also tries to record an MR approval; in the showcase that call answers 401, see AUTOPILOT §3.2.)
   Flags: not protected, not masked (it is not a secret).

---

## M1 — a human opens a vulnerable-dependency MR; F3 fixes it; the gate merges the fix (≈ 25 min)

The change: `requirements.txt` line 1 `fastapi==0.143.0` → `fastapi==0.125.0`. That version pulls `starlette 0.50.0`,
which `pip-audit` flags with **PYSEC-2026-248, PYSEC-2026-249** (plus PYSEC-2026-161, -2280, -2281) — reproduced locally on
2026-10-13 with `pip-audit -r` against a temp file holding exactly that pin (fix versions 1.3.0 / 1.3.1 / 1.0.1 / 1.1.0).
The pin is a **minor** change in policy terms (0.143 → 0.125, §2.2), so `policy-check` passes; only `pip-audit` blocks.

### M1.1 Open the MR from the single-file web editor (3 min)

1. Open https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43190989/showcase/-/edit/main/requirements.txt
   (this is **Repository > Files > `requirements.txt` > Edit > Edit single file**).
2. Change line 1 to exactly:

   ```
   fastapi==0.125.0
   ```

   Leave the other three lines untouched.
3. Below the editor:
   - **Commit message:** `deps: pin fastapi==0.125.0 (security scenario: known-vulnerable starlette)`
   - **Target Branch:** type `security/pin-fastapi-demo` (the editor offers "Create a new branch" — pick it).
   - Tick **Start a new merge request**.
   - Click **Commit changes**.
4. The **New merge request** form opens:
   - Title: keep (or `deps: pin fastapi==0.125.0 (security scenario)`). Make sure it is **not** marked Draft.
   - Description, paste:

     ```
     Security scenario (M1): this MR deliberately pins `fastapi==0.125.0`, which resolves starlette 0.50.0
     (PYSEC-2026-248/249). `pip-audit` must go red and the merge gate must hold (AUTOPILOT §3.3); the
     Security Fixer flow (T3: pipeline failed → f3-security-fixer) is expected to open a minimal fix MR against
     this branch within §4.1/§4.2 and §2.2, which the same gate then merges.

     autopilot: rules=[§3.3, §4.1, §4.2]
     ```

   - **Labels:** add `autopilot` (required: `policy-check`, `review-approve` and `auto-merge` only run on `autopilot` MRs).
   - Leave assignee/reviewer empty; keep "Delete source branch" and "Squash commits" ticked.
   - Click **Create merge request**. Note the MR URL (`…/-/merge_requests/<iid>`).

### M1.2 What should happen now, and what to collect

| when | what you see | collect |
|---|---|---|
| +0 min | MR pipeline starts (it is yours, a human's). Jobs: `version`, `test`, `lint`, `review-handoff`, `semgrep-sast`, `secret_detection`, `gemnasium-python-dependency_scanning`, `pip-audit`, `build-image`, `container_scanning`, `build-wheel`, `policy-check`, `review-approve`, `audit-trail`. | pipeline URL |
| +2 min | **`pip-audit` red** — its log lists starlette 0.50.0 with PYSEC-2026-248 and -249 (and 161/2280/2281). `auto-merge` is skipped (it `needs` `pip-audit`), so the MR cannot merge. `policy-check` posts "auto-merge allowed … §2.2" — correct: the pin is a minor bump, the *scanner* is what blocks. | red `pip-audit` job URL, policy-check note |
| +6 min | Pipeline status **failed** → trigger T3 starts **f3-security-fixer** and T4 starts **f4-incident-responder**. F4's triage answers OTHER and ends. F3's triage answers SECURITY and the fixer step runs (reads the `pip-audit` log, decides "bump the pin within patch/minor"). | **AI > Sessions** (left sidebar; `…/-/automate/sessions` in newer UIs) → the two session ids |
| +8–15 min | F3 creates branch `autopilot/security-<pipeline id>` **from your branch**, restores a safe pin (`fastapi==0.143.0` or newer 0.x), runs tests/ruff, opens a **fix MR targeting `security/pin-fastapi-demo`** with labels `autopilot` + `security-fix` and a note with the scanner output, ending `autopilot: rules=[section 4.1, section 4.2, section 2.2]`. | fix MR URL, its note |
| +15–21 min | The fix MR's pipeline is green → `policy-check` approves → `review-approve` (if F2 ran) → `auto-merge` merges it into your branch. | auto-merge note on the fix MR |
| +21–27 min | Your MR's pipeline re-runs on the merged branch: `pip-audit` green → `auto-merge` merges **your** MR into `main` → main pipeline (release job says "tag exists", dry-run deploys, journey card as artifact — there is no `Closes #n`, so no issue note). | your MR's auto-merge note, the merge commit, main pipeline URL |

Send Claude: your MR URL, the red `pip-audit` job URL, both session ids, the fix MR URL, the final main pipeline URL.
They go into README row 0b and the Devpost text.

### M1.3 If something does not happen

- **No F3 session after 3 min of "failed":** AI > Triggers — T3 must be enabled and point at `f3-security-fixer`;
  the MR pipeline must be *yours* (it is, if you clicked Commit). Tell Claude; do **not** fix the pin by hand — an honest
  "F3 did not fire" beats a staged fix.
- **F3 opened the fix MR against `main` instead of your branch:** acceptable; let it merge, then close your MR with the
  comment "superseded by !<fix>". Still evidence.
- **F3 chose a major bump (fastapi 1.x):** policy says human (§2.2); the fix MR gets `autopilot::needs-human`. Add the
  label `autopilot::human-approved` to it only if you agree with the bump — that one click is recorded as the human
  touch it is.
- **Pipeline minutes:** two MR pipelines + one main pipeline ≈ 25 runner-minutes on the Ultimate group's quota.

---

## M2 — a zero-cost production outage; the monitor opens an Incident; F4 writes the RCA (≈ 10 min)

Production in the showcase is a dry-run deployment (no Render key), so we point the monitor at a URL that always answers
503. `https://httpbin.org/status/503` returns 503 (checked 2026-10-13). The monitor appends `/healthz` and `/version` to
the base URL, and `https://httpbin.org/status/503/healthz` is a 404 (also a failure, but a confusing one), so use the
query-string form below: `…/status/503?probe=` + `/healthz` → `…/status/503?probe=/healthz` → **503** for both probes
(verified 2026-10-13).

### M2.1 Set the production URL (1 min)

1. https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43190989/showcase/-/settings/ci_cd → **Variables** → **Add variable**
   (if `PULSE_PRODUCTION_URL` already exists, **Edit** it and write down the old value first).
   - Key: `PULSE_PRODUCTION_URL`
   - Value: `https://httpbin.org/status/503?probe=`
   - Environment scope: All (default) · Protect: **off** · Mask: **off** (the value cannot be masked anyway) · Expand: on.
   - Save.

### M2.2 Run the monitor schedule as yourself (1 min)

1. https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43190989/showcase/-/pipeline_schedules (**Build > Pipeline schedules**).
2. If there is **no** schedule described `monitor`: **New schedule** → Description `monitor` · Interval pattern: Custom
   `0 */2 * * *` · Cron timezone UTC · Target branch `main` · Activated ✔ → **Create pipeline schedule**. (Creating it
   makes you its owner; scheduled pipelines then run as you, which is what lets T3/T4 fire.)
   If it exists but is owned by someone else, use **Take ownership** first.
3. Press the **▶ Run pipeline schedule** button on the `monitor` row. Note the new pipeline (source: schedule) in
   https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43190989/showcase/-/pipelines.

### M2.3 What should happen, and what to collect

| when | what you see | collect |
|---|---|---|
| +0 | Scheduled pipeline with jobs `monitor` and `incident` only. No scanner runs on a schedule (`.gitlab-ci.yml` workflow rules; `tests/test_ci_rules.py`). On 2026-10-14 `gemnasium-python-dependency_scanning` still ran here too (drill finding 4, fixed since). | pipeline URL |
| +2 min | **`monitor` red** after 5 attempts × 20 s: the log and `monitor-report.json` show `production` `/healthz→503`, `/version→503`. | `monitor` job URL |
| +3 min | **`incident` job** runs (`when: on_failure`): opens **Monitor > Incidents** → "Production unhealthy: production (…)" with the report, labels `autopilot`, `incident`, `env::production`, an *Automatic remediation* line (see "The rollback step" below) and `autopilot: rules=[§6.1, §6.2, §6.3]`. It also tries to clear the chaos switch (nothing to clear — no Render key). **Expected in the showcase since release v0.1.3 (2026-10-16, drill run 6):** the `incident` log prints "rollback target: v0.1.2 (`/version` did not answer; production's last successful deployment (#…, `main` at `<sha>`, the commit of release `v0.1.3`) is `v0.1.3`; `v0.1.2` is the newest release older than that)", the incident description has the same "Rollback target" line, and a note follows: "Rollback to `v0.1.2` started on `rollback/v0.1.2/incident-<iid>`: <pipeline URL>" (see "The rollback step", step 3). Before v0.1.3 existed, production ran on a plain `main` commit and the expected text was "**No automatic rollback:** … is not a release. A human must roll back." https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43190989/showcase/-/incidents | incident URL, `incident` job log (it prints `rollback target: …`) |
| +3 min | Pipeline **failed** → T4 starts **f4-incident-responder** (triage MONITOR → responder) and T3 starts **f3-security-fixer** (triage MONITOR → ends). | AI > Sessions → both ids |
| +5–9 min | F4 posts the **RCA note** on the incident with headings Impact / Timeline (UTC) / Root cause / Remediation (automatic) / Follow-ups for humans / Evidence, ending `autopilot: rules=[section 6.1, section 6.2, section 6.3]`, and adds the label `rca::done`. Expected root cause: the production endpoint answers 503 on both probes (not the chaos switch — the note should say "503 from httpbin", i.e. the configured URL is down/misconfigured). The incident **stays open** (§6.3). | RCA note URL |

#### The rollback step (AUTOPILOT §6.2), as `scripts/incident.py` does it now

1. **Which release is deployed.** What production's `/version` reports, if that is a release. Otherwise the release of
   production's **last successful deployment** (Deployments API, environment `production`, newest finished): a
   `rollback/<tag>/…` ref gives `<tag>`, a tag ref gives that tag, a commit that is exactly a release's commit gives
   that release. Anything else (a later push to `main`) names no release, so there is no guess.
2. **Target** = the newest release older than the deployed one. If the deployed release is unknown or is the oldest
   release, there is **no target**. The incident description says "**No automatic rollback:** <reason>. A human must
   roll back." and the same reason is posted as a note. No pipeline starts.
3. **With a target**, the job creates the branch `rollback/<tag>/incident-<iid>` from the commit the monitor ran on,
   then starts a pipeline on it with `CI_JOB_TOKEN` and **no pipeline variables**. The incident gets the note
   "Rollback to `<tag>` started on `rollback/<tag>/incident-<iid>`: <pipeline URL>". That pipeline runs
   `deploy-staging` → `verify-staging` → `deploy-production` → `rollback-note` + `audit-trail` (no scanners, no
   build). The deploy jobs build the tag's commit (`scripts/rollback_ref.py`, `scripts/deploy.py`), and in the
   showcase they are dry runs. `rollback-note` posts "Rollback to `<tag>` deployed to staging and production" on the
   incident. If the branch or the trigger is refused, the incident gets "Automatic rollback could NOT be started: …
   A human must roll back."
4. **A human rollback** (when the incident says so, and you agree a rollback would help): pick the release. Go to
   **Code > Branches > New branch**, name it `rollback/<tag>/incident-<iid>` (for example `rollback/v0.1.1/incident-4`),
   and create it from `main`. Creating it starts no pipeline, because the workflow rules skip the push. Then go to
   **Build > Pipelines > Run pipeline**, choose that branch, add no variables, and click **Run pipeline**.

**What happened on 2026-10-14** (`docs/DRILL-2026-10-14.md`, findings 3 and 5). The rule then was "if `/version` does
not answer, take the newest release", so Incident #3 named `v0.1.2` as the target. That is the showcase's only
release, and production was already built on it. The job then started the rollback the old way, as a pipeline on
`main` with the variable `ROLLBACK_TO=v0.1.2`. GitLab refused it with 400 "Insufficient permissions to set pipeline
variables", and the incident got "Automatic rollback could NOT be started … A human must roll back." F4's RCA added
that a rollback to `v0.1.2` would not have changed anything, because the probe URL was the fault. Both are fixed: the
rollback runs on the branch with no variables, and the target is never the release already deployed. Replayed
against the drill, the job now picks no target and starts no pipeline (`tests/test_rollback.py`). Until 2026-10-16 the branch path
had not run in a live incident: the showcase had one release and production was deployed from a later `main` commit, so no
target existed. Release v0.1.3 (drill run 6) put production on a release commit with `v0.1.2` below it; the first live
run of the branch path is the next M2 drill.

### M2.4 Restore (1 min)

1. Settings > CI/CD > Variables → `PULSE_PRODUCTION_URL`: **delete** it (or restore the old value you wrote down).
2. Pipeline schedules → ▶ Run once more: with no targets the `monitor` job is green ("no targets configured"), which is
   the honest state of a dry-run production. Leave the schedule **active** (every 2 h, about 6.4 runner-minutes a day,
   `docs/SCI.md`) — it is the Monitor-stage evidence.
3. Leave the incident open with `rca::done` (policy §6.3: a human closes incidents). If it must be closed for tidiness,
   do it **after** judging.

Send Claude: the scheduled pipeline URL, the incident URL, the F4 session id, the RCA note URL.

### M2.5 If something does not happen

- `incident` job says "AUTOPILOT_TOKEN not set": see *Before you start*.
- No F4 session: AI > Triggers — T4 enabled, event *Pipeline events → Failed*, flow `f4-incident-responder`; the
  schedule owner must be you (Take ownership, run again).
- The monitor is green: the variable did not reach the job (protected flag on? wrong scope?) — fix and run again.
- The incident says "No automatic rollback": since release v0.1.3 this is a finding, not the expected state (production
  was deployed from the v0.1.3 commit by its `main` pipeline, so the target must be `v0.1.2`). Read the reason it gives:
  "deployments could not be read" means the Deployments API refused `AUTOPILOT_TOKEN`; "is not a release" means a later
  push to `main` redeployed production after v0.1.3 (push nothing to `main` between the release and the drill). Then
  roll back by hand (step 4) and record the finding.

---

## After both: what Claude does with the links

Row 0b (security loop) and row 0c (incident loop) in the README judges' table, the "Executed for real" paragraph, the
Devpost About text (`DEVPOST-ABOUT-v3.md`), and `.gitlab/duo/triggers.md` status. No video is re-recorded.

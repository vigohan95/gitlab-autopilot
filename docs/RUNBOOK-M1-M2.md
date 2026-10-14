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
   `autopilot` MR pipeline starts F2 from CI and `review-approve` turns F2's APPROVED verdict into a real approval.
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
| +0 | Scheduled pipeline with jobs `monitor` and `incident` only (`semgrep-sast`/`audit-trail` no longer run on schedules). | pipeline URL |
| +2 min | **`monitor` red** after 5 attempts × 20 s: the log and `monitor-report.json` show `production` `/healthz→503`, `/version→503`. | `monitor` job URL |
| +3 min | **`incident` job** runs (`when: on_failure`): opens **Monitor > Incidents** → "Production unhealthy (…)" with the report, labels `autopilot`, `incident`, `env::production`, the line "No earlier release exists; rollback not possible." (the showcase has only `v0.1.2`) and `autopilot: rules=[§6.1, §6.2, §6.3]`. It also tries to clear the chaos switch (nothing to clear — no Render key). https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43190989/showcase/-/incidents | incident URL |
| +3 min | Pipeline **failed** → T4 starts **f4-incident-responder** (triage MONITOR → responder) and T3 starts **f3-security-fixer** (triage MONITOR → ends). | AI > Sessions → both ids |
| +5–9 min | F4 posts the **RCA note** on the incident with headings Impact / Timeline (UTC) / Root cause / Remediation (automatic) / Follow-ups for humans / Evidence, ending `autopilot: rules=[section 6.1, section 6.2, section 6.3]`, and adds the label `rca::done`. Expected root cause: the production endpoint answers 503 on both probes (not the chaos switch — the note should say "503 from httpbin", i.e. the configured URL is down/misconfigured). The incident **stays open** (§6.3). | RCA note URL |

### M2.4 Restore (1 min)

1. Settings > CI/CD > Variables → `PULSE_PRODUCTION_URL`: **delete** it (or restore the old value you wrote down).
2. Pipeline schedules → ▶ Run once more: with no targets the `monitor` job is green ("no targets configured"), which is
   the honest state of a dry-run production. Leave the schedule **active** (every 2 h, about 6 runner-minutes a day,
   `docs/SCI.md`) — it is the Monitor-stage evidence.
3. Leave the incident open with `rca::done` (policy §6.3: a human closes incidents). If it must be closed for tidiness,
   do it **after** judging.

Send Claude: the scheduled pipeline URL, the incident URL, the F4 session id, the RCA note URL.

### M2.5 If something does not happen

- `incident` job says "AUTOPILOT_TOKEN not set": see *Before you start*.
- No F4 session: AI > Triggers — T4 enabled, event *Pipeline events → Failed*, flow `f4-incident-responder`; the
  schedule owner must be you (Take ownership, run again).
- The monitor is green: the variable did not reach the job (protected flag on? wrong scope?) — fix and run again.

---

## After both: what Claude does with the links

Row 0b (security loop) and row 0c (incident loop) in the README judges' table, the "Executed for real" paragraph, the
Devpost About text (`DEVPOST-ABOUT-v3.md`), and `.gitlab/duo/triggers.md` status. No video is re-recorded.

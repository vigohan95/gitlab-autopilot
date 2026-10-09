# DEMO.md — runbook for the three scenes (works today, with or without Duo)

All scenes are driven by `scripts/demo.py` through the GitLab API. They need
one secret, `AUTOPILOT_TOKEN` (project access token, scope `api`, role
Maintainer), either exported locally or stored as a masked (not protected) CI/CD variable —
then every scene is also a **manual job on the main pipeline** (`demo:*`,
play button, no local secrets needed).

```bash
export AUTOPILOT_TOKEN=glpat-...            # local use only; in CI it is the masked variable
python scripts/demo.py --project <id> setup  # labels + "monitor" schedule + variable check
```

Timings below are wall-clock on GitLab.com hosted runners (first pipeline ran
in ~9 minutes end to end; MR pipelines ~6 min because deploy stages are skipped).

## Scene A — the hands-off merge (issue → MR → policy → merge → release)  ~12 min

| step | action | what to watch | ~time |
|---|---|---|---|
| A1 | `demo.py mr` (or play `demo:mr`) | creates issue `#n` (label `autopilot`), branch `demo/release-0.1.x-…`, 1-line commit bumping `pulse.__version__`, MR `!m` with `Closes #n` | 10 s |
| A2 | MR pipeline starts | test · lint · sast · secret_detection · pip-audit · build-image · build-wheel · **policy-check** · **auto-merge** · audit-trail | 6 min |
| A3 | `policy-check` job log + MR note | "Autopilot policy check: auto-merge allowed", rules §1.1 §2.1 §2.2 §3.4 §3.5 satisfied, label `autopilot::approved` added | — |
| A4 | `auto-merge` job log + MR note | "Merging when this pipeline succeeds", rule list incl. §3.2 §3.3 | — |
| A5 | MR merges (squash, branch deleted) → main pipeline | version → … → **release** (`v0.1.x` under Deploy > Releases) → deploy-staging → verify-staging → deploy-production → **journey-card** | 6 min |
| A6 | issue `#n` | journey card note (stages table, 9/9 when Duo adds plan/create) and the issue is **closed** | — |

With Duo enabled, A1 is replaced by just creating the issue (F1 does the branch+MR) and the F2 reviewer note appears before A3.

## Scene B — the security gate (vulnerable dependency → blocked → fixed)  ~14 min

| step | action | what to watch | ~time |
|---|---|---|---|
| B1 | `demo.py security` (or `demo:security`) | branch `demo/security-…` pins `fastapi==0.125.0`; MR with label `autopilot` | 10 s |
| B2 | MR pipeline | **pip-audit red** (starlette 0.50.x: PYSEC-2026-2280/2281, 248/249), `auto-merge` not run (needs pip-audit) → MR cannot merge | 6 min |
| B3 | `demo.py security --fix` (or `demo:security-fix`) | commit restores the current pin on the same branch, label `security-fix`, note citing §4.1 §4.2 §2.2 | 10 s |
| B4 | MR pipeline re-runs | all green → policy-check → auto-merge → merged | 6 min |

With Duo, B3 is done by the Security Fixer flow (F3) on the "Pipeline: Failed" trigger.

## Scene C — self-healing production (chaos → incident → rollback → RCA)  ~5 min

Needs a deployed production (Hugging Face Spaces, see SUBMIT.md §C4). Before
the Spaces exist, use `--fake-url` so the monitor fails on an unreachable host.

| step | action | what to watch | ~time |
|---|---|---|---|
| C1 | `demo.py chaos on` (or `demo:chaos-on`; add `--fake-url` without HF) | variable `PULSE_CHAOS=healthz` on environment `production`; schedule `monitor` played | 5 s |
| C2 | scheduled pipeline | **monitor red** (`/healthz` 503 / unreachable, 3 attempts) → **incident** job: Monitor > Incidents gets `Production unhealthy …` with the report, rollback target, rules §6.1 §6.2 §6.3 | 2 min |
| C3 | rollback pipeline (source: trigger, `ROLLBACK_TO=v0.1.x`) | deploy-staging → verify-staging → deploy-production from the previous release; `rollback-note` on the incident | 3 min |
| C4 | `demo.py chaos off` (or `demo:chaos-off`) | variable removed; next monitor run green | 5 s |

Note: with `PULSE_CHAOS` set, the *rolled-back* production also reports 503
(the switch is an environment variable, not code) — that is why the Incident
Responder's first remediation step is to clear it (AUTOPILOT §7.1). For the
video, run C4 immediately after C3 starts so the rollback deploy comes up healthy.

With Duo, the Incident Responder flow (F4) adds the RCA note after C2.

## Recording order for the 2:40 video

1. Scene A first (A1→A6) — record A1, A3, A4, A5 release page, A6.
2. Scene B (B1→B4) — record B2 red pip-audit, B3 fix MR note, B4 green.
3. Scene C (C1→C4) — record C2 incident, C3 rollback pipeline.
4. Static shots: README architecture, `.gitlab/duo/flows/`, AI > Flows / Sessions (if Duo), Secure > Audit events.

Total runtime for a clean take: about 35 minutes of pipelines; record while they run.

## Cleanup

`demo.py cleanup` closes open demo MRs, deletes `demo/*` branches and removes `PULSE_CHAOS`.

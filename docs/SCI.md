# SCI.md — the CI footprint line on every journey card (an estimate, and how it is computed)

Every Autopilot journey card ends with a line like

> **CI footprint (estimate):** 9.02 runner-minutes ≈ 0.0013 kWh ≈ **0.77 gCO2e** per journey (0.53 g with Google's CFE% adjustment; GCP us-east1, CCF coefficients — `docs/SCI.md`)

and the `journey-card` job of every main pipeline writes the same numbers to the `sci.json` artifact
(`python scripts/sci.py --pipeline $CI_PIPELINE_ID`). This page says exactly what the numbers are, where
every coefficient comes from, and what they are **not**.

**This is an estimate.** GitLab does not expose the power draw of hosted runners, so the figure is a model,
not a meter reading. We use the Cloud Carbon Footprint (CCF) methodology because it is public, documented
and the same one most other entries that report carbon use, which makes the numbers comparable.

## 1. Functional unit and formula

The Green Software Foundation's Software Carbon Intensity specification defines
`SCI = ((E × I) + M) per R` — operational energy `E` times grid intensity `I`, plus embodied emissions `M`,
per functional unit `R` ([sci.greensoftware.foundation](https://sci.greensoftware.foundation/)).

- **R** = one Autopilot journey: one `autopilot` issue taken to production by one `main` pipeline. The MR pipeline
  that preceded it is not included (it is a different pipeline id); add it by running `scripts/sci.py --pipeline` on it.
- **E** (kWh) = Σ job duration (h) × runner power (W) / 1000 × PUE, with the CCF compute + memory model:
  `runner power = vCPUs × (min W + 50 % × (max W − min W)) + memory GB × memory W/GB`.
- **I** (gCO2e/kWh) = the CCF grid factor of the region GitLab-hosted runners run in.
- **M** = 0 — embodied emissions of shared hosted runners are not estimated (we do not know the hardware share);
  `sci.json` carries `embodied_g_co2e: 0` so that a reader can add a figure.

## 2. Coefficients and their sources (as published on 2026-10-13)

| coefficient | value | source |
|---|---|---|
| runner machine, vCPU, memory | `saas-linux-small-amd64`, 2 vCPU, 8 GB — "Untagged jobs run on the `small` Linux x86-64 runner" | [GitLab-hosted runners on Linux](https://docs.gitlab.com/ci/runners/hosted_runners/linux/) |
| cloud provider and region | Google Compute Engine, "The default region is `us-east1`" | same page |
| min / max watts per vCPU (GCP) | 0.71 W / 4.26 W ("Median Min Watts" / "Median Max Watts") | [CCF methodology](https://www.cloudcarbonfootprint.org/docs/methodology/), *Compute* |
| average CPU utilisation | 50 % ("Average CPU Utilization for hyperscale data centers: 50%") | same |
| memory coefficient | 0.392 W per GB (0.000392 kWh per GB-hour) | same, *Memory* |
| PUE (GCP) | 1.1 | same, *Power Usage Effectiveness* |
| grid intensity, GCP `us-east1` (South Carolina) | **0.000576 tCO2e/kWh = 576 g/kWh** (CCF table *without* the carbon-free-energy adjustment — the conservative figure we headline) and 0.0003974 t/kWh = 397.4 g/kWh *with* Google's CFE % | same, *Grid emissions factors → GCP*; CCF cites [Google's region carbon data](https://cloud.google.com/sustainability/region-carbon) |

So one runner VM is modelled at `2 × (0.71 + 0.5 × 3.55) + 8 × 0.392 = 8.106 W`; one runner-hour is
`8.106 Wh × 1.1 = 8.917 Wh = 0.00892 kWh ≈ 5.14 gCO2e` (3.54 g with the CFE adjustment).
`tests/test_sci_and_journey.py` pins this arithmetic.

"Runner-minutes" are the sum of the job `duration` fields the GitLab API reports (queue time excluded, retried
jobs included). GitLab's quota accounting applies a cost factor on top of that; we report the raw durations.

## 3. Worked examples (real pipelines, computed by `scripts/sci.py` on 2026-10-13)

| pipeline | what | jobs | runner-minutes | kWh | gCO2e (576) | gCO2e (CFE 397) |
|---|---|---|---|---|---|---|
| [2933589447](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43190989/showcase/-/pipelines/2933589447) | the 2026-10-10 journey of issue #2 (merge → release → staging → production → journey card) | 24 (8 manual demo jobs, 0 s) | 9.02 | 0.00134 | 0.77 | 0.53 |
| [2941380630](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43190989/showcase/-/pipelines/2941380630) | the 2026-10-13 main pipeline (README change) | 24 | 10.15 | 0.00151 | 0.87 | 0.60 |

For scale: a journey costs about as much CO2e as keeping a 10 W LED bulb on for 15 minutes. The point of the
line is not the absolute number but that it is **on every card, computed the same way, with the deploy stages
included** — and that the next section's waste was visible only once we measured it.

## 4. The monitor schedule: from every 15 minutes to every 2 hours (measured)

The scheduled `monitor` pipeline is the one piece of Autopilot that runs when nothing changed. Measured job
durations on the history project's scheduled pipelines (e.g. [2933040364](https://gitlab.com/hanwg666/gitlab-autopilot/-/pipelines/2933040364), [2933117030](https://gitlab.com/hanwg666/gitlab-autopilot/-/pipelines/2933117030)):

| job on a scheduled pipeline | healthy run | production down (5 probes × 20 s back-off) |
|---|---|---|
| `monitor` | 29.6 – 35.3 s | 111 – 115 s |
| `semgrep-sast` (dragged in by the SAST template's branch rule) | 29 – 33 s | same |
| `audit-trail` (`when: always` on the default branch) | 30.8 – 34.9 s | same |

A healthy run therefore cost **≈ 93 s of runner time, two thirds of it for jobs that produce nothing new** on a
pipeline where no commit changed.

| cadence | runs / day | runner-minutes / day | runner-minutes / 30 days | gCO2e / day (576 g/kWh) |
|---|---|---|---|---|
| `*/15 * * * *`, three jobs (history project, until 2026-10-13) | 96 | **148.8** | 4 464 | 12.7 |
| `0 */2 * * *` as it really ran in the showcase on 2026-10-14: `monitor` 32.1 s + `gemnasium-python-dependency_scanning` 47.6 s ([pipeline 2945631275](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43190989/showcase/-/pipelines/2945631275)) | 12 | **15.9** | 477 | 1.36 |
| `0 */2 * * *`, `monitor` only (since the correction below; the same measured 32.1 s `monitor` job) | 12 | **6.4** | 192 | 0.55 |

**Saved: ≈ 142 runner-minutes a day (96 %)**, or about 4 300 minutes a month — more than ten times the whole
Free-tier allowance (400 min/month), which is exactly why the history project's quota was gone in under three
days. The change: `semgrep-sast`, `audit-trail` and (since 2026-10-14) `gemnasium-python-dependency_scanning` carry
`when: never` for `$CI_PIPELINE_SOURCE == "schedule"` in `.gitlab-ci.yml`, the workflow rule for scheduled and
rollback pipelines sets `SAST_DISABLED`, `SECRET_DETECTION_DISABLED`, `DEPENDENCY_SCANNING_DISABLED` and
`CONTAINER_SCANNING_DISABLED` so that no other template analyzer can start there either, and `scripts/demo.py setup`
creates the schedule with `MONITOR_CRON = "0 */2 * * *"` (an existing schedule is edited in Build > Pipeline
schedules). `sci.schedule_minutes_per_day()` reproduces the table; `tests/test_ci_rules.py` asserts that a
scheduled pipeline runs exactly `monitor` and `incident`.

**Correction (2026-10-14).** Until that day this section and the README said the 2-hour schedule was monitor-only,
at 6.0 runner-minutes a day (a 30 s estimate). The live drill showed otherwise. Both scheduled pipelines of the day,
[2945603793](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43190989/showcase/-/pipelines/2945603793)
(production down: `monitor` 111.1 s, `incident` 35.7 s, `gemnasium-python-dependency_scanning` 54.0 s) and
2945631275 (healthy), also ran the Dependency Scanning template job. Its template rule matches every branch pipeline,
and the job only runs where the Ultimate feature is on. The history project, where §4's measurements were taken, is on
Free, so the job never appeared there; the showcase group is Ultimate, so it did. The real cost of the schedule was
therefore 15.9 runner-minutes a day, not 6.0. With the job excluded it is 6.4 (12 × the measured 32.1 s `monitor`
job). That figure is computed from the measured job, not yet read off a post-fix scheduled pipeline. The scanner
still runs on main pipelines, and now also on merge request pipelines, where a dependency change can actually appear.

Trade-off, stated plainly: detection latency for a production outage rises from ≤ 15 min to ≤ 2 h. For a demo
service with no users that is the right trade; for a real service you would keep a short cadence and make the
probe cheap (one job, no scanners) instead — which is the other half of this change.

## 5. What this does not claim

- It is not a measurement of the runners' electricity, and it ignores the MR pipeline, Duo flow compute, GitLab's
  own infrastructure, network transfer and embodied hardware emissions.
- The grid factor is CCF's published table value; Google's live hourly intensity for `us-east1` differs.
- We did not choose coefficients to make the number small: the headline uses the higher (non-CFE) grid factor.

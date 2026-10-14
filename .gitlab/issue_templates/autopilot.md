<!-- Autopilot issue: the only human action in the journey. Keep it small (one MR, < 400 lines).
     The Planner flow answers with a numbered plan in about 2 minutes, the Developer step opens the MR,
     CI reviews/gates/merges/releases, the journey card and narrative land back here.
     Judging freeze: after 2026-10-27 13:00 UTC (AUTOPILOT §1.3) you get one polite note and no change. -->

## What should change

<!-- One or two sentences. Example: Add per-site latency percentiles (p50/p95) to /api/summary and the status page. -->

## Acceptance checks

- [ ] <!-- e.g. GET /api/summary returns p50_ms and p95_ms per site -->
- [ ] tests added
- [ ] existing JSON keys unchanged

## Out of scope / constraints

<!-- Anything the agent must not touch. Protected paths (AUTOPILOT §2.1) are already excluded. -->

/label ~autopilot

<!-- The real feature issue for the Duo Developer flow (hackathon project). Label: autopilot -->

## What should change

Add per-site latency percentiles to pulse.

- `GET /api/summary` gains two keys per site: `p50_latency_ms` and `p95_latency_ms`, computed over the same
  window as `mean_latency_ms` (the last 100 checks, successful probes only). `null` when there are no successful probes.
- The HTML status page (`GET /`) shows two new columns, **p50** and **p95**, after **latency**.
- `GET /metrics` exposes `pulse_site_latency_p50_ms{site="..."}` and `pulse_site_latency_p95_ms{site="..."}`.

## Acceptance checks

- [ ] `tests/test_store.py`: a site with latencies 10, 20, 30, 40, 50 ms reports p50 = 30 and p95 = 50 (nearest-rank).
- [ ] `tests/test_app.py`: `/api/summary` contains the two keys; `/metrics` contains both gauges; the status page shows the columns.
- [ ] Existing JSON keys unchanged (`mean_latency_ms` stays); `python -m pytest -q` and `ruff check .` green.
- [ ] No new dependencies (use `statistics`/plain Python), no changes outside `pulse/` and `tests/` (AUTOPILOT §2.1, §2.2).

## Out of scope

Histograms, per-window configuration, UI styling beyond two columns.

/label ~autopilot

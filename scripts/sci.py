"""Estimate the CI energy and carbon of one Autopilot journey (an SCI line for the journey card).

This is an **estimate**, not a measurement: GitLab does not expose runner power draw. The method is
the Cloud Carbon Footprint (CCF) one — job duration x a per-vCPU power curve x a memory coefficient x
PUE x the grid intensity of the region the GitLab-hosted runners run in — and every coefficient below
is documented with its source in ``docs/SCI.md``. The functional unit (the "R" of the Green Software
Foundation's SCI) is one journey: one issue taken to production by one main pipeline.

    python scripts/sci.py --pipeline $CI_PIPELINE_ID --out sci.json    # in CI (anonymous GET works on public projects)
    python scripts/sci.py --jobs-json jobs.json                        # offline
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Sources: docs/SCI.md. All values as published on 2026-10-13.
COEFFICIENTS: dict = {
    "provider": "GCP",
    "region": "us-east1",  # GitLab-hosted Linux runners: Google Compute Engine, default region us-east1
    "runner": "saas-linux-small-amd64",  # untagged jobs: 2 vCPU, 8 GB
    "runner_vcpus": 2,
    "runner_memory_gb": 8,
    "min_watts_per_vcpu": 0.71,  # CCF, GCP median min watts
    "max_watts_per_vcpu": 4.26,  # CCF, GCP median max watts
    "avg_cpu_utilization": 0.50,  # CCF default for hyperscale data centres
    "memory_watts_per_gb": 0.392,  # CCF memory coefficient (0.000392 kWh per GB-hour)
    "pue": 1.1,  # CCF, GCP
    "grid_g_co2e_per_kwh": 576.0,  # CCF table, GCP us-east1, WITHOUT the CFE% adjustment (conservative)
    "grid_g_co2e_per_kwh_cfe_adjusted": 397.4,  # same table, WITH Google's carbon-free-energy percentage
    "embodied_g_co2e": 0.0,  # M in the SCI formula: not estimated (shared hosted runners, unknown hardware share)
}


def watts(c: dict = COEFFICIENTS) -> float:
    """Average draw of one runner VM under the CCF compute + memory model, before PUE."""
    per_vcpu = c["min_watts_per_vcpu"] + c["avg_cpu_utilization"] * (c["max_watts_per_vcpu"] - c["min_watts_per_vcpu"])
    return c["runner_vcpus"] * per_vcpu + c["runner_memory_gb"] * c["memory_watts_per_gb"]


def energy_kwh(duration_s: float, c: dict = COEFFICIENTS) -> float:
    """kWh drawn from the grid for ``duration_s`` seconds of one runner VM, PUE included."""
    return watts(c) * (duration_s / 3600.0) / 1000.0 * c["pue"]


def estimate(jobs: list[dict], c: dict = COEFFICIENTS) -> dict:
    """SCI estimate for a list of pipeline jobs (GitLab jobs API objects; ``duration`` in seconds)."""
    seconds = float(sum((j.get("duration") or 0) for j in jobs))
    kwh = energy_kwh(seconds, c)
    return {
        "functional_unit": "one Autopilot journey (one main pipeline)",
        "jobs": len(jobs),
        "ci_seconds": round(seconds, 1),
        "ci_minutes": round(seconds / 60.0, 2),
        "runner_watts": round(watts(c), 3),
        "energy_kwh": round(kwh, 6),
        "g_co2e": round(kwh * c["grid_g_co2e_per_kwh"] + c["embodied_g_co2e"], 4),
        "g_co2e_cfe_adjusted": round(kwh * c["grid_g_co2e_per_kwh_cfe_adjusted"] + c["embodied_g_co2e"], 4),
        "coefficients": dict(c),
        "method": "estimate: CCF compute+memory model x PUE x grid intensity; see docs/SCI.md",
    }


def card_line(est: dict) -> str:
    return (
        f"**CI footprint (estimate):** {est['ci_minutes']} runner-minutes ≈ {est['energy_kwh']:.4f} kWh ≈ "
        f"**{est['g_co2e']:.2f} gCO2e** per journey ({est['g_co2e_cfe_adjusted']:.2f} g with Google's CFE% "
        f"adjustment; GCP {est['coefficients']['region']}, CCF coefficients — `docs/SCI.md`)"
    )


def schedule_minutes_per_day(runs_per_day: float, seconds_per_run: float) -> float:
    """Runner-minutes a scheduled pipeline consumes per day (used in docs/SCI.md for the monitor cadence)."""
    return round(runs_per_day * seconds_per_run / 60.0, 1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pipeline", default=os.environ.get("CI_PIPELINE_ID"))
    ap.add_argument("--jobs-json", help="offline: a JSON array of job objects with a duration field")
    ap.add_argument("--out", default="sci.json")
    args = ap.parse_args(argv)

    if args.jobs_json:
        jobs = json.loads(Path(args.jobs_json).read_text(encoding="utf-8"))
    elif args.pipeline:
        from gitlab_api import GitLab

        jobs = GitLab().get_all(f"pipelines/{args.pipeline}/jobs", include_retried="true")
    else:
        print("nothing to estimate: give --pipeline or --jobs-json")
        return 0
    est = estimate(jobs)
    est["pipeline"] = args.pipeline
    Path(args.out).write_text(json.dumps(est, indent=2), encoding="utf-8")
    print(card_line(est))
    print(f"written {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

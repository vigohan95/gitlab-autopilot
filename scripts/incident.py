"""Open a GitLab Incident and trigger a rollback when the monitor fails (AUTOPILOT §6).

Runs as the ``incident`` job (``when: on_failure`` after ``monitor``). Deterministic
part of the self-healing loop; the Incident Responder flow then adds the RCA.

* Incident creation needs ``AUTOPILOT_TOKEN`` (issues API is not reachable with
  CI_JOB_TOKEN). Without it the script still triggers the rollback and warns.
* Rollback target = newest release tag that is older than the deployed version
  (§6.2, never roll forward). The rollback is a new pipeline on ``main`` with
  ``ROLLBACK_TO=<tag>`` started through the pipeline-trigger API with
  ``CI_JOB_TOKEN``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from gitlab_api import GitLab, warn

INCIDENT_LABELS = ["autopilot", "incident", "env::production"]
REUSE_WINDOW_HOURS = 2


def recent_cutoff() -> str:
    """Only an open incident younger than this is re-used; older ones stay open for humans (AUTOPILOT §6.3)."""
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(hours=REUSE_WINDOW_HOURS)).isoformat(timespec="seconds")


def clear_chaos_switch(gl: GitLab) -> bool:
    """Remediation step 1 (AUTOPILOT §7.1): if the demo chaos switch is on, turn it off on the live service.

    Returns True when a switch was found and cleared. Also removes a legacy project variable if present.
    """
    cleared = False
    try:
        import deploy_render

        url = deploy_render.set_chaos("production", False)  # no-op without RENDER_API_KEY
        if url:
            cleared = True
            print(f"chaos switch cleared on the live production service ({url}); waiting for /healthz 200")
            deploy_render.wait_status(url, 200, timeout=180)
    except Exception as exc:  # noqa: BLE001
        warn(f"could not clear the chaos switch on Render: {exc}")
    if gl.can_write:
        try:
            if any(v["key"] == "PULSE_CHAOS" for v in gl.get_all("variables")):
                gl._client.delete(
                    f"/projects/{gl.project}/variables/PULSE_CHAOS", params={"filter[environment_scope]": "production"}
                )
                cleared = True
                print("legacy PULSE_CHAOS project variable removed")
        except Exception as exc:  # noqa: BLE001
            warn(f"could not inspect CI variables: {exc}")
    return cleared


def pick_rollback_target(releases: list[dict], deployed_version: str | None) -> str | None:
    """Newest release tag strictly older than the deployed one (releases are newest-first)."""
    tags = [r["tag_name"] for r in releases]
    if not tags:
        return None
    current = (
        f"v{deployed_version}" if deployed_version and not str(deployed_version).startswith("v") else deployed_version
    )
    if current in tags:
        idx = tags.index(current)
        return tags[idx + 1] if idx + 1 < len(tags) else None
    # Deployed version is not a release (e.g. a SHA build): roll back to the newest release.
    return tags[0]


def build_description(report: dict, failed: list[dict], rollback_to: str | None, pipeline_url: str) -> str:
    lines = [
        "## Summary",
        "",
        f"The scheduled monitor (pipeline {pipeline_url}) found these environments unhealthy at `{report['ts']}`.",
        "",
        "| environment | url | version | checks |",
        "|---|---|---|---|",
    ]
    for t in failed:
        checks = "; ".join(f"{c['path']}→{c.get('status') or c.get('error')}" for c in t["checks"])
        lines.append(f"| {t['name']} | {t['url']} | {t['version']} | {checks} |")
    lines += [
        "",
        "## Automatic remediation",
        "",
        f"- Rollback target: `{rollback_to}`" if rollback_to else "- No earlier release exists; rollback not possible.",
        "- The Incident Responder flow will attach the root-cause analysis to this incident.",
        "",
        "<details><summary>monitor-report.json</summary>",
        "",
        "```json",
        json.dumps(report, indent=2),
        "```",
        "</details>",
        "",
        "autopilot: rules=[§6.1, §6.2, §6.3]",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", default="monitor-report.json")
    ap.add_argument("--ref", default=os.environ.get("CI_DEFAULT_BRANCH", "main"))
    ap.add_argument("--no-rollback", action="store_true")
    args = ap.parse_args(argv)

    if not Path(args.report).exists():
        print("no monitor report; nothing to do")
        return 0
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    failed = [t for t in report.get("targets", []) if not t.get("ok")]
    if not failed:
        print("monitor report is green; nothing to do")
        return 0

    gl = GitLab()
    pipeline_url = os.environ.get("CI_PIPELINE_URL", "")
    prod = next((t for t in failed if t["name"] == "production"), None)

    chaos_cleared = clear_chaos_switch(gl) if prod else False

    rollback_to = None
    if prod and not args.no_rollback:
        try:
            rollback_to = pick_rollback_target(gl.releases(), prod.get("version"))
        except Exception as exc:  # noqa: BLE001
            warn(f"could not list releases: {exc}")

    incident_iid = None
    if gl.can_write:
        title = f"Production unhealthy: {', '.join(t['name'] for t in failed)} ({report['ts'][:16]})"
        existing = gl.get_all(
            "issues", issue_type="incident", state="opened", labels="autopilot,incident", created_after=recent_cutoff()
        )
        if existing:
            incident_iid = existing[0]["iid"]
            gl.issue_note(incident_iid, f"Monitor failed again at `{report['ts']}` (pipeline {pipeline_url}).")
            print(f"re-used open incident #{incident_iid}")
        else:
            inc = gl.post(
                "issues",
                json={
                    "title": title,
                    "issue_type": "incident",
                    "labels": ",".join(INCIDENT_LABELS),
                    "description": build_description(report, failed, rollback_to, pipeline_url),
                },
            )
            incident_iid = inc["iid"]
            print(f"opened incident #{incident_iid}: {inc['web_url']}")
    else:
        warn("AUTOPILOT_TOKEN not set: incident issue not created (rollback still triggered)")

    pipe: dict = {}
    if prod and rollback_to and not args.no_rollback:
        variables = {"ROLLBACK_TO": rollback_to}
        if incident_iid:
            variables["INCIDENT_IID"] = str(incident_iid)
        try:
            pipe = gl.trigger_pipeline(args.ref, variables)
            print(f"rollback pipeline started: {pipe.get('web_url')} (ROLLBACK_TO={rollback_to})")
        except Exception as exc:  # noqa: BLE001
            body = getattr(getattr(exc, "response", None), "text", "")[:300]
            warn(f"rollback trigger failed: {exc} {body}")
            if incident_iid and gl.can_write:
                gl.issue_note(
                    incident_iid,
                    f"Automatic rollback could NOT be started: `{exc}` `{body}`\n\nA human must roll back.",
                )
        if pipe and incident_iid and gl.can_write:
            extra = (
                "\n\nRemediation step 1: the chaos switch `PULSE_CHAOS` was found on `production` and has been "
                "cleared on the live service (AUTOPILOT §7.1); step 2 is the rollback below."
                if chaos_cleared
                else ""
            )
            gl.issue_note(
                incident_iid,
                f"Rollback to `{rollback_to}` started: {pipe.get('web_url')}{extra}\n\nautopilot: rules=[§6.2, §7.1]",
            )
    with open("incident.env", "w", encoding="utf-8") as fh:
        fh.write(f"INCIDENT_IID={incident_iid or ''}\nROLLBACK_TO={rollback_to or ''}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

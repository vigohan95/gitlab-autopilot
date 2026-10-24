"""Open a GitLab Incident and trigger a rollback when the monitor fails (AUTOPILOT §6).

Runs as the ``incident`` job (``when: on_failure`` after ``monitor``). Deterministic
part of the self-healing loop; the Incident Responder flow then adds the RCA.

* Incident creation needs ``AUTOPILOT_TOKEN`` (issues API is not reachable with
  CI_JOB_TOKEN). Without it the script still triggers the rollback and warns.
* Rollback target = newest release tag that is older than the deployed version
  (§6.2, never roll forward). The deployed version is what production's
  ``/version`` reports; when that is not a release (no answer, or a SHA build),
  it is the release of production's last successful deployment (Deployments
  API: rollback-branch name, tag ref, or a release's commit sha). When neither
  names a release there is **no** target and the incident says a human must
  roll back. Until 2026-10-14 the fallback was "the newest release", which can
  be the very release production runs (the drill's F4 RCA pointed this out:
  the drill picked ``v0.1.2``, the only release, already deployed).
* The rollback is a pipeline on the branch
  ``rollback/<tag>/incident-<iid>`` (``scripts/rollback_ref.py``), cut from the
  commit the monitor ran on and started through the pipeline-trigger API with
  ``CI_JOB_TOKEN`` and **no pipeline variables**: the judged project refuses
  variables below the Maintainer role (docs/DRILL-2026-10-14.md, finding 3).
  Without ``AUTOPILOT_TOKEN`` no branch can be created, and the old call
  (``ROLLBACK_TO=<tag>`` on the default branch) is tried instead.
* Incidents keep the ``autopilot`` label: it is what the Incident Responder
  (F4) and the re-use lookup below search for, and it is not what wakes the
  Planner — T1 fires on *every* new work item; F1 skips incidents itself
  (AUTOPILOT §1.1).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from . import rollback_ref
    from .gitlab_api import GitLab, warn
except ImportError:
    import rollback_ref
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


def _as_tag(version: str | None) -> str | None:
    if not version:
        return None
    version = str(version)
    return version if version.startswith("v") else f"v{version}"


def pick_rollback_target(releases: list[dict], deployed_version: str | None) -> str | None:
    """Newest release tag strictly older than the deployed one (releases are newest-first).

    None when the deployed version is not a release (unknown, or a SHA build) or is the oldest release: rolling
    "back" to the newest release could redeploy what is already running, so we never guess (§6.2).
    """
    tags = [r["tag_name"] for r in releases]
    current = _as_tag(deployed_version)
    if current not in tags:
        return None
    idx = tags.index(current)
    return tags[idx + 1] if idx + 1 < len(tags) else None


def last_production_deployment(gl: GitLab) -> dict | None:
    """Production's last successful deployment (Deployments API; needs AUTOPILOT_TOKEN, it is not anonymous)."""
    deps = gl.get(
        "deployments", environment="production", status="success", order_by="finished_at", sort="desc", per_page=1
    )
    return deps[0] if isinstance(deps, list) and deps else None


def release_of_deployment(deployment: dict, releases: list[dict]) -> tuple[str | None, str]:
    """(release tag, how it was matched) for the code a deployment put on production; (None, ref@sha) otherwise.

    A rollback pipeline runs on ``rollback/<tag>/…`` but its commit is the monitor's, so the tag comes from the
    branch name first; then a tag pipeline's ref; then a release whose commit is exactly the deployed sha. A
    commit that is not a release (a later push to main) does not name a release: no guess.
    """
    ref = deployment.get("ref") or ""
    sha = deployment.get("sha") or ""
    tags = [r["tag_name"] for r in releases]
    parsed = rollback_ref.parse(ref)
    if parsed and parsed[0] in tags:
        return parsed[0], f"rollback branch `{ref}`"
    if ref in tags:
        return ref, f"tag `{ref}`"
    for r in releases:
        if sha and (r.get("commit") or {}).get("id") == sha:
            return r["tag_name"], f"`{ref}` at `{sha[:8]}`, the commit of release `{r['tag_name']}`"
    return None, f"`{ref}` at `{sha[:8]}`"


def resolve_rollback_target(gl: GitLab, releases: list[dict], reported_version: str | None) -> tuple[str | None, str]:
    """(target tag or None, the reason in words) for AUTOPILOT §6.2. ``reported_version``: production's /version."""
    if not releases:
        return None, "the project has no releases"
    reported = _as_tag(reported_version)
    if reported in [r["tag_name"] for r in releases]:
        deployed, basis = reported, f"production's `/version` reports `{reported}`"
    else:
        seen = (
            f"`/version` reported `{reported_version}`, which is not a release"
            if reported_version
            else "`/version` did not answer"
        )
        try:
            dep = last_production_deployment(gl)
        except Exception as exc:  # noqa: BLE001
            return None, f"{seen}, and production's deployments could not be read ({exc})"
        if not dep:
            return None, f"{seen}, and production has no successful deployment"
        deployed, how = release_of_deployment(dep, releases)
        where = f"production's last successful deployment (#{dep.get('iid', '?')}, {how})"
        if not deployed:
            return None, f"{seen}, and {where} is not a release"
        basis = f"{seen}; {where} is `{deployed}`"
    target = pick_rollback_target(releases, deployed)
    if not target:
        return None, f"{basis}, and no older release exists"
    return target, f"{basis}; `{target}` is the newest release older than that"


def start_rollback(
    gl: GitLab, tag: str, incident_iid: int | None, from_ref: str, pipeline_id: str | int | None
) -> tuple[dict, str]:
    """Start the rollback pipeline for ``tag``; returns (pipeline, ref it runs on). Raises on API errors.

    Needs no pipeline variables (finding 3 of the 2026-10-14 drill): the target and the incident are in the
    branch name, the branch is cut from ``from_ref`` so the current CI configuration runs, and the deploy jobs
    build the release tag's commit (``scripts/deploy.py --ref``).
    """
    if not gl.can_write:
        # Creating a branch needs AUTOPILOT_TOKEN; the job token can only trigger. Last resort: the variable call.
        return gl.trigger_pipeline(from_ref, {"ROLLBACK_TO": tag}), from_ref
    branch = rollback_ref.branch_name(tag, incident_iid, pipeline_id)
    try:
        gl.post("repository/branches", json={"branch": branch, "ref": from_ref})
        print(f"rollback branch {branch} created from {from_ref}")
    except Exception as exc:  # noqa: BLE001
        resp = getattr(exc, "response", None)
        if resp is None or resp.status_code != 400 or "already exists" not in resp.text:
            raise
        print(f"rollback branch {branch} already exists (incident re-used); starting a new pipeline on it")
    return gl.trigger_pipeline(branch, {}), branch


def build_description(
    report: dict, failed: list[dict], rollback_to: str | None, pipeline_url: str, rollback_basis: str = ""
) -> str:
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
        (
            f"- Rollback target: `{rollback_to}` ({rollback_basis or 'AUTOPILOT §6.2'})."
            if rollback_to
            else f"- **No automatic rollback:** {rollback_basis or 'no rollback target'}. A human must roll back."
        ),
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

    rollback_to, rollback_basis = None, ""
    if prod and not args.no_rollback:
        try:
            rollback_to, rollback_basis = resolve_rollback_target(gl, gl.releases(), prod.get("version"))
        except Exception as exc:  # noqa: BLE001
            rollback_basis = f"the releases could not be listed ({exc})"
            warn(rollback_basis)
        print(f"rollback target: {rollback_to or 'none'} ({rollback_basis})")

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
                    "description": build_description(report, failed, rollback_to, pipeline_url, rollback_basis),
                },
            )
            incident_iid = inc["iid"]
            print(f"opened incident #{incident_iid}: {inc['web_url']}")
    else:
        warn("AUTOPILOT_TOKEN not set: incident issue not created (rollback still triggered)")

    pipe: dict = {}
    rollback_on = ""
    if prod and not rollback_to and not args.no_rollback and incident_iid and gl.can_write:
        gl.issue_note(
            incident_iid,
            f"No automatic rollback: {rollback_basis}. A human must roll back: pick the release, then run a "
            "pipeline on the branch `rollback/<tag>/incident-<iid>` (docs/RUNBOOK-M1-M2.md, M2.3)."
            "\n\nautopilot: rules=[§6.2]",
        )
    if prod and rollback_to and not args.no_rollback:
        try:
            from_ref = os.environ.get("CI_COMMIT_SHA") or args.ref
            pipe, rollback_on = start_rollback(
                gl, rollback_to, incident_iid, from_ref, os.environ.get("CI_PIPELINE_ID")
            )
            print(f"rollback pipeline started: {pipe.get('web_url')} (ref {rollback_on}, target {rollback_to})")
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
                f"Rollback to `{rollback_to}` started on `{rollback_on}`: {pipe.get('web_url')}{extra}"
                "\n\nautopilot: rules=[§6.2, §7.1]",
            )
    with open("incident.env", "w", encoding="utf-8") as fh:
        fh.write(f"INCIDENT_IID={incident_iid or ''}\nROLLBACK_TO={rollback_to or ''}\nROLLBACK_REF={rollback_on}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

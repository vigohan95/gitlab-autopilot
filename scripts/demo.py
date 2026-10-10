"""Drive the three Autopilot demo scenes through the GitLab API (no Duo required).

Scenes
  mr        issue (label autopilot) -> branch -> patch-version bump -> MR "Closes #n"
            -> MR pipeline: policy-check labels autopilot::approved -> auto-merge merges
            -> main pipeline: release v0.1.(n+1) -> journey-card closes the issue
  security  branch pins fastapi==0.125.0 (pulls starlette 0.50 with PYSEC-2026-2280/2281/248/249)
            -> pip-audit goes red -> MR is blocked; `--fix` pushes the corrected pin
            (what the Security Fixer flow does when Duo is enabled)
  chaos on  sets PULSE_CHAOS=healthz on environment `production` (or --fake-url when
            no Hugging Face deployment exists yet) and plays the "monitor" schedule
            -> monitor red -> incident job opens an Incident + triggers the rollback
  chaos off removes the variable (and the fake URL) and plays the schedule again

Also: setup (labels + schedule), status, cleanup.

Credentials: AUTOPILOT_TOKEN (project access token, scope api, role Maintainer).
Runs locally (`--project <id>`) or as the manual `demo:*` CI jobs on main.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import UTC, datetime

from gitlab_api import GitLab

LABELS = {
    "autopilot": "#1f75cb",
    "autopilot::approved": "#2da160",
    "autopilot::needs-human": "#e24329",
    "autopilot::human-approved": "#6f3f9e",
    "security-fix": "#fc9403",
    "incident": "#e24329",
    "env::production": "#555555",
    "rca::done": "#2da160",
}
SCHEDULE_DESC = "monitor"
VULNERABLE_FASTAPI = "0.125.0"
FAKE_PROD_URL = "https://pulse-chaos.invalid"


# -- pure helpers (unit-tested) ------------------------------------------------
def bump_patch(init_text: str) -> tuple[str, str, str]:
    m = re.search(r'__version__\s*=\s*"(\d+)\.(\d+)\.(\d+)"', init_text)
    if not m:
        raise ValueError("no __version__ in pulse/__init__.py")
    old = ".".join(m.groups())
    new = f"{m.group(1)}.{m.group(2)}.{int(m.group(3)) + 1}"
    return init_text.replace(f'"{old}"', f'"{new}"', 1), old, new


def pin_requirement(req_text: str, package: str, version: str) -> tuple[str, str | None]:
    """Return (new_text, previous_version) with ``package==version`` pinned (extras preserved)."""
    pat = re.compile(rf"^({re.escape(package)}(?:\[[^\]]*\])?)==(\S+)", re.M | re.I)
    m = pat.search(req_text)
    if not m:
        return req_text.rstrip("\n") + f"\n{package}=={version}\n", None
    return pat.sub(rf"\g<1>=={version}", req_text, count=1), m.group(2)


def _stamp() -> str:
    return datetime.now(UTC).strftime("%m%d-%H%M")


# -- API helpers ----------------------------------------------------------------
class Demo:
    def __init__(self, gl: GitLab, ref: str = "main") -> None:
        self.gl = gl
        self.ref = ref
        self.url = gl.project_web_url()

    def file(self, path: str, ref: str | None = None) -> str:
        return self.gl.get(f"repository/files/{path.replace('/', '%2F')}/raw", ref=ref or self.ref)

    def commit(self, branch: str, message: str, actions: list[dict], start_branch: str | None = None) -> dict:
        body = {"branch": branch, "commit_message": message, "actions": actions}
        if start_branch:
            body["start_branch"] = start_branch
        return self.gl.post("repository/commits", json=body)

    def open_mr(self, branch: str, title: str, description: str, labels: list[str]) -> dict:
        return self.gl.post(
            "merge_requests",
            json={
                "source_branch": branch,
                "target_branch": self.ref,
                "title": title,
                "description": description,
                "labels": ",".join(labels),
                "remove_source_branch": True,
                "squash": True,
            },
        )

    def set_variable(self, key: str, value: str, scope: str = "*") -> None:
        existing = [v for v in self.gl.get_all("variables") if v["key"] == key and v["environment_scope"] == scope]
        if existing:
            self.gl.put(
                f"variables/{key}",
                json={"value": value, "environment_scope": scope, "filter[environment_scope]": scope},
            )
        else:
            self.gl.post("variables", json={"key": key, "value": value, "environment_scope": scope})

    def delete_variable(self, key: str, scope: str = "*") -> bool:
        r = self.gl._client.delete(
            f"/projects/{self.gl.project}/variables/{key}", params={"filter[environment_scope]": scope}
        )
        return r.status_code in (204, 404)

    def schedule(self) -> dict | None:
        for s in self.gl.get_all("pipeline_schedules"):
            if s["description"] == SCHEDULE_DESC:
                return s
        return None

    def play_schedule(self) -> dict:
        s = self.schedule() or self.gl.post(
            "pipeline_schedules",
            json={"description": SCHEDULE_DESC, "ref": self.ref, "cron": "*/15 * * * *", "active": True},
        )
        return self.gl.post(f"pipeline_schedules/{s['id']}/play")


# -- scenes ---------------------------------------------------------------------
def scene_setup(d: Demo) -> int:
    existing = {lb["name"] for lb in d.gl.get_all("labels")}
    for name, color in LABELS.items():
        if name not in existing:
            d.gl.post("labels", json={"name": name, "color": color})
            print(f"label created: {name}")
    s = d.schedule()
    if not s:
        s = d.gl.post(
            "pipeline_schedules",
            json={"description": SCHEDULE_DESC, "ref": d.ref, "cron": "*/15 * * * *", "active": True},
        )
        print(f"schedule created: {SCHEDULE_DESC} (*/15 * * * *)")
    else:
        print(f"schedule present: {SCHEDULE_DESC} (next run {s.get('next_run_at')})")
    keys = {v["key"] for v in d.gl.get_all("variables")}
    for k in (
        "AUTOPILOT_TOKEN",
        "HF_TOKEN",
        "HF_STAGING_SPACE",
        "HF_PRODUCTION_SPACE",
        "PULSE_STAGING_URL",
        "PULSE_PRODUCTION_URL",
    ):
        print(f"variable {k:<22} {'set' if k in keys else 'MISSING'}")
    return 0


def scene_mr(d: Demo, with_issue: bool = True) -> int:
    init_text = d.file("pulse/__init__.py")
    new_text, old, new = bump_patch(init_text)
    issue = None
    if with_issue:
        issue = d.gl.post(
            "issues",
            json={
                "title": f"Release pulse {new}: bump patch version",
                "description": (
                    "## What should change\n\nBump `pulse.__version__` from "
                    f"`{old}` to `{new}` so the pipeline cuts release `v{new}`.\n\n"
                    "## Acceptance checks\n\n- [ ] `/version` reports the new version\n- [ ] release `v"
                    f"{new}` exists\n\n/label ~autopilot"
                ),
                "labels": "autopilot",
            },
        )
        print(f"issue #{issue['iid']}: {issue['web_url']}")
    branch = f"demo/release-{new}-{_stamp()}"
    d.commit(
        branch,
        f"pulse: bump version to {new}" + (f" (#{issue['iid']})" if issue else ""),
        [{"action": "update", "file_path": "pulse/__init__.py", "content": new_text}],
        start_branch=d.ref,
    )
    desc = (
        f"Bumps `pulse.__version__` {old} → {new}. Touches only `pulse/__init__.py` (not a protected path, "
        "AUTOPILOT §2.1), 1 changed line (§3.4), no dependency changes (§2.2).\n\n"
        + (f"Closes #{issue['iid']}\n\n" if issue else "")
        + "autopilot: rules=[§1.1, §2.1, §3.4]"
    )
    mr = d.open_mr(
        branch, f"pulse: bump version to {new}" + (f" (#{issue['iid']})" if issue else ""), desc, ["autopilot"]
    )
    print(f"merge request !{mr['iid']}: {mr['web_url']}")
    print(
        "next: MR pipeline -> policy-check (labels autopilot::approved) -> auto-merge -> main pipeline -> release v"
        + new
    )
    return 0


def scene_security(d: Demo, fix: bool = False) -> int:
    req = d.file("requirements.txt")
    if fix:
        mrs = [
            m
            for m in d.gl.get_all("merge_requests", state="opened", labels="autopilot")
            if "demo/security" in m["source_branch"]
        ]
        if not mrs:
            print("no open security demo MR to fix")
            return 1
        mr = mrs[0]
        good = pin_requirement(
            d.file("requirements.txt", mr["source_branch"]), "fastapi", _current_pin(req, "fastapi")
        )[0]
        d.commit(
            mr["source_branch"],
            f"security: bump fastapi to {_current_pin(req, 'fastapi')} (pip-audit: starlette PYSEC-2026-248/249)",
            [{"action": "update", "file_path": "requirements.txt", "content": good}],
        )
        d.gl.add_labels("merge_requests", mr["iid"], ["security-fix"])
        d.gl.put(  # the squash commit takes the MR title: make it say what actually merged
            f"merge_requests/{mr['iid']}",
            json={"title": f"security: bump fastapi to {_current_pin(req, 'fastapi')} after pip-audit findings"},
        )
        d.gl.mr_note(
            mr["iid"],
            "Security fix: restored the current `fastapi` pin after `pip-audit` flagged starlette "
            "PYSEC-2026-248/249 in the vulnerable version. This is what the Security Fixer flow does automatically "
            "when Duo is enabled.\n\nautopilot: rules=[§4.1, §4.2, §2.2]",
        )
        print(f"fix pushed to !{mr['iid']} ({mr['web_url']}); pipeline should go green and auto-merge")
        return 0

    bad, prev = pin_requirement(req, "fastapi", VULNERABLE_FASTAPI)
    branch = f"demo/security-{_stamp()}"
    d.commit(
        branch,
        f"deps: pin fastapi=={VULNERABLE_FASTAPI} (demo: known-vulnerable starlette)",
        [{"action": "update", "file_path": "requirements.txt", "content": bad}],
        start_branch=d.ref,
    )
    mr = d.open_mr(
        branch,
        f"deps: pin fastapi=={VULNERABLE_FASTAPI}",
        f"Demo of the secure stage: `fastapi=={VULNERABLE_FASTAPI}` pulls starlette 0.50.x "
        f"(PYSEC-2026-2280/2281, 248/249). `pip-audit` must fail and the merge gate must hold (AUTOPILOT §3.3). "
        f"Previous pin: `{prev}`.\n\nautopilot: rules=[§3.3]",
        ["autopilot"],
    )
    print(f"merge request !{mr['iid']}: {mr['web_url']}")
    print("next: pip-audit fails -> auto-merge skipped (MR blocked); then run `demo.py security --fix`")
    return 0


def _current_pin(req: str, package: str) -> str:
    m = re.search(rf"^{package}(?:\[[^\]]*\])?==(\S+)", req, re.M | re.I)
    if not m:
        raise SystemExit(f"{package} not pinned in requirements.txt")
    return m.group(1)


def scene_chaos(d: Demo, on: bool, fake_url: str | None = None, play: bool = True) -> int:
    import deploy_render

    if on:
        if fake_url:
            d.set_variable("PULSE_PRODUCTION_URL", fake_url)
            print(f"PULSE_PRODUCTION_URL temporarily set to {fake_url} (no real deployment)")
        d.set_variable("PULSE_CHAOS", "healthz", scope="production")
        print("PULSE_CHAOS=healthz set on environment production (AUTOPILOT §7)")
        url = deploy_render.set_chaos("production", True)
        if url:
            print(f"chaos switch applied to the live Render service (redeployed with the new environment): {url}")
            if not deploy_render.wait_status(url, 503):
                print("::warning:: production did not start returning 503 within the wait; monitor may still be green")
    else:
        d.delete_variable("PULSE_CHAOS", scope="production")
        print("PULSE_CHAOS removed")
        if fake_url:
            d.delete_variable("PULSE_PRODUCTION_URL")
            print("fake PULSE_PRODUCTION_URL removed")
        url = deploy_render.set_chaos("production", False)
        if url:
            print(f"chaos switch cleared on the live Render service (redeployed with the new environment): {url}")
            deploy_render.wait_status(url, 200)
    if play:
        p = d.play_schedule()
        print(f"schedule '{SCHEDULE_DESC}' played: {p.get('message', p)}")
        print("next: monitor job red -> incident job opens an Incident and triggers ROLLBACK_TO=<last release>")
    return 0


def scene_status(d: Demo) -> int:
    for mr in d.gl.get_all("merge_requests", state="opened", labels="autopilot"):
        print(f"MR !{mr['iid']} [{', '.join(mr['labels'])}] {mr['title']} -> {mr['web_url']}")
    for p in d.gl.get("pipelines", per_page=8):
        print(f"pipeline {p['id']:<12} {p['status']:<9} {p['source']:<9} {p['ref']}  {p['web_url']}")
    for i in d.gl.get_all("issues", state="opened", issue_type="incident"):
        print(f"incident #{i['iid']} {i['title']} -> {i['web_url']}")
    return 0


def scene_cleanup(d: Demo) -> int:
    for mr in d.gl.get_all("merge_requests", state="opened", labels="autopilot"):
        if mr["source_branch"].startswith("demo/"):
            d.gl.put(f"merge_requests/{mr['iid']}", json={"state_event": "close"})
            print(f"closed !{mr['iid']}")
    for b in d.gl.get_all("repository/branches", search="^demo/"):
        d.gl._client.delete(f"/projects/{d.gl.project}/repository/branches/{b['name'].replace('/', '%2F')}")
        print(f"deleted branch {b['name']}")
    d.delete_variable("PULSE_CHAOS", scope="production")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=os.environ.get("CI_PROJECT_ID"), help="project id or url-encoded path")
    ap.add_argument("--ref", default=os.environ.get("CI_DEFAULT_BRANCH", "main"))
    sub = ap.add_subparsers(dest="scene", required=True)
    sub.add_parser("setup")
    p_mr = sub.add_parser("mr")
    p_mr.add_argument("--no-issue", action="store_true")
    p_sec = sub.add_parser("security")
    p_sec.add_argument("--fix", action="store_true")
    p_ch = sub.add_parser("chaos")
    p_ch.add_argument("state", choices=["on", "off"])
    p_ch.add_argument("--fake-url", nargs="?", const=FAKE_PROD_URL, help="use when no Hugging Face deployment exists")
    p_ch.add_argument("--no-play", action="store_true")
    sub.add_parser("status")
    sub.add_parser("cleanup")
    args = ap.parse_args(argv)

    if not os.environ.get("AUTOPILOT_TOKEN"):
        print("skipped: AUTOPILOT_TOKEN not set (project access token, scope api)")
        return 0
    if not args.project:
        print("skipped: --project (or CI_PROJECT_ID) required")
        return 0
    d = Demo(GitLab(project=str(args.project)), ref=args.ref)
    t0 = time.time()
    rc = {
        "setup": lambda: scene_setup(d),
        "mr": lambda: scene_mr(d, with_issue=not args.no_issue),
        "security": lambda: scene_security(d, fix=args.fix),
        "chaos": lambda: scene_chaos(d, args.state == "on", fake_url=args.fake_url, play=not args.no_play),
        "status": lambda: scene_status(d),
        "cleanup": lambda: scene_cleanup(d),
    }[args.scene]()
    print(f"done in {time.time() - t0:.1f}s")
    return rc


if __name__ == "__main__":
    sys.exit(main())

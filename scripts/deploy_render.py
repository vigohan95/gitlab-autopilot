"""Deploy pulse to Render (free web service, Docker runtime) — DevSecOps stage: configure.

    python scripts/deploy_render.py --env staging --version 0.1.1 --commit <sha>

Render runs the service from the connected GitHub repository, so a deployment is
"build this commit": the script upserts the environment variables that identify
the build, asks Render to deploy exactly ``--commit`` (``POST /v1/services/{id}/deploys``
with ``commitId``), waits for the deploy to be ``live``, then checks ``/healthz``
and ``/version``. Rollback is the same call with the release tag's commit.

Needs ``RENDER_API_KEY`` (masked CI/CD variable; Account Settings > API Keys).
Service names default to ``pulse-staging`` / ``pulse-prod`` (``render.yaml``) and
can be overridden with ``RENDER_STAGING_SERVICE`` / ``RENDER_PRODUCTION_SERVICE``.
Writes ``deploy.env``: DEPLOY_URL, DEPLOY_VERSION, DEPLOY_MODE=live, DEPLOY_PROVIDER=render.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import httpx

API = "https://api.render.com/v1"
DEFAULT_SERVICES = {"staging": "pulse-staging", "production": "pulse-prod"}
FINAL_OK = {"live"}
FINAL_BAD = {"build_failed", "update_failed", "canceled", "pre_deploy_failed", "deactivated"}


class Render:
    def __init__(self, api_key: str, transport: httpx.BaseTransport | None = None) -> None:
        self.c = httpx.Client(
            base_url=API,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=60,
            transport=transport,
        )

    def find_service(self, name: str) -> dict:
        r = self.c.get("/services", params={"name": name, "limit": 20})
        r.raise_for_status()
        for item in r.json():
            s = item.get("service", item)
            if s.get("name") == name:
                return s
        raise SystemExit(f"Render service {name!r} not found; create it from render.yaml (New > Blueprint)")

    def upsert_env(self, service_id: str, key: str, value: str) -> None:
        r = self.c.put(f"/services/{service_id}/env-vars/{key}", json={"value": value})
        if r.status_code >= 400:
            print(f"::warning:: could not set {key} on {service_id}: HTTP {r.status_code} {r.text[:120]}")

    def delete_env(self, service_id: str, key: str) -> None:
        r = self.c.delete(f"/services/{service_id}/env-vars/{key}")
        if r.status_code not in (204, 404):
            print(f"::warning:: could not delete {key} on {service_id}: HTTP {r.status_code}")

    def latest_deploy(self, service_id: str) -> dict:
        r = self.c.get(f"/services/{service_id}/deploys", params={"limit": 1})
        r.raise_for_status()
        items = r.json()
        first = items[0] if items else {}
        return first.get("deploy", first)

    def _deploy_result(self, r: httpx.Response, service_id: str) -> dict:
        """Render answers 201 + JSON, or 202 Queued with an empty body when a deploy is already in flight
        (e.g. right after a service PATCH). In that case the queued deploy is the newest one on the service."""
        r.raise_for_status()
        if r.content:
            try:
                return r.json()
            except ValueError:
                pass
        print(f"deploy request accepted with HTTP {r.status_code} and no body; using the newest deploy")
        time.sleep(5)
        return self.latest_deploy(service_id)

    def deploy(self, service_id: str, commit: str | None) -> dict:
        body: dict = {"clearCache": "do_not_clear"}
        if commit:
            body["commitId"] = commit
        return self._deploy_result(self.c.post(f"/services/{service_id}/deploys", json=body), service_id)

    def restart(self, service_id: str) -> None:
        r = self.c.post(f"/services/{service_id}/restart")
        if r.status_code >= 400:
            print(f"::warning:: restart of {service_id} returned HTTP {r.status_code} {r.text[:120]}")

    def ensure_health_check(self, service: dict, path: str = "/version") -> None:
        """Platform health check = /version: exists in every release (rollbacks to old releases must pass it)
        and is never affected by the chaos switch. Blueprint sync can lag, so it is enforced through the API."""
        current = (service.get("serviceDetails") or {}).get("healthCheckPath")
        if current == path:
            return
        r = self.c.patch(f"/services/{service['id']}", json={"serviceDetails": {"healthCheckPath": path}})
        if r.status_code >= 400:
            print(f"::warning:: could not set healthCheckPath={path}: HTTP {r.status_code} {r.text[:120]}")
        else:
            print(f"health check path set to {path} (was {current})")

    def redeploy(self, service_id: str) -> dict:
        """Apply changed env vars: redeploy the latest build without rebuilding (falls back to a full deploy)."""
        r = self.c.post(f"/services/{service_id}/deploys", json={"deployMode": "deploy_only"})
        if r.status_code >= 400:
            print(f"::warning:: deploy_only refused (HTTP {r.status_code} {r.text[:100]}); doing a full deploy")
            r = self.c.post(f"/services/{service_id}/deploys", json={"clearCache": "do_not_clear"})
        return self._deploy_result(r, service_id)

    def deploy_status(self, service_id: str, deploy_id: str) -> str:
        r = self.c.get(f"/services/{service_id}/deploys/{deploy_id}")
        r.raise_for_status()
        try:
            return r.json().get("status", "")
        except ValueError:
            return ""


def set_chaos(env: str, on: bool) -> str | None:
    """Flip PULSE_CHAOS on the live Render service (AUTOPILOT §7) and restart it. Returns the service URL."""
    key = os.environ.get("RENDER_API_KEY", "")
    if not key:
        return None
    name = os.environ.get(f"RENDER_{env.upper()}_SERVICE") or DEFAULT_SERVICES[env]
    render = Render(key)
    service = render.find_service(name)
    render.ensure_health_check(service)
    if on:
        render.upsert_env(service["id"], "PULSE_CHAOS", "healthz")
    else:
        render.delete_env(service["id"], "PULSE_CHAOS")
    dep = render.redeploy(service["id"])
    status = wait_live(render, service["id"], dep["id"], timeout=600)
    print(f"redeploy {dep.get('id')} -> {status}")
    return service_url(service)


def wait_status(url: str, want: int, timeout: float = 240, poll: float = 10) -> bool:
    """Wait until GET url/healthz returns HTTP ``want`` (503 = chaos active, 200 = healthy)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=15, follow_redirects=True) as c:
                code = c.get(url + "/healthz").status_code
            print(f"  {url}/healthz -> {code} (want {want})")
            if code == want:
                return True
        except httpx.HTTPError as exc:
            print(f"  {url}/healthz -> {type(exc).__name__}")
        time.sleep(poll)
    return False


def service_url(service: dict) -> str:
    details = service.get("serviceDetails") or {}
    url = details.get("url") or service.get("url") or ""
    return url.rstrip("/")


def wait_live(render: Render, service_id: str, deploy_id: str, timeout: float, poll: float = 20.0) -> str:
    deadline = time.time() + timeout
    status = ""
    while time.time() < deadline:
        status = render.deploy_status(service_id, deploy_id)
        print(f"deploy {deploy_id}: {status}")
        if status in FINAL_OK or status in FINAL_BAD:
            return status
        time.sleep(poll)
    return status or "timeout"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", required=True, choices=["staging", "production"])
    ap.add_argument("--version", required=True)
    ap.add_argument(
        "--commit", default=os.environ.get("CI_COMMIT_SHA"), help="git sha to deploy (default: CI_COMMIT_SHA)"
    )
    ap.add_argument(
        "--sites", default=os.environ.get("PULSE_SITES", "gitlab=https://gitlab.com/,render=https://render.com/")
    )
    ap.add_argument("--chaos", default="", help="AUTOPILOT §7 demo-only switch (explicit only; deploys clear it)")
    ap.add_argument("--wait", type=float, default=600, help="seconds to wait for the deploy + health (0 = don't wait)")
    args = ap.parse_args(argv)

    key = os.environ.get("RENDER_API_KEY", "")
    if not key:
        print("skipped: RENDER_API_KEY not set")
        return 0
    name = os.environ.get(f"RENDER_{args.env.upper()}_SERVICE") or DEFAULT_SERVICES[args.env]
    render = Render(key)
    service = render.find_service(name)
    render.ensure_health_check(service)
    sid, url = service["id"], service_url(service)
    Path("deploy.env").write_text(
        f"DEPLOY_URL={url}\nDEPLOY_VERSION={args.version}\nDEPLOY_MODE=live\nDEPLOY_PROVIDER=render\n",
        encoding="utf-8",
    )

    for k, v in {
        "PULSE_VERSION": args.version,
        "PULSE_SITES": args.sites,
        "PULSE_INTERVAL": "300",
        "PULSE_PORT": "10000",
    }.items():
        render.upsert_env(sid, k, v)
    if args.chaos:
        render.upsert_env(sid, "PULSE_CHAOS", args.chaos)
    else:
        render.delete_env(sid, "PULSE_CHAOS")

    dep = render.deploy(sid, args.commit)
    print(f"render deploy started: service={name} ({sid}) commit={args.commit} deploy={dep.get('id')} url={url}")
    if args.wait <= 0:
        return 0
    started = time.time()
    status = wait_live(render, sid, dep["id"], args.wait)
    if status not in FINAL_OK:
        print(f"ERROR: deploy {dep.get('id')} ended with status {status}")
        return 1
    from deploy_hf import wait_healthy

    remaining = max(60.0, args.wait - (time.time() - started))
    if not wait_healthy(url, args.version, remaining):
        print(f"ERROR: {url} did not report version {args.version} healthy within {remaining:.0f}s")
        return 1
    print(f"{args.env} healthy at {url} (version {args.version}, render deploy {dep.get('id')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

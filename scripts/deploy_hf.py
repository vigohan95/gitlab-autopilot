"""Deploy pulse to a Hugging Face Docker Space (DevSecOps stage: configure).

    python scripts/deploy_hf.py --env staging --space user/pulse-staging --version 0.1.0

Zero-cost hosting: Docker Spaces run on the free CPU tier. The script uploads a
minimal build context (Dockerfile, requirements.txt, pulse/, Space README) with
``huggingface_hub`` and sets the Space variables, then waits for ``/healthz``.

Without ``HF_TOKEN`` the script performs a dry run (prints the plan, exit 0) so
the pipeline and the environment records still exist on forks without secrets.
Writes ``deploy.env`` (dotenv artifact) with ``DEPLOY_URL`` and ``DEPLOY_VERSION``.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SPACE_README = """---
title: pulse {env}
emoji: 📡
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: site-health probe service operated by GitLab Autopilot
---

Deployed automatically by the GitLab Autopilot pipeline. Version `{version}` ({env}).
Source: {source}
"""


def space_url(space: str) -> str:
    owner, name = space.split("/", 1)
    return f"https://{owner}-{name}.hf.space".replace("_", "-").lower()


def build_context(dest: Path, env: str, version: str, source: str) -> None:
    for item in ("Dockerfile", "requirements.txt"):
        shutil.copy(ROOT / item, dest / item)
    shutil.copytree(ROOT / "pulse", dest / "pulse", ignore=shutil.ignore_patterns("__pycache__"))
    (dest / "README.md").write_text(SPACE_README.format(env=env, version=version, source=source), encoding="utf-8")


def wait_healthy(url: str, version: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=10, follow_redirects=True) as c:
                h = c.get(url + "/healthz")
                v = c.get(url + "/version")
            if h.status_code == 200 and v.status_code == 200 and v.json().get("version") == version:
                return True
            seen = v.json().get("version") if v.status_code == 200 else v.status_code
            print(f"waiting: healthz={h.status_code} version={seen}")
        except (httpx.HTTPError, ValueError) as exc:
            print(f"waiting: {type(exc).__name__}")
        time.sleep(15)
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", required=True, choices=["staging", "production"])
    ap.add_argument("--space", required=True, help="owner/space-name")
    ap.add_argument("--version", required=True)
    ap.add_argument(
        "--sites", default=os.environ.get("PULSE_SITES", "gitlab=https://gitlab.com/,hf=https://huggingface.co/")
    )
    ap.add_argument("--wait", type=float, default=420, help="seconds to wait for /healthz (0 = don't wait)")
    ap.add_argument("--chaos", default=os.environ.get("PULSE_CHAOS", ""), help="AUTOPILOT §7 demo-only switch")
    args = ap.parse_args(argv)

    url = space_url(args.space)
    source = os.environ.get("CI_PROJECT_URL", "https://gitlab.com")
    token = os.environ.get("HF_TOKEN", "")
    Path("deploy.env").write_text(
        f"DEPLOY_URL={url}\nDEPLOY_VERSION={args.version}\nDEPLOY_MODE=dry-run\nDEPLOY_PROVIDER=huggingface\n",
        encoding="utf-8",
    )

    if not token:
        print(f"DRY RUN (HF_TOKEN not set): would deploy pulse {args.version} to {args.space} -> {url}")
        return 0

    from huggingface_hub import HfApi
    from huggingface_hub.errors import HfHubHTTPError

    api = HfApi(token=token)
    try:
        api.create_repo(args.space, repo_type="space", space_sdk="docker", exist_ok=True, private=False)
    except HfHubHTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 402:
            # Hugging Face now requires a PRO subscription for Docker Spaces on free CPU (HTTP 402).
            # Paid hosting is out of scope (zero-cost project): record the environment, skip the upload.
            print(
                f"::warning:: Hugging Face refused to create {args.space}: Docker Spaces need a paid PRO plan "
                "(HTTP 402). Deployment skipped (dry run); use the Render deployer (RENDER_API_KEY) instead."
            )
            return 0
        raise
    for key, value in {"PULSE_VERSION": args.version, "PULSE_SITES": args.sites, "PULSE_INTERVAL": "300"}.items():
        api.add_space_variable(args.space, key, value)
    if args.chaos:
        api.add_space_variable(args.space, "PULSE_CHAOS", args.chaos)
    else:
        with contextlib.suppress(Exception):  # variable may not exist
            api.delete_space_variable(args.space, "PULSE_CHAOS")

    sha = os.environ.get("CI_COMMIT_SHORT_SHA", "local")
    with tempfile.TemporaryDirectory() as tmp:
        ctx = Path(tmp) / "ctx"
        ctx.mkdir()
        build_context(ctx, args.env, args.version, source)
        api.upload_folder(
            folder_path=str(ctx),
            repo_id=args.space,
            repo_type="space",
            commit_message=f"deploy pulse {args.version} ({args.env}) from {sha}",
            delete_patterns=["pulse/**"],
        )
    print(f"uploaded pulse {args.version} to {args.space}; url={url}")
    Path("deploy.env").write_text(
        f"DEPLOY_URL={url}\nDEPLOY_VERSION={args.version}\nDEPLOY_MODE=live\nDEPLOY_PROVIDER=huggingface\n",
        encoding="utf-8",
    )

    if args.wait > 0:
        if not wait_healthy(url, args.version, args.wait):
            print(f"ERROR: {url} did not become healthy with version {args.version} within {args.wait}s")
            return 1
        print(f"{args.env} healthy at {url} (version {args.version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

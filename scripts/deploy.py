"""Deploy dispatcher used by the deploy-staging / deploy-production jobs.

    python scripts/deploy.py --env staging --version 0.1.1 [--ref v0.1.0]

Picks the first available zero-cost provider:

1. **Render** (``RENDER_API_KEY`` set) — free web service, deploy by commit, rollback by tag.
2. **Hugging Face** (``HF_TOKEN`` set) — kept for completeness; Docker Spaces on the free CPU
   now require a paid plan (HTTP 402) and the HF deployer then degrades to a dry run.
3. **Dry run** — no provider configured: records the environment and exits 0.

``--ref`` (a release tag, from ``ROLLBACK_TO``) is resolved to its commit through the
Releases API so Render builds exactly the released code (AUTOPILOT §6.2).
Always writes ``deploy.env`` with DEPLOY_MODE=live|dry-run so verify-staging can skip
when nothing was deployed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def resolve_ref(ref: str) -> str:
    """Release tag -> commit sha (Releases API accepts the job token); a sha passes through."""
    if len(ref) >= 7 and all(c in "0123456789abcdef" for c in ref.lower()):
        return ref
    from gitlab_api import GitLab

    rel = GitLab().get(f"releases/{ref}")
    sha = (rel.get("commit") or {}).get("id")
    if not sha:
        raise SystemExit(f"release {ref} has no commit")
    return sha


def write_dry_run(env: str, version: str) -> None:
    url = os.environ.get(f"PULSE_{env.upper()}_URL", "")
    Path("deploy.env").write_text(
        f"DEPLOY_URL={url}\nDEPLOY_VERSION={version}\nDEPLOY_MODE=dry-run\nDEPLOY_PROVIDER=none\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", required=True, choices=["staging", "production"])
    ap.add_argument("--version", required=True)
    ap.add_argument("--ref", default=os.environ.get("ROLLBACK_TO") or os.environ.get("CI_COMMIT_SHA", ""))
    ap.add_argument("--wait", type=float, default=600)
    args = ap.parse_args(argv)

    if os.environ.get("RENDER_API_KEY"):
        import deploy_render

        commit = resolve_ref(args.ref) if args.ref else None
        print(f"provider: render (ref {args.ref} -> {commit})")
        return deploy_render.main(
            ["--env", args.env, "--version", args.version, "--wait", str(args.wait)]
            + (["--commit", commit] if commit else [])
        )

    if os.environ.get("HF_TOKEN"):
        import deploy_hf

        space = (
            os.environ.get(f"HF_{args.env.upper()}_SPACE")
            or f"example/pulse-{'prod' if args.env == 'production' else 'staging'}"
        )
        print(f"provider: huggingface ({space})")
        rc = deploy_hf.main(["--env", args.env, "--space", space, "--version", args.version, "--wait", str(args.wait)])
        text = Path("deploy.env").read_text(encoding="utf-8") if Path("deploy.env").exists() else ""
        if "DEPLOY_MODE=" not in text:
            Path("deploy.env").write_text(text + "DEPLOY_MODE=dry-run\nDEPLOY_PROVIDER=huggingface\n", encoding="utf-8")
        return rc

    print(f"DRY RUN: no deploy provider configured (set RENDER_API_KEY); {args.env} {args.version} recorded only")
    write_dry_run(args.env, args.version)
    return 0


if __name__ == "__main__":
    sys.exit(main())

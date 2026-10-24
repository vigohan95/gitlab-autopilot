"""Generate RELEASE_NOTES.md and version.env for the release job (AUTOPILOT §5).

Reads ``pulse.__version__``, finds the previous ``v*`` tag and lists the commits
since then. Never invents changes (§5.3). A release is cut only when no
``v<version>`` tag exists yet.

In CI this uses the GitLab REST API only (Tags + Compare endpoints), so it runs
in the slim Python image without a ``git`` binary. Locally (no ``CI_PROJECT_ID``)
it falls back to ``git`` when available.

Outputs (dotenv) : VERSION, RELEASE_TAG, RELEASE_NEEDED (true/false), PREVIOUS_TAG
Outputs (file)   : RELEASE_NOTES.md
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# the project being released: the CI checkout, else the current directory (local use)
REPO = Path(os.environ.get("CI_PROJECT_DIR") or Path.cwd())


def project_version() -> str:
    """The version to release, in order: ``--version`` / ``AUTOPILOT_VERSION``, a ``VERSION`` file in the project,
    the ``pulse`` sample app of this repository, this package's own version."""
    env = os.environ.get("AUTOPILOT_VERSION")
    if env:
        return env.strip()
    vf = REPO / "VERSION"
    if vf.exists():
        return vf.read_text(encoding="utf-8").strip()
    try:
        sys.path.insert(0, str(REPO))
        from pulse import __version__ as app_version

        return app_version
    except ImportError:
        pass
    try:
        from . import __version__ as own_version
    except ImportError:
        from __init__ import __version__ as own_version  # scripts/ on sys.path
    return own_version


__version__ = project_version()  # the version this checkout would release (kept for callers that read it)

MR_RE = re.compile(r"!(\d+)")
ISSUE_RE = re.compile(r"#(\d+)")


# -- git fallback (local use only) ---------------------------------------------
def _git(*args: str) -> str:
    if not shutil.which("git"):
        return ""
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=False).stdout.strip()  # noqa: S607


def _git_tags() -> list[str]:
    return [t for t in _git("tag", "--list", "v*", "--sort=-v:refname").splitlines() if t]


def _git_commits_since(ref: str | None) -> list[str]:
    rng = f"{ref}..HEAD" if ref else "HEAD"
    return [ln for ln in _git("log", "--no-merges", "--pretty=format:%s", rng).splitlines() if ln.strip()]


# -- GitLab API path (CI) ------------------------------------------------------
def _api_tags() -> list[str]:
    """Release tag names, newest first.

    The Releases API accepts CI_JOB_TOKEN for the running project (it is in the job-token
    allowlist); the Tags API may not, so it is only a fallback. Raises if neither works.
    """
    from gitlab_api import GitLab

    gl = GitLab()
    try:
        names = [r["tag_name"] for r in gl.get_all("releases")]
    except Exception as exc:  # noqa: BLE001
        print(f"::warning:: releases API unavailable ({type(exc).__name__}); trying tags API")
        tags = gl.get_all("repository/tags", search="^v", order_by="version", sort="desc")
        names = [t["name"] for t in tags]
    semver = [n for n in names if re.fullmatch(r"v\d+\.\d+\.\d+", n)]
    return sorted(semver, key=lambda n: tuple(int(x) for x in n[1:].split(".")), reverse=True)


def _api_commits_since(prev: str | None, sha: str) -> list[str]:
    """Commit titles since ``prev``; falls back to the current commit title when the
    Repository API refuses the job token."""
    from gitlab_api import GitLab

    gl = GitLab()
    try:
        if prev:
            cmp = gl.get("repository/compare", **{"from": prev, "to": sha})
            commits = cmp.get("commits", [])
        else:
            commits = gl.get("repository/commits", ref_name=sha, per_page=50)
        return [c["title"] for c in commits if len(c.get("parent_ids", [])) < 2]
    except Exception as exc:  # noqa: BLE001
        print(f"::warning:: commit list unavailable ({type(exc).__name__}); using CI_COMMIT_TITLE")
        title = os.environ.get("CI_COMMIT_TITLE", "").strip()
        return [title] if title else []


def render(version: str, prev: str | None, commits: list[str]) -> str:
    lines = [f"## pulse v{version}", ""]
    lines.append(f"Changes since `{prev}`:" if prev else "First release.")
    lines.append("")
    if not commits:
        lines.append("- (no commits listed)")
    for c in commits:
        lines.append(f"- {c}")
    mrs = sorted({m for c in commits for m in MR_RE.findall(c)}, key=int)
    issues = sorted({i for c in commits for i in ISSUE_RE.findall(c)}, key=int)
    lines += [""]
    if mrs:
        lines.append("Merge requests: " + ", ".join(f"!{m}" for m in mrs))
    if issues:
        lines.append("Issues: " + ", ".join(f"#{i}" for i in issues))
    lines += ["", "Released automatically by the Autopilot pipeline. autopilot: rules=[§5.1, §5.3]", ""]
    return "\n".join(lines)


def decide(tags: list[str], version: str) -> tuple[str, bool, str | None]:
    """Return (tag, release_needed, previous_tag). ``tags`` newest-first."""
    tag = f"v{version}"
    exists = tag in tags
    others = [t for t in tags if t != tag]
    return tag, not exists, (others[0] if others else None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", help="version to release (default: AUTOPILOT_VERSION, VERSION file, pulse, own)")
    args = ap.parse_args(argv if argv is not None else [])
    version = args.version or project_version()
    in_ci = bool(os.environ.get("CI_PROJECT_ID"))
    sha = os.environ.get("CI_COMMIT_SHA", "HEAD")
    source = "gitlab-api" if in_ci else "git"
    try:
        tags = _api_tags() if in_ci else _git_tags()
        tag, needed, prev = decide(tags, version)
        commits = _api_commits_since(prev, sha) if in_ci else _git_commits_since(prev)
    except Exception as exc:  # noqa: BLE001 - never block the pipeline, never re-release blindly
        print(f"::warning:: tags unavailable via {source} ({type(exc).__name__}: {exc}); RELEASE_NEEDED=false")
        tag, needed, prev, commits = f"v{version}", False, None, []

    notes = render(version, prev, commits)
    Path("RELEASE_NOTES.md").write_text(notes, encoding="utf-8")
    needed_s = "true" if needed else "false"
    Path("version.env").write_text(
        f"VERSION={version}\nRELEASE_TAG={tag}\nRELEASE_NEEDED={needed_s}\nPREVIOUS_TAG={prev or ''}\n",
        encoding="utf-8",
    )
    print(Path("version.env").read_text(encoding="utf-8"))
    print(notes)
    return 0


def cli() -> int:
    """Console entry point (``autopilot-release-notes``) and ``python scripts/release_notes.py``."""
    return main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(cli())

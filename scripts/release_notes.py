"""Generate RELEASE_NOTES.md and version.env for the release job (AUTOPILOT §5).

Reads ``pulse.__version__``, finds the previous ``v*`` tag and lists the commits
(with merge-request and issue references) since then. Never invents changes
(§5.3). Also decides whether a release is needed: a release is cut only when no
``v<version>`` tag exists yet.

Outputs (dotenv) : VERSION, RELEASE_NEEDED (true/false), PREVIOUS_TAG
Outputs (file)   : RELEASE_NOTES.md
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pulse import __version__  # noqa: E402

MR_RE = re.compile(r"!(\d+)")
ISSUE_RE = re.compile(r"#(\d+)")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()


def previous_tag(current: str) -> str | None:
    tags = [t for t in git("tag", "--list", "v*", "--sort=-v:refname").splitlines() if t and t != current]
    return tags[0] if tags else None


def commits_since(ref: str | None) -> list[str]:
    rng = f"{ref}..HEAD" if ref else "HEAD"
    out = git("log", "--no-merges", "--pretty=format:%s", rng)
    return [ln for ln in out.splitlines() if ln.strip()]


def render(version: str, prev: str | None, commits: list[str]) -> str:
    lines = [f"## pulse v{version}", ""]
    lines.append(f"Changes since `{prev}`:" if prev else "First release.")
    lines.append("")
    if not commits:
        lines.append("- (no commits listed)")
    for c in commits:
        c = MR_RE.sub(r"!\1", c)
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


def main() -> int:
    version = __version__
    tag = f"v{version}"
    exists = bool(git("tag", "--list", tag))
    prev = previous_tag(tag)
    notes = render(version, prev, commits_since(prev))
    needed = "false" if exists else "true"
    Path("RELEASE_NOTES.md").write_text(notes, encoding="utf-8")
    Path("version.env").write_text(
        f"VERSION={version}\nRELEASE_TAG={tag}\nRELEASE_NEEDED={needed}\nPREVIOUS_TAG={prev or ''}\n",
        encoding="utf-8",
    )
    print(Path("version.env").read_text())
    print(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())

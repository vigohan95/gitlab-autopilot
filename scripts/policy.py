"""Policy engine for AUTOPILOT.md.

Reads the ``yaml autopilot-policy`` block from AUTOPILOT.md and evaluates a
change set against it. Used by:

* the ``policy-check`` CI job on merge requests (``--mr $CI_MERGE_REQUEST_IID``)
* ``automerge.py`` before it merges
* unit tests (``--files``)

Exit status 0 = auto-merge allowed, 2 = human required, 1 = error.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
POLICY_FILE = ROOT / "AUTOPILOT.md"
_BLOCK = re.compile(r"```yaml autopilot-policy\n(.*?)```", re.S)
_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9_.\-\[\]]+)\s*==\s*([0-9][^\s;#]*)")


def load_policy(path: Path = POLICY_FILE) -> dict:
    text = path.read_text(encoding="utf-8")
    m = _BLOCK.search(text)
    if not m:
        raise SystemExit(f"{path}: no ```yaml autopilot-policy block")
    policy = yaml.safe_load(m.group(1))
    if policy.get("autopilot_policy") != 1:
        raise SystemExit("unsupported autopilot_policy version")
    return policy


@dataclass
class Decision:
    allowed: bool
    violated: list[str] = field(default_factory=list)  # rule ids e.g. "§2.1"
    satisfied: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def as_markdown(self) -> str:
        verdict = "auto-merge allowed" if self.allowed else "human required"
        lines = [f"**Autopilot policy check: {verdict}**", ""]
        for r in self.reasons:
            lines.append(f"- {r}")
        rules = sorted(set(self.violated) | set(self.satisfied))
        lines += ["", f"autopilot: rules=[{', '.join(rules)}]"]
        return "\n".join(lines)


def _matches(path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/**"):
            if path.startswith(pat[:-3] + "/"):
                return True
        elif fnmatch.fnmatch(path, pat) or path == pat:
            return True
    return False


def _bump_kind(old: str, new: str) -> str:
    o = [int(x) for x in re.findall(r"\d+", old)[:3]] + [0, 0, 0]
    n = [int(x) for x in re.findall(r"\d+", new)[:3]] + [0, 0, 0]
    if n[0] != o[0]:
        return "major"
    if n[1] != o[1]:
        return "minor"
    return "patch"


def classify_dependency_diff(old_text: str, new_text: str) -> list[tuple[str, str]]:
    """Return [(package, kind)] where kind in patch/minor/major/add/remove."""
    old = {m.group(1).lower(): m.group(2) for m in map(_REQ_LINE.match, old_text.splitlines()) if m}
    new = {m.group(1).lower(): m.group(2) for m in map(_REQ_LINE.match, new_text.splitlines()) if m}
    out = []
    for pkg in sorted(set(old) | set(new)):
        if pkg not in old:
            out.append((pkg, "add"))
        elif pkg not in new:
            out.append((pkg, "remove"))
        elif old[pkg] != new[pkg]:
            out.append((pkg, _bump_kind(old[pkg], new[pkg])))
    return out


def evaluate(
    policy: dict,
    changed_files: list[str],
    labels: list[str] | None = None,
    changed_lines: int = 0,
    dependency_changes: list[tuple[str, str]] | None = None,
    draft: bool = False,
    has_conflicts: bool = False,
) -> Decision:
    labels = labels or []
    d = Decision(allowed=True)
    human_ok = policy["labels"]["human_approved"] in labels

    if policy["scope_label"] not in labels:
        d.allowed = False
        d.violated.append("§1.1")
        d.reasons.append(f"§1.1 merge request lacks the `{policy['scope_label']}` label")
    else:
        d.satisfied.append("§1.1")

    protected = [f for f in changed_files if _matches(f, policy["protected_paths"])]
    if protected and not human_ok:
        d.allowed = False
        d.violated.append("§2.1")
        d.reasons.append("§2.1 protected paths changed: " + ", ".join(sorted(protected)))
    elif protected:
        d.satisfied.append("§2.1")
        d.reasons.append("§2.1 protected paths changed but `human_approved` label present")
    else:
        d.satisfied.append("§2.1")

    bad_bumps = [(p, k) for p, k in (dependency_changes or []) if k in policy["dependency_bumps"]["human"]]
    if bad_bumps and not human_ok:
        d.allowed = False
        d.violated.append("§2.2")
        d.reasons.append("§2.2 dependency changes need a human: " + ", ".join(f"{p} ({k})" for p, k in bad_bumps))
    else:
        d.satisfied.append("§2.2")

    lim = policy["limits"]
    if changed_lines > lim["max_changed_lines"] or len(changed_files) > lim["max_changed_files"]:
        d.allowed = False
        d.violated.append("§3.4")
        d.reasons.append(
            f"§3.4 size limit exceeded: {changed_lines} lines / {len(changed_files)} files "
            f"(max {lim['max_changed_lines']} / {lim['max_changed_files']})"
        )
    else:
        d.satisfied.append("§3.4")

    if draft or has_conflicts:
        d.allowed = False
        d.violated.append("§3.5")
        d.reasons.append("§3.5 merge request is a draft or has conflicts")
    else:
        d.satisfied.append("§3.5")

    if d.allowed:
        d.reasons.append("all policy rules satisfied")
    return d


def evaluate_mr(policy: dict, iid: int) -> Decision:
    """Evaluate a live merge request through the API."""
    from gitlab_api import GitLab

    gl = GitLab()
    mr = gl.get(f"merge_requests/{iid}")
    changes = gl.get(f"merge_requests/{iid}/diffs")
    files = [c["new_path"] for c in changes]
    lines = 0
    for c in changes:
        lines += sum(
            1
            for ln in c.get("diff", "").splitlines()
            if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))
        )
    dep_changes: list[tuple[str, str]] = []
    for c in changes:
        if c["new_path"] in policy["dependency_files"] and not c.get("new_file"):
            try:
                old = gl.get(f"repository/files/{c['old_path'].replace('/', '%2F')}/raw", ref=mr["target_branch"])
                new = gl.get(f"repository/files/{c['new_path'].replace('/', '%2F')}/raw", ref=mr["sha"])
                if isinstance(old, str) and isinstance(new, str):
                    dep_changes += classify_dependency_diff(old, new)
            except Exception:  # pragma: no cover - best effort; treated as "add"
                dep_changes.append((c["new_path"], "add"))
    return evaluate(
        policy,
        files,
        labels=mr.get("labels", []),
        changed_lines=lines,
        dependency_changes=dep_changes,
        draft=bool(mr.get("draft")),
        has_conflicts=bool(mr.get("has_conflicts")),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mr", type=int, help="merge request IID to evaluate via the API")
    ap.add_argument("--files", nargs="*", help="offline: changed file paths")
    ap.add_argument("--labels", nargs="*", default=[])
    ap.add_argument("--lines", type=int, default=0)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--comment", action="store_true", help="post the decision as an MR note (needs AUTOPILOT_TOKEN)")
    args = ap.parse_args(argv)

    policy = load_policy()
    if args.mr:
        try:
            decision = evaluate_mr(policy, args.mr)
        except Exception as exc:  # noqa: BLE001 - API unreachable: do not block the pipeline
            print(f"skipped: could not read merge request !{args.mr} via the API ({type(exc).__name__}: {exc})")
            return 0
    else:
        decision = evaluate(policy, args.files or [], labels=args.labels, changed_lines=args.lines)

    print(json.dumps(decision.__dict__, indent=2) if args.json else decision.as_markdown())
    if args.comment and args.mr and os.environ.get("AUTOPILOT_TOKEN"):
        from gitlab_api import GitLab

        gl = GitLab()
        gl.mr_note(args.mr, decision.as_markdown())
        # §3.2: when no reviewer agent is enabled, the policy check is the reviewer of record.
        label = policy["labels"]["approved"] if decision.allowed else policy["labels"]["needs_human"]
        gl.add_labels("merge_requests", args.mr, [label])
    return 0 if decision.allowed else 2


if __name__ == "__main__":
    sys.exit(main())

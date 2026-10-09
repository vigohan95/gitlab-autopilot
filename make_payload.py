"""Build a GitLab "commit with actions" payload from the git tree.

The first push to GitLab is done from a browser session (no personal access token
on this machine), with one REST call:

    POST /api/v4/projects/:id/repository/commits   (X-CSRF-Token from the page)

This script writes ``dist/commit_payload.json`` with every tracked file as a
``create`` (or ``update``) action. Text files are sent as UTF-8 text, anything
else as base64. Binaries over 200 KB are refused (keep images out of the repo).

    python make_payload.py                       # create actions, branch main
    python make_payload.py --action update       # when the files already exist
    python make_payload.py --message "..." --branch main --out dist/commit_payload.json
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAX_BINARY = 200 * 1024


def changed_since(commit: str) -> list[tuple[str, str]]:
    """[(status, path)] from ``git diff --name-status <commit> HEAD`` (A/M/D; renames become D + A)."""
    cmd = ["git", "diff", "--name-status", "-M", "-z", commit, "HEAD"]
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, check=True).stdout.decode("utf-8")  # noqa: S603, S607
    parts = [p for p in out.split("\0") if p != ""]
    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(parts):
        status = parts[i]
        if status.startswith(("R", "C")):
            old, new = parts[i + 1], parts[i + 2]
            if status.startswith("R"):
                pairs.append(("D", old))
            pairs.append(("A", new))
            i += 3
        else:
            pairs.append((status[0], parts[i + 1]))
            i += 2
    return pairs


def file_action(action: str, rel: str) -> dict:
    if action == "delete":
        return {"action": "delete", "file_path": rel}
    data = (ROOT / rel).read_bytes()
    try:
        content = data.decode("utf-8").replace("\r\n", "\n")
        if "\0" in content:
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "binary")
        return {"action": action, "file_path": rel, "content": content, "encoding": "text"}
    except UnicodeDecodeError:
        if len(data) > MAX_BINARY:
            raise SystemExit(f"{rel}: binary file larger than 200 KB; remove it from the tree") from None
        return {
            "action": action,
            "file_path": rel,
            "content": base64.b64encode(data).decode("ascii"),
            "encoding": "base64",
        }


def build_since(commit: str, branch: str, message: str) -> dict:
    actions = []
    for status, rel in changed_since(commit):
        actions.append(file_action({"A": "create", "M": "update", "D": "delete"}.get(status, "update"), rel))
    return {"branch": branch, "commit_message": message, "actions": actions}


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout  # noqa: S607
    return [p for p in out.decode("utf-8").split("\0") if p]


def build(action: str, branch: str, message: str) -> dict:
    actions = []
    text_bytes = bin_bytes = 0
    for rel in tracked_files():
        data = (ROOT / rel).read_bytes()
        try:
            content = data.decode("utf-8").replace("\r\n", "\n")  # always push LF
            if "\0" in content:
                raise UnicodeDecodeError("utf-8", b"", 0, 1, "binary")
            actions.append({"action": action, "file_path": rel, "content": content, "encoding": "text"})
            text_bytes += len(data)
        except UnicodeDecodeError:
            if len(data) > MAX_BINARY:
                raise SystemExit(f"{rel}: binary file larger than 200 KB; remove it from the tree") from None
            actions.append(
                {
                    "action": action,
                    "file_path": rel,
                    "content": base64.b64encode(data).decode("ascii"),
                    "encoding": "base64",
                }
            )
            bin_bytes += len(data)
    payload = {"branch": branch, "commit_message": message, "actions": actions}
    payload["_stats"] = {"files": len(actions), "text_bytes": text_bytes, "binary_bytes": bin_bytes}
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--action", default="create", choices=["create", "update"])
    ap.add_argument("--branch", default="main")
    ap.add_argument(
        "--message", default="Autopilot: pulse service, DevSecOps pipeline, Duo flows and agents, policy as code"
    )
    ap.add_argument("--since", help="commit: emit only files changed since it (create/update/delete)")
    ap.add_argument("--out", help="default dist/commit_payload.json, or dist/update_payload.json with --since")
    args = ap.parse_args(argv)

    if args.since:
        payload = build_since(args.since, args.branch, args.message)
        n_text = sum(len(a.get("content", "")) for a in payload["actions"] if a.get("encoding") == "text")
        stats = {"files": len(payload["actions"]), "text_bytes": n_text, "binary_bytes": 0}
        for a in payload["actions"]:
            print(f"  {a['action']:<6} {a['file_path']}")
    else:
        payload = build(args.action, args.branch, args.message)
        stats = payload.pop("_stats")
    out = ROOT / (args.out or ("dist/update_payload.json" if args.since else "dist/commit_payload.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    size = out.stat().st_size
    kb_text, kb_bin = stats["text_bytes"] / 1024, stats["binary_bytes"] / 1024
    print(f"{out}: {stats['files']} files, {size / 1024:.0f} KB JSON ({kb_text:.0f} KB text, {kb_bin:.0f} KB binary)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

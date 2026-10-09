"""Check out selected paths of a git ref without a git binary (used by rollback, AUTOPILOT §6.2).

    python scripts/fetch_ref.py --ref v0.1.0 --paths pulse Dockerfile requirements.txt

Downloads ``GET /projects/:id/repository/archive.tar.gz?sha=<ref>`` through the
GitLab API (job token, falling back to anonymous access on a public project) and
extracts only the requested top-level paths into the current directory,
replacing what is there. Exit 1 if the ref cannot be fetched.
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import sys
import tarfile
from pathlib import Path

from gitlab_api import GitLab


def extract_paths(archive: bytes, paths: list[str], dest: Path) -> list[str]:
    """Extract ``paths`` (top-level files/dirs) from a GitLab archive into ``dest``."""
    written: list[str] = []
    wanted = set(paths)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        members = tar.getmembers()
        if not members:
            return written
        prefix = members[0].name.split("/", 1)[0]  # "<project>-<ref>-<sha>"
        for p in paths:
            target = dest / p
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        for m in members:
            rel = m.name[len(prefix) + 1 :] if m.name.startswith(prefix + "/") else None
            if not rel:
                continue
            top = rel.split("/", 1)[0]
            if top not in wanted:
                continue
            if m.isdir():
                (dest / rel).mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                out = dest / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                f = tar.extractfile(m)
                if f is None:  # pragma: no cover
                    continue
                out.write_bytes(f.read())
                written.append(rel)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--paths", nargs="+", required=True)
    ap.add_argument("--dest", default=".")
    args = ap.parse_args(argv)

    gl = GitLab()
    try:
        archive = gl.get_bytes("repository/archive.tar.gz", sha=args.ref)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: could not download archive for {args.ref}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    written = extract_paths(archive, args.paths, Path(args.dest).resolve())
    print(f"checked out {len(written)} files from {args.ref} into {os.path.abspath(args.dest)}")
    for w in written:
        print(f"  {w}")
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())

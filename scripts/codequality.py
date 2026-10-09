"""Convert ``ruff check --output-format json`` into GitLab's Code Quality report format.

ruff check --output-format json . > ruff.json || true
python scripts/codequality.py ruff.json > codequality.json
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

SEVERITY = {"E": "major", "F": "critical", "B": "major", "S": "critical", "I": "minor", "UP": "minor"}


def convert(ruff_items: list[dict], root: Path) -> list[dict]:
    out = []
    for it in ruff_items:
        code = it.get("code") or "RUFF"
        prefix = next((p for p in sorted(SEVERITY, key=len, reverse=True) if code.startswith(p)), "")
        try:
            path = str(Path(it["filename"]).resolve().relative_to(root)).replace("\\", "/")
        except ValueError:
            path = it["filename"].replace("\\", "/")
        line = it["location"]["row"]
        fp = hashlib.sha1(f"{path}:{line}:{code}".encode()).hexdigest()  # noqa: S324 - not security
        out.append(
            {
                "description": f"{code}: {it['message']}",
                "check_name": code,
                "fingerprint": fp,
                "severity": SEVERITY.get(prefix, "minor"),
                "location": {"path": path, "lines": {"begin": line}},
            }
        )
    return out


def main(argv: list[str]) -> int:
    src = Path(argv[1]) if len(argv) > 1 else None
    raw = src.read_text(encoding="utf-8") if src else sys.stdin.read()
    items = json.loads(raw or "[]")
    json.dump(convert(items, Path.cwd().resolve()), sys.stdout, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

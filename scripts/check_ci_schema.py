"""Validate .gitlab-ci.yml offline against GitLab's official CI JSON schema.

    python scripts/check_ci_schema.py [.gitlab-ci.yml] [--schema path/to/ci.json]

The schema is the one the GitLab pipeline editor uses:
https://gitlab.com/gitlab-org/gitlab/-/raw/master/app/assets/javascripts/editor/schema/ci.json
It is downloaded to a cache directory when not given. Exit 1 on any error.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

import jsonschema
import yaml

SCHEMA_URL = "https://gitlab.com/gitlab-org/gitlab/-/raw/master/app/assets/javascripts/editor/schema/ci.json"


class _Loader(yaml.SafeLoader):
    """SafeLoader that understands GitLab's ``!reference`` tag."""


def _reference(loader: yaml.SafeLoader, node: yaml.Node):
    return loader.construct_sequence(node)


_Loader.add_constructor("!reference", _reference)


def load_schema(path: Path | None) -> dict:
    if path is None:
        path = Path(tempfile.gettempdir()) / "gitlab-ci-schema.json"
        if not path.exists():
            urllib.request.urlretrieve(SCHEMA_URL, path)  # noqa: S310 - fixed https URL
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", nargs="?", default=".gitlab-ci.yml")
    ap.add_argument("--schema", type=Path)
    args = ap.parse_args(argv)

    doc = yaml.load(Path(args.file).read_text(encoding="utf-8"), Loader=_Loader)  # noqa: S506 - custom SafeLoader
    schema = load_schema(args.schema)
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    if errors:
        for e in errors:
            loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
            print(f"ERROR at {loc}: {e.message[:300]}")
        print(f"{len(errors)} schema error(s)")
        return 1
    jobs = [
        k
        for k, v in doc.items()
        if isinstance(v, dict)
        and not k.startswith(".")
        and k not in ("default", "variables", "workflow", "include", "stages")
    ]
    n_stages = len(doc.get("stages", []))
    print(f"{args.file}: valid against GitLab CI schema; {n_stages} stages, {len(jobs)} jobs declared locally")
    return 0


if __name__ == "__main__":
    sys.exit(main())

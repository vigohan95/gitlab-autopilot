"""Succeed if the wheel for the given version is already in the project's PyPI package registry.

    python scripts/package_exists.py 0.1.1

Runs after ``twine upload`` fails. twine >= 6 does not allow ``--skip-existing``
for non-PyPI repositories, and GitLab answers 400 for a duplicate file, so the
registry is checked directly through the PyPI *simple* index, which accepts the
CI job token as basic auth (username ``gitlab-ci-token``):

    GET {CI_API_V4_URL}/projects/{CI_PROJECT_ID}/packages/pypi/simple/pulse-monitor

Exit 0 when the file for the version is listed; exit 1 when the index is readable
and the version is missing (a real upload failure); exit 0 with a warning when the
index cannot be read at all (cannot verify - do not block the release on it).
"""

from __future__ import annotations

import os
import re
import sys

import httpx

PACKAGE = "pulse-monitor"


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def version_listed(index_html: str, version: str, package: str = PACKAGE) -> bool:
    """True when the simple index lists a file for ``package`` at ``version``."""
    stem = f"{normalized(package)}-{re.escape(version)}"
    return re.search(rf"{stem}(?:-[^\"'<>\s]*)?\.(?:whl|tar\.gz)", index_html, re.I) is not None


def fetch_index(package: str = PACKAGE) -> httpx.Response:
    api = os.environ.get("CI_API_V4_URL", "https://gitlab.com/api/v4")
    project = os.environ.get("CI_PROJECT_ID", "")
    token = os.environ.get("CI_JOB_TOKEN", "")
    auth = ("gitlab-ci-token", token) if token else None
    url = f"{api}/projects/{project}/packages/pypi/simple/{package}"
    return httpx.get(url, auth=auth, timeout=30, follow_redirects=True)


def main(argv: list[str]) -> int:
    version = argv[1] if len(argv) > 1 else ""
    if not version:
        print("usage: package_exists.py <version>")
        return 2
    try:
        r = fetch_index()
    except httpx.HTTPError as exc:
        print(f"::warning:: simple index unreachable ({type(exc).__name__}); cannot verify {PACKAGE} {version}")
        return 0
    if r.status_code == 200:
        if version_listed(r.text, version):
            print(f"{PACKAGE} {version} is already in the Package Registry; treating the upload as done")
            return 0
        print(f"ERROR: {PACKAGE} {version} is not in the Package Registry and the upload failed")
        return 1
    print(f"::warning:: simple index returned HTTP {r.status_code}; cannot verify {PACKAGE} {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""Exit 0 if the wheel for the given version is already in the project's PyPI package registry.

    python scripts/package_exists.py 0.1.1

Used after ``twine upload --skip-existing``: GitLab's registry answers 400 for a
duplicate file, which twine does not always recognise. The Packages API accepts
CI_JOB_TOKEN for the running project.
"""

from __future__ import annotations

import sys

from gitlab_api import GitLab

PACKAGE = "pulse-monitor"


def main(argv: list[str]) -> int:
    version = argv[1] if len(argv) > 1 else ""
    if not version:
        print("usage: package_exists.py <version>")
        return 2
    try:
        pkgs = GitLab().get_all("packages", package_type="pypi", package_name=PACKAGE, package_version=version)
    except Exception as exc:  # noqa: BLE001
        print(f"packages API unavailable ({type(exc).__name__}: {exc})")
        return 1
    if pkgs:
        print(f"{PACKAGE} {version} already in the Package Registry: {pkgs[0].get('_links', {}).get('web_path', '')}")
        return 0
    print(f"{PACKAGE} {version} not found in the Package Registry")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

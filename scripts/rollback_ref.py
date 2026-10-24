"""The rollback branch convention (AUTOPILOT §6.2): ``rollback/<release tag>/incident-<iid>``.

A rollback pipeline must know two things: which release to deploy and which incident to report to. Until
2026-10-14 ``scripts/incident.py`` passed them as pipeline variables (``ROLLBACK_TO``, ``INCIDENT_IID``). In the
judged project that call was refused — "Insufficient permissions to set pipeline variables" — because the
project's *Minimum role to use pipeline variables* is above Developer and only a Maintainer can lower it
(docs/DRILL-2026-10-14.md, finding 3). Now both facts travel in the **branch name** instead:

* ``incident.py`` creates ``rollback/v0.1.2/incident-3`` from the commit the monitor ran on (so the pipeline uses
  the current ``.gitlab-ci.yml``) and starts a pipeline on it with no variables at all — Developer role suffices;
* the deploy jobs' rules match the branch prefix, and ``.deploy`` runs ``eval "$(python scripts/rollback_ref.py)"``,
  which exports ``ROLLBACK_TO`` and ``INCIDENT_IID``; ``deploy.py`` then builds the release tag's commit, not the
  branch's.

Without an incident (no ``AUTOPILOT_TOKEN``) the suffix is ``pipeline-<monitor pipeline id>`` so names never collide.
An explicit ``ROLLBACK_TO`` variable (a human with the right role pressing "Run pipeline") still wins.

    python scripts/rollback_ref.py            # prints shell exports for the current CI job, or nothing
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from collections.abc import Mapping

PREFIX = "rollback/"
# keep in sync with the `rollback/v[0-9]` rules in .gitlab-ci.yml (tests/test_scripts.py checks both)
_BRANCH = re.compile(r"^rollback/(v\d[^/]*)/(?:incident-(\d+)|pipeline-\d+)$")


def branch_name(tag: str, incident_iid: int | str | None = None, pipeline_id: int | str | None = None) -> str:
    """``rollback/<tag>/incident-<iid>``, or ``rollback/<tag>/pipeline-<id>`` when no incident exists."""
    if not re.fullmatch(r"v\d[^/\s]*", tag):
        raise ValueError(f"not a release tag: {tag!r}")
    if incident_iid:
        return f"{PREFIX}{tag}/incident-{int(incident_iid)}"
    return f"{PREFIX}{tag}/pipeline-{int(pipeline_id or 0)}"


def parse(branch: str | None) -> tuple[str, str | None] | None:
    """(release tag, incident iid or None) for a rollback branch; None for any other ref."""
    m = _BRANCH.match(branch or "")
    if not m:
        return None
    return m.group(1), m.group(2)


def current(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """(ROLLBACK_TO, INCIDENT_IID) for this job: explicit variables first, then the branch name; '' when absent."""
    env = os.environ if env is None else env
    tag, iid = env.get("ROLLBACK_TO", ""), env.get("INCIDENT_IID", "")
    parsed = parse(env.get("CI_COMMIT_BRANCH"))
    if parsed and not tag:
        tag, iid = parsed[0], iid or (parsed[1] or "")
    return tag, iid


def shell_exports(env: Mapping[str, str] | None = None) -> str:
    tag, iid = current(env)
    if not tag:
        return ""
    return f"export ROLLBACK_TO={shlex.quote(tag)} INCIDENT_IID={shlex.quote(iid)}"


def main(argv: list[str] | None = None) -> int:  # noqa: ARG001 - no options; eval "$(autopilot-rollback-ref)"
    out = shell_exports()
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

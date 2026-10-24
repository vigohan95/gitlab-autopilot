"""gitlab-autopilot — policy-as-code CI gate, incident and rollback automation for GitLab.

The modules in this package are the CI scripts of the Autopilot pipeline. They run two ways:

* installed (``pip install gitlab-autopilot``) as ``gitlab_autopilot.<module>`` with console entry points
  (``autopilot-policy``, ``autopilot-automerge``, ``autopilot-incident``, ...);
* straight from a checkout as ``python scripts/<module>.py`` (the way this repository's own pipeline runs them).
"""

__version__ = "0.2.0"

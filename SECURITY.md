# Security

Autopilot runs with a project access token (`AUTOPILOT_TOKEN`, scope `api`) and merges code on its own.
Treat a vulnerability in it as a vulnerability in every project that includes it.

## Reporting

Email the maintainer at the address on the GitHub profile of [vigohan95](https://github.com/vigohan95), or
open a [private vulnerability report](https://github.com/vigohan95/gitlab-autopilot/security/advisories/new)
on GitHub. Please do not open a public issue for a security problem. You will get an answer within 7 days.

## Scope

In scope: anything that lets a merge request bypass `AUTOPILOT.md` (protected paths, the scanner baseline,
the review-thread hold), anything that makes a rollback deploy the wrong release, token or secret exposure in
notes, artifacts or logs, and the GitLab API helper (`scripts/gitlab_api.py`).

Out of scope: the sample `pulse` service's own availability, GitLab.com itself, and the GitLab Duo flows'
model behaviour (report those to GitLab).

## What the scripts never do

- print `AUTOPILOT_TOKEN`, `CI_JOB_TOKEN` or any deploy key (see the docstring of `scripts/gitlab_api.py`);
- force-push, or merge with a red pipeline (AUTOPILOT §3.1, §3.6);
- roll forward: a rollback only ever targets an older release (AUTOPILOT §6.2).

## Supported versions

The latest minor release receives fixes. Pin `AUTOPILOT_VERSION` in your pipeline and upgrade deliberately.

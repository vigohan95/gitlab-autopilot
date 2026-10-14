"""Which jobs run in which pipeline: a small evaluator of `.gitlab-ci.yml` rules.

Found in the 2026-10-14 drill: scheduled pipeline 2945603793 ran `gemnasium-python-dependency_scanning` next to
`monitor` and `incident`, although docs/SCI.md and the README said the schedule was monitor-only; the template's
`$CI_COMMIT_BRANCH && …` rule matches every branch pipeline, rollback/<tag>/… branches included. These tests
evaluate the workflow rules and every locally defined job's rules for the five kinds of pipeline we run.

The evaluator covers the subset of GitLab's rules syntax this file uses (`==`, `!=`, `=~`, `!~`, `&&`, `||`,
parentheses, variable presence, `exists` globs, `when`, workflow `variables`); it is not a general one.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CI = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8"))

# Every analyzer job of the four included templates starts with `$<X>_DISABLED == 'true'` -> never.
SCANNER_SWITCHES = (
    "SAST_DISABLED",
    "SECRET_DETECTION_DISABLED",
    "DEPENDENCY_SCANNING_DISABLED",
    "CONTAINER_SCANNING_DISABLED",
)
# Template jobs that this repository matches and therefore overrides locally.
SCANNERS = ("semgrep-sast", "secret_detection", "gemnasium-python-dependency_scanning", "container_scanning")
# Template base jobs that are re-staged here but never run (`rules: - when: never` in the template).
TEMPLATE_BASES = ("sast", "dependency_scanning")

_TOKEN = re.compile(r"""\s*(\$\w+|"[^"]*"|'[^']*'|/(?:\\.|[^/\\])*/|==|!=|=~|!~|&&|\|\||\(|\)|null)""")


def _tokens(expr: str) -> list[str]:
    out, pos = [], 0
    while pos < len(expr.rstrip()):
        m = _TOKEN.match(expr, pos)
        assert m, f"cannot parse {expr[pos:]!r}"
        out.append(m.group(1))
        pos = m.end()
    return out


def evaluate(expr: str, env: dict) -> bool:
    toks = _tokens(expr)
    i = 0

    def value(tok):
        if tok.startswith("$"):
            return env.get(tok[1:])
        if tok == "null":
            return None
        if tok[0] in "\"'":
            return tok[1:-1]
        return tok  # a /regex/

    def primary():
        nonlocal i
        if toks[i] == "(":
            i += 1
            r = disj()
            assert toks[i] == ")"
            i += 1
            return r
        left = value(toks[i])
        i += 1
        if i < len(toks) and toks[i] in ("==", "!=", "=~", "!~"):
            op, right = toks[i], value(toks[i + 1])
            i += 2
            if op in ("==", "!="):
                return (left == right) == (op == "==")
            found = left is not None and re.search(right[1 : right.rindex("/")], left) is not None
            return found == (op == "=~")
        return bool(left)

    def conj():
        nonlocal i
        r = primary()
        while i < len(toks) and toks[i] == "&&":
            i += 1
            r = primary() and r
        return r

    def disj():
        nonlocal i
        r = conj()
        while i < len(toks) and toks[i] == "||":
            i += 1
            r = conj() or r
        return r

    result = disj()
    assert i == len(toks), f"trailing tokens in {expr!r}"
    return result


def _exists(patterns: list[str]) -> bool:
    for pattern in patterns:
        m = re.search(r"\{([^}]*)\}", pattern)
        alternatives = (
            [pattern[: m.start()] + a + pattern[m.end() :] for a in m.group(1).split(",")] if m else [pattern]
        )
        if any(any(ROOT.glob(a)) for a in alternatives):
            return True
    return False


def first_match(rules: list[dict], env: dict) -> dict | None:
    for rule in rules:
        if "if" in rule and not evaluate(rule["if"], env):
            continue
        if "exists" in rule and not _exists(rule["exists"]):
            continue
        return rule
    return None


def _rules(name: str) -> list[dict] | None:
    job = CI[name]
    while "rules" not in job and isinstance(job.get("extends"), str):
        job = CI[job["extends"]]
    return job.get("rules")


def pipeline(**predefined: str) -> tuple[dict, set[str]] | None:
    """(variables, jobs that run) for a pipeline, or None when the workflow rules create no pipeline."""
    env = {k: v for k, v in CI["variables"].items()}
    env.update({"CI_DEFAULT_BRANCH": "main", "GITLAB_FEATURES": "dependency_scanning,container_scanning,sast"})
    env.update(predefined)
    rule = first_match(CI["workflow"]["rules"], env)
    if rule is None or rule.get("when") == "never":
        return None
    env.update(rule.get("variables", {}))
    jobs = set()
    for name, job in CI.items():
        if name.startswith(".") or not isinstance(job, dict) or name in ("variables", "workflow", "default"):
            continue
        if name in TEMPLATE_BASES:
            continue
        rules = _rules(name)
        if rules is None:
            jobs.add(name)
            continue
        hit = first_match(rules, env)
        if hit is not None and hit.get("when") != "never":
            jobs.add(name)
    return env, jobs


SCHEDULE = {"CI_PIPELINE_SOURCE": "schedule", "CI_COMMIT_BRANCH": "main"}
MAIN = {"CI_PIPELINE_SOURCE": "push", "CI_COMMIT_BRANCH": "main"}
MR = {"CI_PIPELINE_SOURCE": "merge_request_event", "CI_MERGE_REQUEST_IID": "5", "CI_MERGE_REQUEST_LABELS": "autopilot"}
ROLLBACK_BRANCH = {"CI_PIPELINE_SOURCE": "trigger", "CI_COMMIT_BRANCH": "rollback/v0.1.1/incident-3"}
ROLLBACK_VARIABLE = {"CI_PIPELINE_SOURCE": "web", "CI_COMMIT_BRANCH": "main", "ROLLBACK_TO": "v0.1.1"}


def test_evaluator_reads_the_syntax_we_use():
    env = {"A": "x", "B": "", "L": "autopilot,security-fix"}
    assert evaluate('$A == "x" && $L =~ /(^|,)autopilot(,|$)/', env)
    assert not evaluate("$B", env) and not evaluate("$MISSING", env)
    assert evaluate("$B == '' || $A == 'y'", env)
    assert evaluate("($A == 'y' || $A == 'x') && $L !~ /\\bnope\\b/", env)
    assert evaluate("$C =~ /^rollback\\/v[0-9]/", {"C": "rollback/v0.1.1/incident-3"})
    assert _exists(["**/{requirements.txt,poetry.lock}"]) and not _exists(["**/{pom.xml,build.sbt}"])


def test_scheduled_pipeline_is_monitor_only():
    env, jobs = pipeline(**SCHEDULE)
    assert jobs == {"monitor", "incident"}
    assert all(env[s] == "true" for s in SCANNER_SWITCHES)  # every other template analyzer is off too


@pytest.mark.parametrize("source", ["trigger", "api", "web"])
def test_rollback_branch_pipeline_runs_no_scanner(source):
    env, jobs = pipeline(**{**ROLLBACK_BRANCH, "CI_PIPELINE_SOURCE": source})
    assert jobs == {"deploy-staging", "verify-staging", "deploy-production", "rollback-note", "audit-trail"}
    assert all(env[s] == "true" for s in SCANNER_SWITCHES)


def test_rollback_branch_push_creates_no_pipeline():
    assert pipeline(**{**ROLLBACK_BRANCH, "CI_PIPELINE_SOURCE": "push"}) is None


def test_hand_started_rollback_on_main_runs_no_scanner():
    env, jobs = pipeline(**ROLLBACK_VARIABLE)
    assert not jobs & set(SCANNERS)
    assert jobs == {"deploy-staging", "verify-staging", "deploy-production", "rollback-note", "audit-trail"}
    assert all(env[s] == "true" for s in SCANNER_SWITCHES)


@pytest.mark.parametrize("kind", [MAIN, MR], ids=["main", "merge_request"])
def test_scanners_still_run_on_main_and_merge_request_pipelines(kind):
    env, jobs = pipeline(**kind)
    assert set(SCANNERS) <= jobs
    assert not any(s in env for s in SCANNER_SWITCHES)


def test_gemnasium_keeps_the_template_conditions():
    # without the Ultimate feature (the Free-tier history project) it skips itself, as the template does
    _, jobs = pipeline(**MAIN, GITLAB_FEATURES="")
    assert "gemnasium-python-dependency_scanning" not in jobs
    _, jobs = pipeline(**MAIN, DEPENDENCY_SCANNING_DISABLED="true")
    assert "gemnasium-python-dependency_scanning" not in jobs

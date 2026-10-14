"""Which jobs run in which pipeline: a small evaluator of `.gitlab-ci.yml` rules.

Found in the 2026-10-14 drill: scheduled pipeline 2945603793 ran `gemnasium-python-dependency_scanning` next to
`monitor` and `incident`, although docs/SCI.md and the README said the schedule was monitor-only; the template's
`$CI_COMMIT_BRANCH && …` rule matches every branch pipeline, rollback/<tag>/… branches included. These tests
evaluate the workflow rules and every locally defined job's rules for the five kinds of pipeline we run.

The evaluator covers the subset of GitLab's rules syntax this file uses (`==`, `!=`, `=~`, `!~`, `&&`, `||`,
parentheses, variable presence, `exists` globs, `when`, workflow `variables`); it is not a general one.
"""

import os
import re
import shutil
import subprocess
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


# --------------------------------------------------------------- transient infrastructure failures (run 5) --
# 2026-10-14, run 5: MR pipeline 2945797046 failed at build-image on "unexpected status from HEAD request to
# <registry>: 500 Internal Server Error" and nothing retried it. Registry- and network-bound jobs now retry
# GitLab's infrastructure failure classes, and build-image wraps every docker call in scripts/registry_retry.sh.
INFRA_FAILURES = {
    "runner_system_failure",
    "stuck_or_timeout_failure",
    "api_failure",
    "unknown_failure",
    "scheduler_failure",
    "data_integrity_failure",
}
RETRY_SCRIPT = ROOT / "scripts" / "registry_retry.sh"


@pytest.mark.parametrize("name", ["build-image", "container_scanning", "build-wheel"])
def test_registry_bound_jobs_retry_infrastructure_failures_only(name):
    retry = CI[name]["retry"]
    assert retry["max"] == 2
    assert set(retry["when"]) == INFRA_FAILURES  # never script_failure: a red test or scanner error is not hidden


def test_every_docker_registry_call_in_build_image_goes_through_the_retry_wrapper():
    job = CI["build-image"]
    lines = [line for part in job["before_script"] + job["script"] for line in part.splitlines()]
    calls = [(line, m.start()) for line in lines for m in re.finditer(r"\bdocker (?:login|build|push)\b", line)]
    assert len(calls) == 6  # login, build, push sha, push ref slug, push latest, push v<version>
    assert not [line for line, at in calls if "registry_retry.sh" not in line[:at]]


def _run_retry(tmp_path, body: str, attempts: int = 3) -> tuple[int, str, int]:
    """Run registry_retry.sh around a fake command; returns (exit code, stderr, how often the command ran)."""
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("no POSIX sh")
    fake = tmp_path / "fake.sh"
    fake.write_text(
        'n=$(cat "$COUNTER" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$COUNTER"\n' + body,
        encoding="utf-8",
        newline="\n",
    )
    counter = tmp_path / "count"
    env = {
        **os.environ,
        "COUNTER": counter.as_posix(),
        "REGISTRY_RETRY_BACKOFF": "0",
        "REGISTRY_RETRY_ATTEMPTS": str(attempts),
    }
    proc = subprocess.run(  # noqa: S603 - fixed local script and a fake command written by the test
        [sh, RETRY_SCRIPT.as_posix(), sh, fake.as_posix()], capture_output=True, text=True, env=env, timeout=60
    )
    return proc.returncode, proc.stderr, int(counter.read_text().strip())


REGISTRY_500 = (
    'echo "error parsing HTTP 500 response body: unexpected status from HEAD request to '
    'https://registry.gitlab.com/v2/x/blobs/sha256:ab: 500 Internal Server Error"; exit 1'
)


def test_retry_wrapper_retries_the_run5_registry_500_and_then_succeeds(tmp_path):
    rc, err, runs = _run_retry(tmp_path, f'if [ "$n" -lt 2 ]; then {REGISTRY_500}; fi; echo pushed')
    assert (rc, runs) == (0, 2)
    assert "attempt 1/3 failed with a transient registry/network error" in err


@pytest.mark.parametrize(
    "message",
    ["net/http: TLS handshake timeout", "dial tcp 1.2.3.4:443: i/o timeout", "received unexpected HTTP status: 503"],
)
def test_retry_wrapper_gives_up_after_three_transient_failures(tmp_path, message):
    rc, err, runs = _run_retry(tmp_path, f'echo "{message}"; exit 7')
    assert (rc, runs) == (7, 3)
    assert "on all 3 attempts" in err


def test_retry_wrapper_does_not_retry_a_real_failure(tmp_path):
    rc, _, runs = _run_retry(tmp_path, 'echo "denied: requested access to the resource is denied"; exit 5')
    assert (rc, runs) == (5, 1)


def test_retry_wrapper_passes_success_through_once(tmp_path):
    rc, _, runs = _run_retry(tmp_path, "echo pushed")
    assert (rc, runs) == (0, 1)

"""The scripts must stay installable: every console entry point in pyproject.toml resolves to a callable, the
CI template only calls commands that exist, and the policy file is found from the working directory."""

from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
SCRIPTS = PYPROJECT["project"]["scripts"]


def test_distribution_is_gitlab_autopilot_and_maps_scripts_dir():
    assert PYPROJECT["project"]["name"] == "gitlab-autopilot"
    assert PYPROJECT["tool"]["setuptools"]["package-dir"]["gitlab_autopilot"] == "scripts"
    assert "gitlab_autopilot" in PYPROJECT["tool"]["setuptools"]["packages"]
    assert (ROOT / "scripts" / "__init__.py").exists()


def test_every_entry_point_resolves_to_a_callable():
    for name, target in SCRIPTS.items():
        module, attr = target.split(":")
        if module.startswith("gitlab_autopilot."):
            module = module.split(".", 1)[1]  # scripts/ is on sys.path as top-level modules in tests
        mod = importlib.import_module(module)
        assert callable(getattr(mod, attr)), f"{name} -> {target}"


def test_ci_template_uses_only_known_commands():
    doc = yaml.safe_load((ROOT / "templates" / "Autopilot.gitlab-ci.yml").read_text(encoding="utf-8"))
    jobs = {k: v for k, v in doc.items() if isinstance(v, dict) and "script" in v}
    assert len(jobs) == 10
    used = set()
    for job in jobs.values():
        for line in job["script"]:
            for word in str(line).split():
                if word.startswith("autopilot-"):
                    used.add(word.strip('"$()'))
    assert used <= set(SCRIPTS), used - set(SCRIPTS)
    assert {"autopilot-policy", "autopilot-automerge", "autopilot-incident", "autopilot-monitor"} <= used
    for job in jobs.values():  # reserved stages only: the adopter's stages list stays untouched
        assert job["stage"] in (".pre", ".post")
    assert "AUTOPILOT_VERSION" in doc["variables"]


def test_policy_file_resolves_from_cwd_then_env(tmp_path, monkeypatch):
    import policy

    monkeypatch.delenv("AUTOPILOT_POLICY_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    assert policy.default_policy_file() == ROOT / "AUTOPILOT.md"  # nothing in cwd: this checkout's policy
    (tmp_path / "AUTOPILOT.md").write_text(
        (ROOT / "templates" / "AUTOPILOT.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert policy.default_policy_file() == tmp_path / "AUTOPILOT.md"
    starter = policy.load_policy()
    assert starter["freeze_after"] is None and starter["scope_label"] == "autopilot"
    monkeypatch.setenv("AUTOPILOT_POLICY_FILE", str(ROOT / "AUTOPILOT.md"))
    assert policy.default_policy_file() == ROOT / "AUTOPILOT.md"


def test_starter_policy_holds_protected_paths_and_dependency_rules(tmp_path, monkeypatch):
    import policy

    monkeypatch.chdir(tmp_path)
    (tmp_path / "AUTOPILOT.md").write_text(
        (ROOT / "templates" / "AUTOPILOT.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    pol = policy.load_policy()
    held = policy.evaluate(pol, [".gitlab-ci.yml"], labels=["autopilot"], changed_lines=3)
    assert not held.allowed and "§2.1" in held.violated
    released = policy.evaluate(
        pol, [".gitlab-ci.yml"], labels=["autopilot", "autopilot::human-approved"], changed_lines=3
    )
    assert released.allowed
    ok = policy.evaluate(pol, ["src/app.py"], labels=["autopilot"], changed_lines=3)
    assert ok.allowed
    too_big = policy.evaluate(pol, ["src/app.py"], labels=["autopilot"], changed_lines=401)
    assert not too_big.allowed and "§3.4" in too_big.violated


def test_release_notes_version_sources(tmp_path, monkeypatch):
    import release_notes

    monkeypatch.setenv("AUTOPILOT_VERSION", "9.9.9")
    assert release_notes.project_version() == "9.9.9"
    monkeypatch.delenv("AUTOPILOT_VERSION")
    monkeypatch.setattr(release_notes, "REPO", tmp_path)
    (tmp_path / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    assert release_notes.project_version() == "1.2.3"
    (tmp_path / "VERSION").unlink()
    assert release_notes.project_version()  # falls through to the pulse sample app, else the package itself

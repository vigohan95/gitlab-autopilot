import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from policy import classify_dependency_diff, evaluate, load_policy  # noqa: E402

POLICY = load_policy()


def test_policy_block_parses_and_has_expected_rules():
    assert POLICY["scope_label"] == "autopilot"
    assert ".gitlab/**" in POLICY["protected_paths"]
    assert POLICY["limits"]["max_changed_lines"] == 400
    assert POLICY["rollback"]["never_roll_forward"] is True


def test_plain_code_change_is_allowed():
    d = evaluate(POLICY, ["pulse/app.py", "tests/test_app.py"], labels=["autopilot"], changed_lines=120)
    assert d.allowed and "§2.1" in d.satisfied and "§1.1" in d.satisfied
    assert d.as_markdown().startswith("**Autopilot policy check: auto-merge allowed**")
    assert "autopilot: rules=[" in d.as_markdown()


def test_missing_scope_label_blocks():
    d = evaluate(POLICY, ["pulse/app.py"], labels=[])
    assert not d.allowed and "§1.1" in d.violated


def test_protected_paths_need_human():
    d = evaluate(POLICY, ["Dockerfile"], labels=["autopilot"])
    assert not d.allowed and "§2.1" in d.violated
    d = evaluate(POLICY, [".gitlab/duo/flows/x.yaml"], labels=["autopilot"])
    assert not d.allowed and "§2.1" in d.violated
    d = evaluate(POLICY, [".gitlab-ci.yml"], labels=["autopilot", "autopilot::human-approved"])
    assert d.allowed and "§2.1" in d.satisfied


def test_size_limits():
    d = evaluate(POLICY, ["pulse/app.py"], labels=["autopilot"], changed_lines=401)
    assert not d.allowed and "§3.4" in d.violated
    many = [f"pulse/f{i}.py" for i in range(21)]
    d = evaluate(POLICY, many, labels=["autopilot"], changed_lines=10)
    assert not d.allowed and "§3.4" in d.violated


def test_draft_or_conflicts_block():
    assert not evaluate(POLICY, ["pulse/app.py"], labels=["autopilot"], draft=True).allowed
    assert not evaluate(POLICY, ["pulse/app.py"], labels=["autopilot"], has_conflicts=True).allowed


def test_dependency_bump_classification():
    old = "fastapi==0.115.0\nhttpx==0.27.0\nremoved==1.0\n"
    new = "fastapi==0.116.2\nhttpx==1.0.0\nadded==2.0\n"
    changes = dict(classify_dependency_diff(old, new))
    assert changes == {"fastapi": "minor", "httpx": "major", "removed": "remove", "added": "add"}


def test_dependency_rules():
    d = evaluate(POLICY, ["requirements.txt"], labels=["autopilot"], dependency_changes=[("fastapi", "patch")])
    assert d.allowed and "§2.2" in d.satisfied
    d = evaluate(POLICY, ["requirements.txt"], labels=["autopilot"], dependency_changes=[("httpx", "major")])
    assert not d.allowed and "§2.2" in d.violated
    d = evaluate(
        POLICY,
        ["requirements.txt"],
        labels=["autopilot", "autopilot::human-approved"],
        dependency_changes=[("httpx", "major")],
    )
    assert d.allowed


def test_freeze_after_is_enforced_only_after_the_deadline():
    from datetime import UTC, datetime

    from policy import freeze_after, freeze_note, is_frozen

    assert freeze_after(POLICY) == datetime(2026, 10, 27, 13, 0, tzinfo=UTC)
    before = datetime(2026, 10, 27, 12, 59, tzinfo=UTC)
    after = datetime(2026, 10, 27, 13, 0, tzinfo=UTC)
    assert not is_frozen(POLICY, before) and is_frozen(POLICY, after)

    d = evaluate(POLICY, ["pulse/app.py"], labels=["autopilot"], changed_lines=5, now=before)
    assert d.allowed and "§1.3" in d.satisfied

    d = evaluate(POLICY, ["pulse/app.py"], labels=["autopilot"], changed_lines=5, now=after)
    assert not d.allowed and d.frozen and d.violated == ["§1.3"]
    assert d.as_markdown().startswith("**Autopilot policy check: frozen for judging**")
    # a protected-path change after the freeze is not evaluated further: one note, no other verdicts
    d = evaluate(POLICY, ["Dockerfile"], labels=[], now=after)
    assert d.violated == ["§1.3"] and "§2.1" not in d.violated

    note = freeze_note(POLICY)
    assert "frozen for judging" in note and "2026-10-27 13:00 UTC" in note and note.endswith("autopilot: rules=[§1.3]")


def test_no_freeze_when_policy_has_no_value():
    from policy import is_frozen

    assert not is_frozen({**POLICY, "freeze_after": None})


def test_freeze_check_cli(monkeypatch, capsys):
    import policy as policy_mod

    monkeypatch.setattr(policy_mod, "is_frozen", lambda p, now=None: False)
    assert policy_mod.main(["--freeze-check"]) == 0 and capsys.readouterr().out.startswith("OPEN")
    monkeypatch.setattr(policy_mod, "is_frozen", lambda p, now=None: True)
    monkeypatch.delenv("AUTOPILOT_TOKEN", raising=False)
    assert policy_mod.main(["--freeze-check"]) == 3 and capsys.readouterr().out.startswith("FROZEN\n")

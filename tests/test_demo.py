import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import demo  # noqa: E402


def test_bump_patch():
    text, old, new = demo.bump_patch('"""doc"""\n\n__version__ = "0.1.9"\n')
    assert (old, new) == ("0.1.9", "0.1.10")
    assert '__version__ = "0.1.10"' in text
    with pytest.raises(ValueError):
        demo.bump_patch("nothing here")


def test_bump_patch_on_real_init():
    text, old, new = demo.bump_patch((Path(__file__).resolve().parent.parent / "pulse" / "__init__.py").read_text())
    assert text.count(new) == 1 and old != new


def test_pin_requirement_replace_and_add():
    req = "fastapi==0.143.0\nuvicorn[standard]==0.54.0\nhttpx==0.28.1\n"
    new, prev = demo.pin_requirement(req, "fastapi", "0.125.0")
    assert prev == "0.143.0" and "fastapi==0.125.0\n" in new and "0.143.0" not in new
    new, prev = demo.pin_requirement(req, "uvicorn", "0.30.0")
    assert prev == "0.54.0" and "uvicorn[standard]==0.30.0" in new
    new, prev = demo.pin_requirement(req, "starlette", "0.50.0")
    assert prev is None and new.endswith("starlette==0.50.0\n")


def test_main_skips_without_token(monkeypatch, capsys):
    monkeypatch.delenv("AUTOPILOT_TOKEN", raising=False)
    assert demo.main(["--project", "1", "status"]) == 0
    assert "skipped: AUTOPILOT_TOKEN not set" in capsys.readouterr().out

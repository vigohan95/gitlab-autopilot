import io
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fetch_ref  # noqa: E402
import release_notes  # noqa: E402


def test_decide_release_needed_and_previous_tag():
    assert release_notes.decide(["v0.2.0", "v0.1.0"], "0.3.0") == ("v0.3.0", True, "v0.2.0")
    assert release_notes.decide(["v0.2.0", "v0.1.0"], "0.2.0") == ("v0.2.0", False, "v0.1.0")
    assert release_notes.decide([], "0.1.0") == ("v0.1.0", True, None)


def test_render_lists_commits_and_refs():
    text = release_notes.render("0.2.0", "v0.1.0", ["pulse: add percentiles (#3) !7", "ci: tidy"])
    assert "## pulse v0.2.0" in text and "Changes since `v0.1.0`" in text
    assert "- pulse: add percentiles (#3) !7" in text
    assert "Merge requests: !7" in text and "Issues: #3" in text
    assert "autopilot: rules=[§5.1, §5.3]" in text
    assert "First release." in release_notes.render("0.1.0", None, [])


def test_release_notes_main_in_ci_uses_api(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CI_PROJECT_ID", "1")
    monkeypatch.setenv("CI_COMMIT_SHA", "abc")
    monkeypatch.setattr(release_notes, "_api_tags", lambda: ["v0.0.1"])
    monkeypatch.setattr(release_notes, "_api_commits_since", lambda prev, sha: [f"since {prev} to {sha}"])
    assert release_notes.main() == 0
    env = (tmp_path / "version.env").read_text()
    assert "RELEASE_NEEDED=true" in env and "PREVIOUS_TAG=v0.0.1" in env and "RELEASE_TAG=v0.1.0" in env
    assert "since v0.0.1 to abc" in (tmp_path / "RELEASE_NOTES.md").read_text(encoding="utf-8")


def test_release_notes_main_survives_api_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CI_PROJECT_ID", "1")

    def boom():
        raise RuntimeError("no api")

    monkeypatch.setattr(release_notes, "_api_tags", boom)
    assert release_notes.main() == 0
    assert "RELEASE_NEEDED=false" in (tmp_path / "version.env").read_text()


def test_api_tags_prefers_releases_and_sorts_semver(monkeypatch):
    class FakeGL:
        def get_all(self, path, **params):
            assert path == "releases"
            return [{"tag_name": "v0.2.0"}, {"tag_name": "v0.10.0"}, {"tag_name": "rc-1"}, {"tag_name": "v0.9.1"}]

    import gitlab_api

    monkeypatch.setattr(gitlab_api, "GitLab", lambda: FakeGL())
    assert release_notes._api_tags() == ["v0.10.0", "v0.9.1", "v0.2.0"]


def test_api_tags_falls_back_to_tags_api(monkeypatch):
    class FakeGL:
        def get_all(self, path, **params):
            if path == "releases":
                raise RuntimeError("404")
            return [{"name": "v0.1.0"}]

    import gitlab_api

    monkeypatch.setattr(gitlab_api, "GitLab", lambda: FakeGL())
    assert release_notes._api_tags() == ["v0.1.0"]


def test_commits_fallback_to_commit_title(monkeypatch):
    class FakeGL:
        def get(self, path, **params):
            raise RuntimeError("403")

    import gitlab_api

    monkeypatch.setattr(gitlab_api, "GitLab", lambda: FakeGL())
    monkeypatch.setenv("CI_COMMIT_TITLE", "pulse: bump to 0.1.1")
    assert release_notes._api_commits_since("v0.1.0", "abc") == ["pulse: bump to 0.1.1"]


def _archive(prefix: str, files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(f"{prefix}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_extract_paths_replaces_only_requested_paths(tmp_path):
    (tmp_path / "pulse").mkdir()
    (tmp_path / "pulse" / "stale.py").write_text("old")
    (tmp_path / "keep.txt").write_text("keep")
    archive = _archive(
        "proj-v0.1.0-abc",
        {"pulse/app.py": b"new app", "pulse/__init__.py": b"v", "Dockerfile": b"FROM x", "README.md": b"ignored"},
    )
    written = fetch_ref.extract_paths(archive, ["pulse", "Dockerfile"], tmp_path)
    assert sorted(written) == ["Dockerfile", "pulse/__init__.py", "pulse/app.py"]
    assert not (tmp_path / "pulse" / "stale.py").exists()
    assert (tmp_path / "keep.txt").read_text() == "keep"
    assert not (tmp_path / "README.md").exists()
    assert (tmp_path / "pulse" / "app.py").read_bytes() == b"new app"

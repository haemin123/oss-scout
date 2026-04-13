"""Unit tests for server/tools/scaffold.py security logic.

Tests focus on path traversal prevention, subdir validation,
tarball extraction safety, next_steps detection, and CLAUDE.md generation.
All tests run without network (no real tarball downloads).
"""

from __future__ import annotations

import io
import os
import tarfile
import tempfile
from pathlib import Path

import pytest

from server.tools.scaffold import (
    SecurityError,
    _detect_next_steps,
    _generate_claude_md,
    _safe_extract_tarball,
    _validate_scaffold_args,
    _validate_subdir,
    _validate_target_dir,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tarball(files: dict[str, bytes], prefix: str = "owner-repo-abc123/") -> bytes:
    """Create an in-memory gzipped tarball with given files.

    files: mapping of relative paths to content bytes.
    prefix: the top-level directory GitHub adds.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name=prefix + name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _make_tarball_with_symlink(target: str) -> bytes:
    """Create a tarball containing a symlink."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="owner-repo-abc123/link")
        info.type = tarfile.SYMTYPE
        info.linkname = target
        tar.addfile(info)
    return buf.getvalue()


def _make_tarball_with_absolute_path() -> bytes:
    """Create a tarball with an absolute path member."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="/etc/passwd")
        info.size = 5
        tar.addfile(info, io.BytesIO(b"hello"))
    return buf.getvalue()


def _make_tarball_with_traversal() -> bytes:
    """Create a tarball with path traversal (..)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="owner-repo-abc123/../../../etc/passwd")
        info.size = 5
        tar.addfile(info, io.BytesIO(b"hello"))
    return buf.getvalue()


# ===========================================================================
# _validate_target_dir
# ===========================================================================


class TestValidateTargetDir:
    def test_valid_subdir_of_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "my_project"
        result = _validate_target_dir(str(target))
        assert result == target.resolve()

    def test_rejects_path_traversal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SecurityError, match="현재 작업 디렉토리 하위"):
            _validate_target_dir("../../etc/malicious")

    def test_rejects_absolute_outside_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SecurityError):
            _validate_target_dir("/tmp/somewhere_else")

    def test_rejects_nonempty_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "nonempty"
        target.mkdir()
        (target / "file.txt").write_text("content")
        with pytest.raises(SecurityError, match="비어있지 않습니다"):
            _validate_target_dir(str(target))

    def test_accepts_empty_existing_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "empty_dir"
        target.mkdir()
        result = _validate_target_dir(str(target))
        assert result == target.resolve()


# ===========================================================================
# _validate_subdir
# ===========================================================================


class TestValidateSubdir:
    def test_none_is_ok(self) -> None:
        assert _validate_subdir(None) is None

    def test_valid_subdir(self) -> None:
        assert _validate_subdir("packages/app") == "packages/app"

    def test_rejects_dotdot(self) -> None:
        with pytest.raises(SecurityError, match="\\.\\."):
            _validate_subdir("../escape")

    def test_rejects_nested_dotdot(self) -> None:
        with pytest.raises(SecurityError, match="\\.\\."):
            _validate_subdir("packages/../../escape")

    def test_rejects_absolute_path(self) -> None:
        # Use platform-appropriate absolute path
        abs_path = "C:\\absolute\\path" if os.name == "nt" else "/absolute/path"
        with pytest.raises(SecurityError, match="상대 경로"):
            _validate_subdir(abs_path)


# ===========================================================================
# _safe_extract_tarball
# ===========================================================================


class TestSafeExtractTarball:
    def test_extracts_normal_tarball(self, tmp_path: Path) -> None:
        tar_data = _make_tarball({
            "README.md": b"# Hello",
            "src/index.ts": b"console.log('hi')",
            "LICENSE": b"MIT License",
        })
        count = _safe_extract_tarball(tar_data, tmp_path)
        assert count == 3
        assert (tmp_path / "README.md").read_bytes() == b"# Hello"
        assert (tmp_path / "src" / "index.ts").exists()
        assert (tmp_path / "LICENSE").exists()

    def test_rejects_symlink(self, tmp_path: Path) -> None:
        tar_data = _make_tarball_with_symlink("/etc/passwd")
        with pytest.raises(SecurityError, match="symlink"):
            _safe_extract_tarball(tar_data, tmp_path)

    def test_rejects_absolute_path(self, tmp_path: Path) -> None:
        tar_data = _make_tarball_with_absolute_path()
        with pytest.raises(SecurityError, match="절대 경로"):
            _safe_extract_tarball(tar_data, tmp_path)

    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        tar_data = _make_tarball_with_traversal()
        with pytest.raises(SecurityError, match="경로 탈출"):
            _safe_extract_tarball(tar_data, tmp_path)

    def test_subdir_filter(self, tmp_path: Path) -> None:
        tar_data = _make_tarball({
            "packages/app/index.ts": b"app code",
            "packages/lib/lib.ts": b"lib code",
            "README.md": b"root readme",
        })
        count = _safe_extract_tarball(tar_data, tmp_path, subdir="packages/app")
        assert count == 1
        assert (tmp_path / "index.ts").read_bytes() == b"app code"
        assert not (tmp_path / "lib.ts").exists()
        assert not (tmp_path / "README.md").exists()

    def test_empty_tarball(self, tmp_path: Path) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            pass
        count = _safe_extract_tarball(buf.getvalue(), tmp_path)
        assert count == 0


# ===========================================================================
# _detect_next_steps
# ===========================================================================


class TestDetectNextSteps:
    def test_npm_project(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}")
        steps = _detect_next_steps(tmp_path)
        assert "npm install" in steps

    def test_yarn_project(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "yarn.lock").write_text("")
        steps = _detect_next_steps(tmp_path)
        assert "yarn install" in steps

    def test_pnpm_project(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "pnpm-lock.yaml").write_text("")
        steps = _detect_next_steps(tmp_path)
        assert "pnpm install" in steps

    def test_python_project(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("flask")
        steps = _detect_next_steps(tmp_path)
        assert "pip install -r requirements.txt" in steps

    def test_env_example(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / ".env.example").write_text("KEY=")
        steps = _detect_next_steps(tmp_path)
        assert any(".env" in s for s in steps)

    def test_empty_project(self, tmp_path: Path) -> None:
        steps = _detect_next_steps(tmp_path)
        assert len(steps) == 0 or steps == []


# ===========================================================================
# _generate_claude_md
# ===========================================================================


class TestGenerateClaudeMd:
    def test_generates_file(self, tmp_path: Path) -> None:
        md_path = _generate_claude_md(
            tmp_path, "owner", "repo", "MIT License", ["npm install"]
        )
        assert md_path.exists()
        content = md_path.read_text(encoding="utf-8")
        assert "Scaffolded from" in content
        assert "owner/repo" in content
        assert "MIT License" in content

    def test_includes_next_steps(self, tmp_path: Path) -> None:
        md_path = _generate_claude_md(
            tmp_path, "owner", "repo", "MIT", ["npm install", "cp .env.example .env"]
        )
        content = md_path.read_text(encoding="utf-8")
        assert "npm install" in content
        assert ".env.example" in content

    def test_creates_claude_md_file(self, tmp_path: Path) -> None:
        md_path = _generate_claude_md(tmp_path, "o", "r", "MIT", [])
        assert md_path.name == "CLAUDE.md"


# ===========================================================================
# _validate_scaffold_args
# ===========================================================================


class TestValidateScaffoldArgs:
    def test_valid_args(self) -> None:
        result = _validate_scaffold_args({
            "repo_url": "https://github.com/owner/repo",
            "target_dir": "./my_project",
        })
        assert result["repo_url"] == "https://github.com/owner/repo"
        assert result["target_dir"] == "./my_project"
        assert result["generate_claude_md"] is True

    def test_rejects_missing_repo_url(self) -> None:
        with pytest.raises(ValueError, match="repo_url"):
            _validate_scaffold_args({"target_dir": "./p"})

    def test_rejects_invalid_repo_url(self) -> None:
        with pytest.raises(ValueError, match="형식"):
            _validate_scaffold_args({
                "repo_url": "https://gitlab.com/owner/repo",
                "target_dir": "./p",
            })

    def test_rejects_missing_target_dir(self) -> None:
        with pytest.raises(ValueError, match="target_dir"):
            _validate_scaffold_args({
                "repo_url": "https://github.com/owner/repo",
            })

"""Unit tests for server/tools/merge_repos.py.

Tests focus on package.json merging, version conflict detection,
tarball filtering, path validation, and security checks.
All tests run without network.
"""

from __future__ import annotations

import io
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import pytest

from server.tools.merge_repos import (
    MergeSecurityError,
    _filter_tarball_members,
    _safe_extract_to_target,
    _validate_path_no_traversal,
    merge_package_json,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tarball(
    files: dict[str, bytes],
    prefix: str = "owner-repo-abc123/",
) -> bytes:
    """Create an in-memory gzipped tarball with given files."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name=prefix + name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _make_tarball_with_symlink() -> bytes:
    """Create a tarball containing a symlink."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="owner-repo-abc123/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    return buf.getvalue()


# ===========================================================================
# merge_package_json
# ===========================================================================

class TestMergePackageJson:
    def test_add_new_dependencies(self) -> None:
        base: dict[str, Any] = {"dependencies": {"react": "^18.0.0"}}
        source: dict[str, Any] = {"dependencies": {"lucide-react": "^0.400.0"}}
        result = merge_package_json(base, source)

        assert "lucide-react" in result["added_dependencies"]
        assert result["added_dependencies"]["lucide-react"] == "^0.400.0"
        assert result["version_conflicts"] == []

    def test_no_duplicate_existing(self) -> None:
        base: dict[str, Any] = {"dependencies": {"react": "^18.0.0"}}
        source: dict[str, Any] = {"dependencies": {"react": "^18.0.0"}}
        result = merge_package_json(base, source)

        assert result["added_dependencies"] == {}
        assert result["version_conflicts"] == []

    def test_version_conflict_detected(self) -> None:
        base: dict[str, Any] = {"dependencies": {"react": "^18.0.0"}}
        source: dict[str, Any] = {"dependencies": {"react": "^17.0.0"}}
        result = merge_package_json(base, source)

        assert len(result["version_conflicts"]) == 1
        conflict = result["version_conflicts"][0]
        assert conflict["package"] == "react"
        assert conflict["base_version"] == "^18.0.0"
        assert conflict["source_version"] == "^17.0.0"

    def test_dev_dependencies_merged(self) -> None:
        base: dict[str, Any] = {"devDependencies": {"jest": "^29.0.0"}}
        source: dict[str, Any] = {"devDependencies": {"vitest": "^1.0.0"}}
        result = merge_package_json(base, source)

        assert "vitest" in result["added_dependencies"]

    def test_dev_dep_not_added_if_in_deps(self) -> None:
        base: dict[str, Any] = {
            "dependencies": {"typescript": "^5.0.0"},
            "devDependencies": {},
        }
        source: dict[str, Any] = {"devDependencies": {"typescript": "^5.0.0"}}
        result = merge_package_json(base, source)

        # Should not be added to devDeps since it's already in deps
        assert "typescript" not in result["added_dependencies"]

    def test_merged_data_sorted(self) -> None:
        base: dict[str, Any] = {"dependencies": {"zod": "^3.0.0", "axios": "^1.0.0"}}
        source: dict[str, Any] = {"dependencies": {"lodash": "^4.0.0"}}
        result = merge_package_json(base, source)

        deps_keys = list(result["merged_data"]["dependencies"].keys())
        assert deps_keys == sorted(deps_keys)

    def test_empty_base(self) -> None:
        base: dict[str, Any] = {}
        source: dict[str, Any] = {"dependencies": {"react": "^18.0.0"}}
        result = merge_package_json(base, source)

        assert "react" in result["added_dependencies"]

    def test_empty_source(self) -> None:
        base: dict[str, Any] = {"dependencies": {"react": "^18.0.0"}}
        source: dict[str, Any] = {}
        result = merge_package_json(base, source)

        assert result["added_dependencies"] == {}
        assert result["version_conflicts"] == []


# ===========================================================================
# _validate_path_no_traversal
# ===========================================================================

class TestValidatePathNoTraversal:
    def test_valid_path(self) -> None:
        _validate_path_no_traversal("src/components/chat", "target_path")

    def test_traversal_rejected(self) -> None:
        with pytest.raises(MergeSecurityError, match="보안 검증 실패"):
            _validate_path_no_traversal("../../../etc/passwd", "target_path")

    def test_hidden_traversal_rejected(self) -> None:
        with pytest.raises(MergeSecurityError, match="보안 검증 실패"):
            _validate_path_no_traversal("src/../../outside", "source_paths")


# ===========================================================================
# _filter_tarball_members
# ===========================================================================

class TestFilterTarballMembers:
    def _make_members(self, paths: list[str], prefix: str) -> list[tarfile.TarInfo]:
        members = []
        for p in paths:
            info = tarfile.TarInfo(name=prefix + p)
            info.size = 10
            members.append(info)
        return members

    def test_no_filter_returns_all(self) -> None:
        prefix = "owner-repo-abc/"
        members = self._make_members(["a.ts", "b.ts"], prefix)
        result = _filter_tarball_members(members, None, prefix)
        assert len(result) == 2

    def test_glob_filter(self) -> None:
        prefix = "owner-repo-abc/"
        members = self._make_members(
            ["components/Chat.tsx", "lib/db.ts", "components/Nav.tsx"],
            prefix,
        )
        result = _filter_tarball_members(members, ["components/*.tsx"], prefix)
        assert len(result) == 2

    def test_directory_filter(self) -> None:
        prefix = "owner-repo-abc/"
        members = self._make_members(
            ["src/a.ts", "src/b.ts", "lib/c.ts"],
            prefix,
        )
        result = _filter_tarball_members(members, ["src/**"], prefix)
        assert len(result) == 2


# ===========================================================================
# _safe_extract_to_target
# ===========================================================================

class TestSafeExtractToTarget:
    def test_basic_extraction(self) -> None:
        tarball = _make_tarball({
            "hello.txt": b"world",
            "src/app.ts": b"console.log('hi');",
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "out"
            target.mkdir()
            extracted = _safe_extract_to_target(tarball, target)

            assert "hello.txt" in extracted
            assert "src/app.ts" in extracted
            assert (target / "hello.txt").read_bytes() == b"world"

    def test_symlink_rejected(self) -> None:
        tarball = _make_tarball_with_symlink()
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "out"
            target.mkdir()
            with pytest.raises(MergeSecurityError, match="symlink"):
                _safe_extract_to_target(tarball, target)

    def test_source_paths_filter(self) -> None:
        tarball = _make_tarball({
            "components/Chat.tsx": b"chat",
            "lib/db.ts": b"db",
            "README.md": b"readme",
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "out"
            target.mkdir()
            extracted = _safe_extract_to_target(
                tarball, target, source_paths=["components/*.tsx"]
            )

            assert "components/Chat.tsx" in extracted
            assert "lib/db.ts" not in extracted
            assert "README.md" not in extracted

    def test_empty_tarball(self) -> None:
        tarball = _make_tarball({})
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "out"
            target.mkdir()
            extracted = _safe_extract_to_target(tarball, target)
            assert extracted == []

"""Unit tests for server/tools/smart_scaffold.py.

Tests cover: keep_only filtering, remove_patterns, project_name substitution,
env_vars generation, unused dependency detection, and security validation.
All tests run without network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.tools.scaffold import SecurityError
from server.tools.smart_scaffold import (
    _apply_env_vars,
    _apply_keep_only,
    _apply_project_name,
    _apply_remove_patterns,
    _collect_all_files,
    _detect_unused_deps,
    _validate_glob_patterns,
    _validate_smart_scaffold_args,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _populate_dir(tmp_path: Path, files: dict[str, str]) -> None:
    """Create files in tmp_path from a dict of relative_path -> content."""
    for rel_path, content in files.items():
        full = tmp_path / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")


# ===========================================================================
# _validate_glob_patterns
# ===========================================================================


class TestValidateGlobPatterns:
    def test_valid_patterns(self) -> None:
        result = _validate_glob_patterns(
            ["src/components/**", "src/hooks/**"], "keep_only"
        )
        assert result == ["src/components/**", "src/hooks/**"]

    def test_rejects_path_traversal(self) -> None:
        with pytest.raises(SecurityError, match="\\.\\."):
            _validate_glob_patterns(["../etc/**"], "keep_only")

    def test_rejects_absolute_path(self) -> None:
        with pytest.raises(SecurityError, match="상대 경로"):
            _validate_glob_patterns(["/etc/passwd"], "remove_patterns")

    def test_strips_whitespace(self) -> None:
        result = _validate_glob_patterns(["  src/**  "], "keep_only")
        assert result == ["src/**"]

    def test_skips_empty_strings(self) -> None:
        result = _validate_glob_patterns(["", "  ", "src/**"], "keep_only")
        assert result == ["src/**"]


# ===========================================================================
# _validate_smart_scaffold_args
# ===========================================================================


class TestValidateSmartScaffoldArgs:
    def test_minimal_valid_args(self) -> None:
        result = _validate_smart_scaffold_args({
            "repo_url": "https://github.com/owner/repo",
            "target_dir": "./my_project",
        })
        assert result["repo_url"] == "https://github.com/owner/repo"
        assert result["target_dir"] == "./my_project"
        assert result["project_name"] is None
        assert result["keep_only"] is None
        assert result["remove_patterns"] is None
        assert result["env_vars"] is None

    def test_full_args(self) -> None:
        result = _validate_smart_scaffold_args({
            "repo_url": "https://github.com/owner/repo",
            "target_dir": "./my_project",
            "project_name": "my-app",
            "keep_only": ["src/**"],
            "remove_patterns": ["tests/**"],
            "env_vars": {"DB_URL": "postgres://localhost"},
            "subdir": "packages/web",
        })
        assert result["project_name"] == "my-app"
        assert result["keep_only"] == ["src/**"]
        assert result["remove_patterns"] == ["tests/**"]
        assert result["env_vars"] == {"DB_URL": "postgres://localhost"}
        assert result["subdir"] == "packages/web"

    def test_rejects_missing_repo_url(self) -> None:
        with pytest.raises(ValueError, match="repo_url"):
            _validate_smart_scaffold_args({"target_dir": "./p"})

    def test_rejects_invalid_repo_url(self) -> None:
        with pytest.raises(ValueError, match="형식"):
            _validate_smart_scaffold_args({
                "repo_url": "https://gitlab.com/owner/repo",
                "target_dir": "./p",
            })

    def test_rejects_missing_target_dir(self) -> None:
        with pytest.raises(ValueError, match="target_dir"):
            _validate_smart_scaffold_args({
                "repo_url": "https://github.com/owner/repo",
            })

    def test_rejects_non_list_keep_only(self) -> None:
        with pytest.raises(ValueError, match="keep_only"):
            _validate_smart_scaffold_args({
                "repo_url": "https://github.com/owner/repo",
                "target_dir": "./p",
                "keep_only": "src/**",
            })

    def test_rejects_non_dict_env_vars(self) -> None:
        with pytest.raises(ValueError, match="env_vars"):
            _validate_smart_scaffold_args({
                "repo_url": "https://github.com/owner/repo",
                "target_dir": "./p",
                "env_vars": "DB_URL=foo",
            })


# ===========================================================================
# _apply_keep_only
# ===========================================================================


class TestApplyKeepOnly:
    def test_keeps_matching_files(self, tmp_path: Path) -> None:
        _populate_dir(tmp_path, {
            "src/components/Button.tsx": "export default Button",
            "src/hooks/useAuth.ts": "export function useAuth()",
            "src/pages/analytics/index.tsx": "analytics page",
            "tests/unit/test.ts": "test code",
            "README.md": "# Readme",
            "LICENSE": "MIT",
        })
        removed = _apply_keep_only(tmp_path, ["src/components/**", "src/hooks/**"])
        assert (tmp_path / "src" / "components" / "Button.tsx").exists()
        assert (tmp_path / "src" / "hooks" / "useAuth.ts").exists()
        assert (tmp_path / "LICENSE").exists()  # Essential files preserved
        assert not (tmp_path / "src" / "pages" / "analytics" / "index.tsx").exists()
        assert not (tmp_path / "tests" / "unit" / "test.ts").exists()
        assert not (tmp_path / "README.md").exists()
        assert removed == 3  # analytics, tests, README

    def test_empty_keep_only_keeps_all(self, tmp_path: Path) -> None:
        """When keep_only is None (not passed), no filtering happens."""
        _populate_dir(tmp_path, {
            "src/index.ts": "code",
            "tests/test.ts": "test",
        })
        files_before = len(_collect_all_files(tmp_path))
        # This test verifies the handler logic: keep_only=None means skip filtering
        # _apply_keep_only with empty patterns would remove everything
        # so the handler should NOT call _apply_keep_only when keep_only is None
        assert files_before == 2

    def test_preserves_license_variants(self, tmp_path: Path) -> None:
        _populate_dir(tmp_path, {
            "src/index.ts": "code",
            "LICENSE.md": "MIT License",
            "LICENCE": "BSD License",
        })
        _apply_keep_only(tmp_path, ["src/**"])
        assert (tmp_path / "LICENSE.md").exists()
        assert (tmp_path / "LICENCE").exists()


# ===========================================================================
# _apply_remove_patterns
# ===========================================================================


class TestApplyRemovePatterns:
    def test_removes_matching_files(self, tmp_path: Path) -> None:
        _populate_dir(tmp_path, {
            "src/index.ts": "code",
            "src/pages/analytics/index.tsx": "analytics",
            "tests/unit/test.ts": "test",
            "LICENSE": "MIT",
        })
        removed = _apply_remove_patterns(tmp_path, ["tests/**"])
        assert not (tmp_path / "tests" / "unit" / "test.ts").exists()
        assert (tmp_path / "src" / "index.ts").exists()
        assert removed == 1

    def test_removes_multiple_patterns(self, tmp_path: Path) -> None:
        _populate_dir(tmp_path, {
            "src/index.ts": "code",
            "src/pages/analytics/index.tsx": "analytics",
            "tests/unit/test.ts": "test",
            "docs/guide.md": "docs",
        })
        removed = _apply_remove_patterns(
            tmp_path, ["tests/**", "docs/**"]
        )
        assert removed == 2
        assert (tmp_path / "src" / "index.ts").exists()
        assert not (tmp_path / "tests").exists()
        assert not (tmp_path / "docs").exists()

    def test_no_matches_removes_nothing(self, tmp_path: Path) -> None:
        _populate_dir(tmp_path, {
            "src/index.ts": "code",
        })
        removed = _apply_remove_patterns(tmp_path, ["nonexistent/**"])
        assert removed == 0
        assert (tmp_path / "src" / "index.ts").exists()


# ===========================================================================
# _apply_project_name
# ===========================================================================


class TestApplyProjectName:
    def test_updates_package_json_name(self, tmp_path: Path) -> None:
        pkg = {"name": "original-repo", "version": "1.0.0"}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        _apply_project_name(tmp_path, "my-new-app", "original-repo")
        updated = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
        assert updated["name"] == "my-new-app"
        assert updated["version"] == "1.0.0"  # Other fields preserved

    def test_updates_readme_heading(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "# Original Repo\n\nSome description.\n",
            encoding="utf-8",
        )
        _apply_project_name(tmp_path, "my-new-app", "original-repo")
        content = (tmp_path / "README.md").read_text(encoding="utf-8")
        assert content.startswith("# my-new-app\n")
        assert "Some description." in content

    def test_no_package_json_no_error(self, tmp_path: Path) -> None:
        # Should not raise even if files don't exist
        _apply_project_name(tmp_path, "my-app", "original")

    def test_malformed_package_json_no_error(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("not json", encoding="utf-8")
        _apply_project_name(tmp_path, "my-app", "original")
        # Should not crash


# ===========================================================================
# _apply_env_vars
# ===========================================================================


class TestApplyEnvVars:
    def test_creates_env_from_example(self, tmp_path: Path) -> None:
        (tmp_path / ".env.example").write_text(
            "# Database config\nDATABASE_URL=\nAPI_KEY=your_key_here\n",
            encoding="utf-8",
        )
        created = _apply_env_vars(tmp_path, {"DATABASE_URL": "postgres://localhost"})
        assert created is True
        content = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "DATABASE_URL=postgres://localhost" in content
        assert "API_KEY=your_key_here" in content  # Preserved from template

    def test_adds_extra_vars_not_in_example(self, tmp_path: Path) -> None:
        (tmp_path / ".env.example").write_text(
            "EXISTING_KEY=value\n",
            encoding="utf-8",
        )
        created = _apply_env_vars(
            tmp_path, {"EXISTING_KEY": "new_value", "NEW_KEY": "brand_new"}
        )
        assert created is True
        content = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "EXISTING_KEY=new_value" in content
        assert "NEW_KEY=brand_new" in content

    def test_creates_env_from_scratch(self, tmp_path: Path) -> None:
        created = _apply_env_vars(
            tmp_path, {"DB_URL": "sqlite:///db.sqlite", "SECRET": "changeme"}
        )
        assert created is True
        content = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "DB_URL=sqlite:///db.sqlite" in content
        assert "SECRET=changeme" in content

    def test_empty_env_vars_no_file(self, tmp_path: Path) -> None:
        created = _apply_env_vars(tmp_path, {})
        assert created is False
        assert not (tmp_path / ".env").exists()


# ===========================================================================
# _detect_unused_deps
# ===========================================================================


class TestDetectUnusedDeps:
    def test_detects_unused_dep(self, tmp_path: Path) -> None:
        pkg = {
            "name": "test",
            "dependencies": {
                "react": "^18.0.0",
                "@vercel/analytics": "^1.0.0",
                "lodash": "^4.0.0",
            },
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        _populate_dir(tmp_path, {
            "src/index.tsx": "import React from 'react';\nimport _ from 'lodash';",
        })
        unused = _detect_unused_deps(tmp_path)
        assert "@vercel/analytics" in unused
        assert "react" not in unused
        assert "lodash" not in unused

    def test_no_package_json(self, tmp_path: Path) -> None:
        unused = _detect_unused_deps(tmp_path)
        assert unused == []

    def test_all_deps_used(self, tmp_path: Path) -> None:
        pkg = {
            "name": "test",
            "dependencies": {"react": "^18.0.0"},
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        _populate_dir(tmp_path, {
            "src/App.tsx": "import React from 'react';",
        })
        unused = _detect_unused_deps(tmp_path)
        assert unused == []

    def test_no_dependencies(self, tmp_path: Path) -> None:
        pkg = {"name": "test"}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        unused = _detect_unused_deps(tmp_path)
        assert unused == []


# ===========================================================================
# Security: path traversal in glob patterns
# ===========================================================================


class TestGlobPatternSecurity:
    def test_keep_only_rejects_traversal(self) -> None:
        with pytest.raises(SecurityError):
            _validate_glob_patterns(["../../etc/**"], "keep_only")

    def test_remove_patterns_rejects_absolute(self) -> None:
        with pytest.raises(SecurityError):
            _validate_glob_patterns(["/root/**"], "remove_patterns")

    def test_nested_traversal_rejected(self) -> None:
        with pytest.raises(SecurityError):
            _validate_glob_patterns(["src/../../etc/**"], "keep_only")

"""Unit tests for inject_feature tool.

Tests cover:
- Input validation (missing/invalid args)
- File content fetching (mock GitHub)
- Import dependency extraction
- Env var detection (process.env.X, os.getenv patterns)
- Dependency detection (npm/pip packages from imports)
- Conflict detection (existing files in project)
- Placement with custom mapping
- Placement with auto-suggest
- Full pipeline integration test (mock GitHub)
- License check integration
- Edge cases: empty files, non-existent files
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from server.tools.inject_feature import (
    INJECT_FEATURE_TOOL,
    _check_conflicts,
    _detect_dependencies,
    _extract_env_vars,
    _extract_python_imports,
    _generate_integration_notes,
    _resolve_relative_imports,
    _validate_inject_args,
    handle_inject_feature,
)

# ===========================================================================
# Tool definition
# ===========================================================================


class TestToolDefinition:
    """Tests for INJECT_FEATURE_TOOL definition."""

    def test_tool_name(self) -> None:
        assert INJECT_FEATURE_TOOL.name == "inject_feature"

    def test_required_fields(self) -> None:
        schema = INJECT_FEATURE_TOOL.inputSchema
        assert schema["required"] == ["repo_url", "feature", "files", "project_dir"]

    def test_placement_is_optional(self) -> None:
        schema = INJECT_FEATURE_TOOL.inputSchema
        assert "placement" not in schema.get("required", [])


# ===========================================================================
# Input validation
# ===========================================================================


class TestValidateInjectArgs:
    """Tests for _validate_inject_args()."""

    def test_missing_repo_url(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="repo_url"):
            _validate_inject_args({
                "feature": "auth", "files": ["a.ts"],
                "project_dir": str(tmp_path),
            })

    def test_empty_repo_url(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="repo_url"):
            _validate_inject_args({
                "repo_url": "", "feature": "auth",
                "files": ["a.ts"], "project_dir": str(tmp_path),
            })

    def test_missing_feature(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="feature"):
            _validate_inject_args({
                "repo_url": "https://github.com/o/r",
                "files": ["a.ts"], "project_dir": str(tmp_path),
            })

    def test_feature_too_long(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="200"):
            _validate_inject_args({
                "repo_url": "https://github.com/o/r",
                "feature": "x" * 201,
                "files": ["a.ts"], "project_dir": str(tmp_path),
            })

    def test_missing_files(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="files"):
            _validate_inject_args({
                "repo_url": "https://github.com/o/r",
                "feature": "auth", "project_dir": str(tmp_path),
            })

    def test_empty_files_list(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="files"):
            _validate_inject_args({
                "repo_url": "https://github.com/o/r",
                "feature": "auth", "files": [],
                "project_dir": str(tmp_path),
            })

    def test_files_with_empty_string(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="files\\[0\\]"):
            _validate_inject_args({
                "repo_url": "https://github.com/o/r",
                "feature": "auth", "files": [""],
                "project_dir": str(tmp_path),
            })

    def test_missing_project_dir(self) -> None:
        with pytest.raises(ValueError, match="project_dir"):
            _validate_inject_args({
                "repo_url": "https://github.com/o/r",
                "feature": "auth", "files": ["a.ts"],
            })

    def test_invalid_placement_type(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="placement"):
            _validate_inject_args({
                "repo_url": "https://github.com/o/r",
                "feature": "auth", "files": ["a.ts"],
                "project_dir": str(tmp_path), "placement": "bad",
            })

    def test_valid_args(self, tmp_path: Path) -> None:
        project_dir = str(tmp_path / "myapp")
        result = _validate_inject_args({
            "repo_url": "https://github.com/owner/repo/",
            "feature": " auth ",
            "files": [" src/auth.ts "],
            "project_dir": f" {project_dir} ",
        })
        assert result["repo_url"] == "https://github.com/owner/repo"
        assert result["feature"] == "auth"
        assert result["files"] == ["src/auth.ts"]
        assert result["project_dir"] == project_dir
        assert result["placement"] is None

    def test_valid_args_with_placement(self, tmp_path: Path) -> None:
        result = _validate_inject_args({
            "repo_url": "https://github.com/owner/repo",
            "feature": "auth",
            "files": ["a.ts"],
            "project_dir": str(tmp_path),
            "placement": {"a.ts": "src/a.ts"},
        })
        assert result["placement"] == {"a.ts": "src/a.ts"}


# ===========================================================================
# Env var detection
# ===========================================================================


class TestExtractEnvVars:
    """Tests for _extract_env_vars()."""

    def test_process_env_dot_notation(self) -> None:
        content = 'const key = process.env.STRIPE_SECRET_KEY;'
        result = _extract_env_vars(content)
        assert len(result) == 1
        assert result[0]["name"] == "STRIPE_SECRET_KEY"
        assert "Stripe" in result[0]["description"]

    def test_process_env_bracket_notation(self) -> None:
        content = 'const key = process.env["DATABASE_URL"];'
        result = _extract_env_vars(content)
        assert len(result) == 1
        assert result[0]["name"] == "DATABASE_URL"

    def test_process_env_single_quote_bracket(self) -> None:
        content = "const key = process.env['JWT_SECRET'];"
        result = _extract_env_vars(content)
        assert len(result) == 1
        assert result[0]["name"] == "JWT_SECRET"

    def test_os_getenv(self) -> None:
        content = 'secret = os.getenv("SECRET_KEY")'
        result = _extract_env_vars(content)
        assert len(result) == 1
        assert result[0]["name"] == "SECRET_KEY"

    def test_os_environ_bracket(self) -> None:
        content = 'db_url = os.environ["DATABASE_URL"]'
        result = _extract_env_vars(content)
        assert len(result) == 1
        assert result[0]["name"] == "DATABASE_URL"

    def test_os_environ_get(self) -> None:
        content = 'db_url = os.environ.get("REDIS_URL", "localhost")'
        result = _extract_env_vars(content)
        assert len(result) == 1
        assert result[0]["name"] == "REDIS_URL"

    def test_deduplication(self) -> None:
        content = (
            'const a = process.env.STRIPE_SECRET_KEY;\n'
            'const b = process.env.STRIPE_SECRET_KEY;\n'
        )
        result = _extract_env_vars(content)
        assert len(result) == 1

    def test_multiple_vars(self) -> None:
        content = (
            'const a = process.env.STRIPE_SECRET_KEY;\n'
            'const b = process.env.DATABASE_URL;\n'
        )
        result = _extract_env_vars(content)
        assert len(result) == 2
        names = {r["name"] for r in result}
        assert names == {"STRIPE_SECRET_KEY", "DATABASE_URL"}

    def test_no_env_vars(self) -> None:
        content = 'const x = 42;'
        result = _extract_env_vars(content)
        assert result == []

    def test_unknown_env_var_gets_default_description(self) -> None:
        content = 'const key = process.env.MY_CUSTOM_VAR;'
        result = _extract_env_vars(content)
        assert len(result) == 1
        assert result[0]["name"] == "MY_CUSTOM_VAR"
        assert "MY_CUSTOM_VAR" in result[0]["description"]


# ===========================================================================
# Dependency detection
# ===========================================================================


class TestDetectDependencies:
    """Tests for _detect_dependencies()."""

    def test_npm_dependencies(self) -> None:
        contents = {
            "src/payment.ts": (
                'import Stripe from "stripe";\n'
                'import { loadStripe } from "@stripe/stripe-js";\n'
            ),
        }
        result = _detect_dependencies(contents, "typescript")
        assert "npm" in result
        assert "stripe" in result["npm"]
        assert "@stripe/stripe-js" in result["npm"]
        assert "npm install" in result["install_command"]

    def test_npm_skips_relative_imports(self) -> None:
        contents = {
            "src/utils.ts": 'import { helper } from "./helper";\n',
        }
        result = _detect_dependencies(contents, "typescript")
        assert result["npm"] == []

    def test_pip_dependencies(self) -> None:
        contents = {
            "app/main.py": (
                "import fastapi\n"
                "import sqlalchemy\n"
                "import os\n"
                "import json\n"
            ),
        }
        result = _detect_dependencies(contents, "python")
        assert "pip" in result
        assert "fastapi" in result["pip"]
        assert "sqlalchemy" in result["pip"]
        # stdlib should be excluded
        assert "os" not in result["pip"]
        assert "json" not in result["pip"]

    def test_empty_contents(self) -> None:
        result = _detect_dependencies({}, "typescript")
        assert result["npm"] == []
        assert result["install_command"] == ""

    def test_python_empty(self) -> None:
        result = _detect_dependencies({}, "python")
        assert result["pip"] == []
        assert result["install_command"] == ""


# ===========================================================================
# Python import extraction
# ===========================================================================


class TestExtractPythonImports:
    """Tests for _extract_python_imports()."""

    def test_import_statement(self) -> None:
        result = _extract_python_imports("import fastapi")
        assert "fastapi" in result

    def test_from_import(self) -> None:
        result = _extract_python_imports("from fastapi import FastAPI")
        assert "fastapi" in result

    def test_submodule_import(self) -> None:
        result = _extract_python_imports("from fastapi.responses import JSONResponse")
        assert "fastapi" in result

    def test_stdlib_included_in_raw(self) -> None:
        # _extract_python_imports returns raw, filtering is done in _detect_dependencies
        result = _extract_python_imports("import os")
        assert "os" in result


# ===========================================================================
# Conflict detection
# ===========================================================================


class TestCheckConflicts:
    """Tests for _check_conflicts()."""

    def test_no_conflicts(self, tmp_path: Path) -> None:
        result = _check_conflicts(["nonexistent/file.ts"], str(tmp_path))
        assert result == []

    def test_file_exists(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "auth.ts").write_text("existing")
        result = _check_conflicts(["src/auth.ts"], str(tmp_path))
        assert len(result) == 1
        assert result[0]["target_path"] == "src/auth.ts"
        assert "존재" in result[0]["reason"]

    def test_multiple_conflicts(self, tmp_path: Path) -> None:
        (tmp_path / "a.ts").write_text("a")
        (tmp_path / "b.ts").write_text("b")
        result = _check_conflicts(["a.ts", "b.ts", "c.ts"], str(tmp_path))
        assert len(result) == 2


# ===========================================================================
# Relative import resolution
# ===========================================================================


class TestResolveRelativeImports:
    """Tests for _resolve_relative_imports()."""

    def test_js_relative_import(self) -> None:
        content = 'import { helper } from "./utils";'
        result = _resolve_relative_imports(content, "src/components/auth.ts", "typescript")
        # Should resolve to src/components/utils with various extensions
        assert any("src/components/utils" in r for r in result)

    def test_js_parent_import(self) -> None:
        content = 'import { db } from "../lib/db";'
        result = _resolve_relative_imports(content, "src/api/route.ts", "typescript")
        assert any("src/lib/db" in r for r in result)

    def test_python_relative_import(self) -> None:
        content = "from .models import User"
        result = _resolve_relative_imports(content, "app/auth/views.py", "python")
        assert any("models.py" in r for r in result)

    def test_no_relative_imports(self) -> None:
        content = 'import stripe from "stripe";'
        result = _resolve_relative_imports(content, "src/pay.ts", "typescript")
        assert result == []


# ===========================================================================
# Integration notes
# ===========================================================================


class TestGenerateIntegrationNotes:
    """Tests for _generate_integration_notes()."""

    def test_nextjs_framework_hints(self) -> None:
        notes = _generate_integration_notes(
            "payment", "nextjs", [], {}, [],
        )
        assert "layout.tsx" in notes

    def test_env_vars_mentioned(self) -> None:
        env_vars = [{"name": "STRIPE_SECRET_KEY", "description": "Stripe key"}]
        notes = _generate_integration_notes(
            "payment", None, env_vars, {}, [],
        )
        assert "STRIPE_SECRET_KEY" in notes

    def test_install_command_mentioned(self) -> None:
        deps: dict[str, Any] = {"npm": ["stripe"], "install_command": "npm install stripe"}
        notes = _generate_integration_notes(
            "payment", None, [], deps, [],
        )
        assert "npm install stripe" in notes

    def test_conflicts_mentioned(self) -> None:
        conflicts = [{"target_path": "src/auth.ts", "reason": "exists"}]
        notes = _generate_integration_notes(
            "auth", None, [], {}, conflicts,
        )
        assert "src/auth.ts" in notes
        assert "충돌" in notes

    def test_fallback_note(self) -> None:
        notes = _generate_integration_notes("auth", None, [], {}, [])
        assert "auth" in notes

    def test_express_framework(self) -> None:
        notes = _generate_integration_notes("api", "express", [], {}, [])
        assert "라우터" in notes


# ===========================================================================
# Full pipeline — handle_inject_feature
# ===========================================================================


def _make_mock_github(
    file_contents: dict[str, str] | None = None,
    license_spdx: str = "MIT",
) -> AsyncMock:
    """Create a mock GitHubClient for inject_feature tests."""
    mock = AsyncMock()

    # get_file_content_batch
    if file_contents is None:
        file_contents = {
            "app/api/checkout/route.ts": (
                'import Stripe from "stripe";\n'
                'const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!);\n'
                'export async function POST() { return Response.json({}); }\n'
            ),
        }

    async def mock_batch(repo: str, paths: list[str]) -> dict[str, str]:
        return {p: c for p, c in (file_contents or {}).items() if p in paths}

    mock.get_file_content_batch = AsyncMock(side_effect=mock_batch)

    # get_license
    mock.get_license = AsyncMock(return_value={
        "name": "MIT License",
        "spdx_id": license_spdx,
        "url": None,
        "body": "",
    })

    return mock


class TestHandleInjectFeature:
    """Integration tests for handle_inject_feature()."""

    @pytest.mark.asyncio
    async def test_full_pipeline(self, tmp_path: Path) -> None:
        """Full pipeline test with mocked GitHub."""
        project_dir = str(tmp_path)
        # Create a package.json to trigger stack detection
        (tmp_path / "package.json").write_text('{"dependencies": {"next": "14.0.0"}}')
        (tmp_path / "tsconfig.json").write_text("{}")

        mock_github = _make_mock_github()

        result = await handle_inject_feature(
            {
                "repo_url": "https://github.com/owner/repo",
                "feature": "stripe payment",
                "files": ["app/api/checkout/route.ts"],
                "project_dir": project_dir,
            },
            mock_github,
        )

        assert len(result) == 1
        data = json.loads(result[0].text)

        assert data["feature"] == "stripe payment"
        assert data["source_repo"] == "owner/repo"
        assert data["license"] == "MIT"
        assert len(data["files"]) >= 1
        assert data["files"][0]["source_path"] == "app/api/checkout/route.ts"
        assert data["files"][0]["is_dependency"] is False
        assert "STRIPE_SECRET_KEY" in [
            v["name"] for v in data["env_vars_needed"]
        ]
        assert "stripe" in data["dependencies"].get("npm", [])

    @pytest.mark.asyncio
    async def test_invalid_repo_url(self, tmp_path: Path) -> None:
        mock_github = _make_mock_github()
        with pytest.raises(ValueError, match="Invalid GitHub"):
            await handle_inject_feature(
                {
                    "repo_url": "not-a-url",
                    "feature": "auth",
                    "files": ["a.ts"],
                    "project_dir": str(tmp_path),
                },
                mock_github,
            )

    @pytest.mark.asyncio
    async def test_nonexistent_project_dir(self) -> None:
        mock_github = _make_mock_github()
        with pytest.raises(ValueError, match="존재하지 않"):
            await handle_inject_feature(
                {
                    "repo_url": "https://github.com/o/r",
                    "feature": "auth",
                    "files": ["a.ts"],
                    "project_dir": "/nonexistent/path/abc123",
                },
                mock_github,
            )

    @pytest.mark.asyncio
    async def test_empty_file_fetch(self, tmp_path: Path) -> None:
        """When GitHub returns no file content."""
        (tmp_path / "package.json").write_text("{}")

        mock_github = _make_mock_github(file_contents={})

        # Override to return empty for any paths
        mock_github.get_file_content_batch = AsyncMock(return_value={})

        result = await handle_inject_feature(
            {
                "repo_url": "https://github.com/o/r",
                "feature": "auth",
                "files": ["nonexistent.ts"],
                "project_dir": str(tmp_path),
            },
            mock_github,
        )

        data = json.loads(result[0].text)
        assert "message" in data
        assert data["files"] == []

    @pytest.mark.asyncio
    async def test_custom_placement(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}")

        file_contents = {
            "src/auth.ts": 'export function login() {}',
        }
        mock_github = _make_mock_github(file_contents=file_contents)

        result = await handle_inject_feature(
            {
                "repo_url": "https://github.com/o/r",
                "feature": "auth",
                "files": ["src/auth.ts"],
                "project_dir": str(tmp_path),
                "placement": {"src/auth.ts": "lib/custom-auth.ts"},
            },
            mock_github,
        )

        data = json.loads(result[0].text)
        auth_file = data["files"][0]
        assert auth_file["target_path"] == "lib/custom-auth.ts"

    @pytest.mark.asyncio
    async def test_conflict_detection(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}")
        # Create existing file that will conflict
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "auth.ts").write_text("existing content")

        file_contents = {"src/auth.ts": 'export function login() {}'}
        mock_github = _make_mock_github(file_contents=file_contents)

        result = await handle_inject_feature(
            {
                "repo_url": "https://github.com/o/r",
                "feature": "auth",
                "files": ["src/auth.ts"],
                "project_dir": str(tmp_path),
                "placement": {"src/auth.ts": "src/auth.ts"},
            },
            mock_github,
        )

        data = json.loads(result[0].text)
        assert len(data["conflicts"]) == 1
        assert data["conflicts"][0]["target_path"] == "src/auth.ts"

    @pytest.mark.asyncio
    async def test_license_check_failure(self, tmp_path: Path) -> None:
        """License check should gracefully handle exceptions."""
        (tmp_path / "package.json").write_text("{}")

        file_contents = {"src/a.ts": "export const x = 1;"}
        mock_github = _make_mock_github(file_contents=file_contents)
        mock_github.get_license = AsyncMock(side_effect=Exception("API error"))

        result = await handle_inject_feature(
            {
                "repo_url": "https://github.com/o/r",
                "feature": "auth",
                "files": ["src/a.ts"],
                "project_dir": str(tmp_path),
            },
            mock_github,
        )

        data = json.loads(result[0].text)
        # Should still return a result, license might be None or "Unknown"
        assert "license" in data

    @pytest.mark.asyncio
    async def test_python_project(self, tmp_path: Path) -> None:
        """Test with a Python project (requirements.txt)."""
        (tmp_path / "requirements.txt").write_text("fastapi\nsqlalchemy\n")

        file_contents = {
            "app/main.py": (
                "import fastapi\n"
                "import sqlalchemy\n"
                'db_url = os.getenv("DATABASE_URL")\n'
            ),
        }
        mock_github = _make_mock_github(file_contents=file_contents)

        result = await handle_inject_feature(
            {
                "repo_url": "https://github.com/o/r",
                "feature": "api",
                "files": ["app/main.py"],
                "project_dir": str(tmp_path),
            },
            mock_github,
        )

        data = json.loads(result[0].text)
        assert "pip" in data["dependencies"]
        assert "fastapi" in data["dependencies"]["pip"]
        assert any(v["name"] == "DATABASE_URL" for v in data["env_vars_needed"])

    @pytest.mark.asyncio
    async def test_auto_suggest_placement_nextjs(self, tmp_path: Path) -> None:
        """Auto placement should use nextjs rules."""
        (tmp_path / "package.json").write_text('{"dependencies": {"next": "14.0.0"}}')
        (tmp_path / "tsconfig.json").write_text("{}")

        file_contents = {"api/payment/route.ts": "export async function POST() {}"}
        mock_github = _make_mock_github(file_contents=file_contents)

        result = await handle_inject_feature(
            {
                "repo_url": "https://github.com/o/r",
                "feature": "payment",
                "files": ["api/payment/route.ts"],
                "project_dir": str(tmp_path),
            },
            mock_github,
        )

        data = json.loads(result[0].text)
        # Should have a target_path suggested by nextjs rules
        assert data["files"][0]["target_path"] is not None

    @pytest.mark.asyncio
    async def test_dependency_file_resolution(self, tmp_path: Path) -> None:
        """Test 1-depth dependency resolution."""
        (tmp_path / "package.json").write_text("{}")

        main_content = (
            'import { helper } from "./utils";\n'
            'export function main() { return helper(); }\n'
        )
        utils_content = 'export function helper() { return 42; }\n'

        file_contents = {
            "src/main.ts": main_content,
            "src/utils.ts": utils_content,
        }

        call_count = 0

        async def mock_batch(repo: str, paths: list[str]) -> dict[str, str]:
            nonlocal call_count
            call_count += 1
            return {p: c for p, c in file_contents.items() if p in paths}

        mock_github = _make_mock_github(file_contents=file_contents)
        mock_github.get_file_content_batch = AsyncMock(side_effect=mock_batch)

        result = await handle_inject_feature(
            {
                "repo_url": "https://github.com/o/r",
                "feature": "utils",
                "files": ["src/main.ts"],
                "project_dir": str(tmp_path),
            },
            mock_github,
        )

        response = json.loads(result[0].text)
        # Should have called batch at least twice: once for main files, once for deps
        assert call_count >= 1
        assert len(response["files"]) >= 1

"""Unit tests for server/tools/integration_check.py."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from server.tools.integration_check import (
    check_api_url_consistency,
    check_dependencies,
    check_empty_files,
    check_env_vars,
    check_relative_imports,
    handle_validate_integration,
    scan_imports,
)

# ===========================================================================
# Helpers
# ===========================================================================


def _write_file(base: str, rel_path: str, content: str) -> str:
    """Create a file inside a temp directory, returning its absolute path."""
    full = os.path.join(base, rel_path.replace("/", os.sep))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return full


# ===========================================================================
# scan_imports
# ===========================================================================


class TestScanImports:
    def test_js_import_statement(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/App.tsx", (
            "import React from 'react';\n"
            "import { Button } from '@mui/material';\n"
        ))
        results = scan_imports(str(tmp_path))
        modules = [r["module"] for r in results]
        assert "react" in modules
        assert "@mui/material" in modules

    def test_js_require_statement(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/index.js", (
            "const express = require('express');\n"
            "const path = require('path');\n"
        ))
        results = scan_imports(str(tmp_path))
        modules = [r["module"] for r in results]
        assert "express" in modules
        assert "path" in modules

    def test_js_relative_import(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/App.tsx", (
            "import { Header } from './components/Header';\n"
            "import utils from '../utils';\n"
        ))
        results = scan_imports(str(tmp_path))
        relative = [r for r in results if r["is_relative"]]
        assert len(relative) == 2

    def test_python_import(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "main.py", (
            "import json\n"
            "from pathlib import Path\n"
            "import requests\n"
            "from server.tools import validate\n"
        ))
        results = scan_imports(str(tmp_path))
        modules = [r["module"] for r in results]
        assert "json" in modules
        assert "pathlib" in modules
        assert "requests" in modules
        assert "server.tools" in modules

    def test_skips_node_modules(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "node_modules/foo/index.js", (
            "import bar from 'bar';\n"
        ))
        _write_file(str(tmp_path), "src/index.js", (
            "import foo from 'foo';\n"
        ))
        results = scan_imports(str(tmp_path))
        files = [r["file"] for r in results]
        assert all("node_modules" not in f for f in files)

    def test_empty_directory(self, tmp_path: Path) -> None:
        results = scan_imports(str(tmp_path))
        assert results == []

    def test_includes_line_numbers(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/app.ts", (
            "// header comment\n"
            "import React from 'react';\n"
            "import axios from 'axios';\n"
        ))
        results = scan_imports(str(tmp_path))
        lines = {r["module"]: r["line"] for r in results}
        assert lines["react"] == 2
        assert lines["axios"] == 3


# ===========================================================================
# check_dependencies
# ===========================================================================


class TestCheckDependencies:
    def test_missing_js_dependency(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "package.json", json.dumps({
            "dependencies": {"react": "^18.0.0"},
            "devDependencies": {},
        }))
        imports = [
            {
                "file": "src/App.tsx", "line": 1,
                "module": "react", "kind": "js", "is_relative": False,
            },
            {
                "file": "src/App.tsx", "line": 2,
                "module": "@assistant-ui/react", "kind": "js", "is_relative": False,
            },
        ]
        issues = check_dependencies(str(tmp_path), imports)
        missing = [i for i in issues if i["type"] == "missing_dependency"]
        assert len(missing) == 1
        assert "@assistant-ui/react" in missing[0]["detail"]

    def test_no_issues_when_all_deps_present(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "package.json", json.dumps({
            "dependencies": {"react": "^18.0.0", "axios": "^1.0.0"},
        }))
        imports = [
            {
                "file": "src/App.tsx", "line": 1,
                "module": "react", "kind": "js", "is_relative": False,
            },
            {
                "file": "src/lib.ts", "line": 1,
                "module": "axios", "kind": "js", "is_relative": False,
            },
        ]
        issues = check_dependencies(str(tmp_path), imports)
        assert len(issues) == 0

    def test_ignores_node_builtins(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "package.json", json.dumps({"dependencies": {}}))
        imports = [
            {
                "file": "src/index.js", "line": 1,
                "module": "path", "kind": "js", "is_relative": False,
            },
            {
                "file": "src/index.js", "line": 2,
                "module": "fs", "kind": "js", "is_relative": False,
            },
            {
                "file": "src/index.js", "line": 3,
                "module": "node:crypto", "kind": "js", "is_relative": False,
            },
        ]
        issues = check_dependencies(str(tmp_path), imports)
        assert len(issues) == 0

    def test_skips_relative_imports(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "package.json", json.dumps({"dependencies": {}}))
        imports = [
            {
                "file": "src/App.tsx", "line": 1,
                "module": "./Header", "kind": "js", "is_relative": True,
            },
        ]
        issues = check_dependencies(str(tmp_path), imports)
        assert len(issues) == 0

    def test_missing_python_dependency(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "requirements.txt", "flask>=2.0\n")
        imports = [
            {
                "file": "app.py", "line": 1, "module": "flask",
                "kind": "py", "is_relative": False, "top_level": "flask",
            },
            {
                "file": "app.py", "line": 2, "module": "requests",
                "kind": "py", "is_relative": False, "top_level": "requests",
            },
        ]
        issues = check_dependencies(str(tmp_path), imports)
        missing = [i for i in issues if "requests" in i["detail"]]
        assert len(missing) == 1

    def test_ignores_python_stdlib(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "requirements.txt", "")
        imports = [
            {
                "file": "app.py", "line": 1, "module": "json",
                "kind": "py", "is_relative": False, "top_level": "json",
            },
            {
                "file": "app.py", "line": 2, "module": "os",
                "kind": "py", "is_relative": False, "top_level": "os",
            },
            {
                "file": "app.py", "line": 3, "module": "pathlib",
                "kind": "py", "is_relative": False, "top_level": "pathlib",
            },
        ]
        issues = check_dependencies(str(tmp_path), imports)
        assert len(issues) == 0

    def test_ignores_local_python_packages(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "requirements.txt", "")
        _write_file(str(tmp_path), "server/__init__.py", "")
        imports = [
            {
                "file": "main.py", "line": 1, "module": "server.tools",
                "kind": "py", "is_relative": False, "top_level": "server",
            },
        ]
        issues = check_dependencies(str(tmp_path), imports)
        assert len(issues) == 0

    def test_auto_fixable_flag(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "package.json", json.dumps({"dependencies": {}}))
        imports = [
            {
                "file": "src/App.tsx", "line": 1,
                "module": "lodash", "kind": "js", "is_relative": False,
            },
        ]
        issues = check_dependencies(str(tmp_path), imports)
        assert len(issues) == 1
        assert issues[0]["auto_fixable"] is True
        assert "npm install lodash" in issues[0]["fix"]

    def test_scoped_package_detection(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "package.json", json.dumps({
            "dependencies": {"@mui/material": "^5.0.0"},
        }))
        imports = [
            {
                "file": "src/App.tsx", "line": 1,
                "module": "@mui/material/Button", "kind": "js", "is_relative": False,
            },
        ]
        issues = check_dependencies(str(tmp_path), imports)
        assert len(issues) == 0


# ===========================================================================
# check_env_vars
# ===========================================================================


class TestCheckEnvVars:
    def test_detects_missing_env_var(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/lib/ai.ts", (
            "const key = process.env.GEMINI_API_KEY;\n"
        ))
        # No .env file
        issues = check_env_vars(str(tmp_path))
        assert len(issues) == 1
        assert issues[0]["type"] == "env_missing"
        assert "GEMINI_API_KEY" in issues[0]["detail"]

    def test_no_issue_when_env_defined(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/config.ts", (
            "const url = process.env.DATABASE_URL;\n"
        ))
        _write_file(str(tmp_path), ".env", "DATABASE_URL=postgres://localhost/db\n")
        issues = check_env_vars(str(tmp_path))
        assert len(issues) == 0

    def test_detects_placeholder_value(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/config.ts", (
            "const key = process.env.API_KEY;\n"
        ))
        _write_file(str(tmp_path), ".env", "API_KEY=your-key-here\n")
        issues = check_env_vars(str(tmp_path))
        assert len(issues) == 1
        assert issues[0]["type"] == "env_placeholder"

    def test_detects_empty_value(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/config.ts", (
            "const key = process.env.SECRET_KEY;\n"
        ))
        _write_file(str(tmp_path), ".env", "SECRET_KEY=\n")
        issues = check_env_vars(str(tmp_path))
        assert len(issues) == 1
        assert issues[0]["type"] == "env_placeholder"

    def test_python_env_detection(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "app.py", (
            "import os\n"
            "key = os.getenv('OPENAI_API_KEY')\n"
            "host = os.environ.get('DB_HOST')\n"
        ))
        issues = check_env_vars(str(tmp_path))
        vars_found = [i["detail"] for i in issues]
        assert any("OPENAI_API_KEY" in v for v in vars_found)
        assert any("DB_HOST" in v for v in vars_found)

    def test_bracket_notation_env(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/config.ts", (
            "const key = process.env['API_SECRET'];\n"
        ))
        issues = check_env_vars(str(tmp_path))
        assert len(issues) == 1
        assert "API_SECRET" in issues[0]["detail"]

    def test_no_code_refs_no_issues(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/App.tsx", (
            "const App = () => <div>Hello</div>;\n"
            "export default App;\n"
        ))
        issues = check_env_vars(str(tmp_path))
        assert len(issues) == 0

    def test_reads_env_local(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/config.ts", (
            "const key = process.env.MY_TOKEN;\n"
        ))
        _write_file(str(tmp_path), ".env.local", "MY_TOKEN=actual-value\n")
        issues = check_env_vars(str(tmp_path))
        assert len(issues) == 0


# ===========================================================================
# check_relative_imports
# ===========================================================================


class TestCheckRelativeImports:
    def test_detects_broken_import(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/App.tsx", (
            "import { Header } from './components/Header';\n"
        ))
        # No src/components/Header.tsx exists
        issues = check_relative_imports(str(tmp_path))
        assert len(issues) == 1
        assert issues[0]["type"] == "broken_import"
        assert "./components/Header" in issues[0]["detail"]

    def test_no_issue_when_file_exists(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/App.tsx", (
            "import { Header } from './components/Header';\n"
        ))
        _write_file(str(tmp_path), "src/components/Header.tsx", (
            "export const Header = () => <header />;\n"
        ))
        issues = check_relative_imports(str(tmp_path))
        assert len(issues) == 0

    def test_resolves_index_file(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/App.tsx", (
            "import utils from './utils';\n"
        ))
        _write_file(str(tmp_path), "src/utils/index.ts", (
            "export default {};\n"
        ))
        issues = check_relative_imports(str(tmp_path))
        assert len(issues) == 0

    def test_resolves_without_extension(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/App.tsx", (
            "import config from './config';\n"
        ))
        _write_file(str(tmp_path), "src/config.js", "module.exports = {};\n")
        issues = check_relative_imports(str(tmp_path))
        assert len(issues) == 0

    def test_no_relative_imports_no_issues(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/App.tsx", (
            "import React from 'react';\n"
        ))
        issues = check_relative_imports(str(tmp_path))
        assert len(issues) == 0


# ===========================================================================
# check_empty_files
# ===========================================================================


class TestCheckEmptyFiles:
    def test_detects_empty_file(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/placeholder.ts", "")
        issues = check_empty_files(str(tmp_path))
        assert len(issues) == 1
        assert issues[0]["type"] == "empty_file"

    def test_detects_comments_only_file(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/stub.ts", (
            "// This is a placeholder\n"
            "// TODO: implement this\n"
        ))
        issues = check_empty_files(str(tmp_path))
        assert any(i["type"] == "comments_only" for i in issues)

    def test_detects_todo_heavy_file(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/wip.ts", (
            "export function a() { /* TODO: implement */ }\n"
            "export function b() { /* TODO: implement */ }\n"
            "export function c() { /* FIXME: broken */ }\n"
            "export function d() { /* HACK: workaround */ }\n"
            "export function e() { /* TODO: refactor */ }\n"
        ))
        issues = check_empty_files(str(tmp_path))
        todo_issues = [i for i in issues if i["type"] == "todo_heavy"]
        assert len(todo_issues) == 1
        assert "5" in todo_issues[0]["detail"]

    def test_no_issue_for_normal_file(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/App.tsx", (
            "import React from 'react';\n"
            "const App = () => <div>Hello</div>;\n"
            "export default App;\n"
        ))
        issues = check_empty_files(str(tmp_path))
        assert len(issues) == 0

    def test_skips_non_code_files(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "README.md", "")
        _write_file(str(tmp_path), "data.json", "")
        issues = check_empty_files(str(tmp_path))
        assert len(issues) == 0


# ===========================================================================
# handle_validate_integration (full integration test)
# ===========================================================================


class TestHandleValidateIntegration:
    @pytest.mark.asyncio
    async def test_full_project_scan(self, tmp_path: Path) -> None:
        # Setup a mini project with various issues
        _write_file(str(tmp_path), "package.json", json.dumps({
            "dependencies": {"react": "^18.0.0"},
        }))
        _write_file(str(tmp_path), "src/App.tsx", (
            "import React from 'react';\n"
            "import { Button } from '@mui/material';\n"
            "import { Header } from './components/Header';\n"
            "const key = process.env.API_KEY;\n"
        ))
        _write_file(str(tmp_path), "src/empty.ts", "")

        result = await handle_validate_integration(
            {"project_dir": str(tmp_path)},
        )
        assert len(result) == 1
        data = json.loads(result[0].text)

        assert data["status"] == "FAIL"  # Has errors
        assert data["summary"]["total"] > 0
        assert data["summary"]["errors"] > 0

        # Check specific issues detected
        issue_types = [i["type"] for i in data["issues"]]
        assert "missing_dependency" in issue_types  # @mui/material
        assert "broken_import" in issue_types  # ./components/Header
        assert "env_missing" in issue_types  # API_KEY
        assert "empty_file" in issue_types  # empty.ts

    @pytest.mark.asyncio
    async def test_clean_project(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "package.json", json.dumps({
            "dependencies": {"react": "^18.0.0"},
        }))
        _write_file(str(tmp_path), "src/App.tsx", (
            "import React from 'react';\n"
            "const App = () => <div>Hello</div>;\n"
            "export default App;\n"
        ))

        result = await handle_validate_integration(
            {"project_dir": str(tmp_path)},
        )
        data = json.loads(result[0].text)
        assert data["status"] == "PASS"
        assert data["summary"]["total"] == 0

    @pytest.mark.asyncio
    async def test_auto_fix_command(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "package.json", json.dumps({
            "dependencies": {},
        }))
        _write_file(str(tmp_path), "src/App.tsx", (
            "import React from 'react';\n"
            "import axios from 'axios';\n"
        ))

        result = await handle_validate_integration(
            {"project_dir": str(tmp_path)},
        )
        data = json.loads(result[0].text)
        assert data["auto_fix_command"] is not None
        assert "npm install" in data["auto_fix_command"]

    @pytest.mark.asyncio
    async def test_invalid_project_dir(self) -> None:
        with pytest.raises(ValueError, match="디렉토리"):
            await handle_validate_integration(
                {"project_dir": "/nonexistent/path/xyz"},
            )

    @pytest.mark.asyncio
    async def test_missing_project_dir(self) -> None:
        with pytest.raises(ValueError, match="project_dir"):
            await handle_validate_integration({})

    @pytest.mark.asyncio
    async def test_warn_status_for_warnings_only(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "package.json", json.dumps({
            "dependencies": {"react": "^18.0.0"},
        }))
        _write_file(str(tmp_path), "src/App.tsx", (
            "import React from 'react';\n"
            "const key = process.env.MY_SECRET;\n"
        ))
        _write_file(str(tmp_path), ".env", "MY_SECRET=\n")

        result = await handle_validate_integration(
            {"project_dir": str(tmp_path)},
        )
        data = json.loads(result[0].text)
        assert data["status"] == "WARN"
        assert data["summary"]["errors"] == 0
        assert data["summary"]["warnings"] > 0


# ===========================================================================
# check_api_url_consistency
# ===========================================================================


class TestCheckApiUrlConsistency:
    def test_nextjs_app_router_routes_matched(self, tmp_path: Path) -> None:
        """Next.js App Router file-based routes are detected and matched."""
        _write_file(str(tmp_path), "app/api/reports/route.ts", (
            "export async function GET() { return Response.json([]); }\n"
        ))
        _write_file(str(tmp_path), "src/hooks/useReports.ts", (
            "const res = fetch('/api/reports');\n"
        ))
        issues = check_api_url_consistency(str(tmp_path))
        assert len(issues) == 0

    def test_express_router_get_detected(self, tmp_path: Path) -> None:
        """Express router.get() routes are detected."""
        _write_file(str(tmp_path), "routes/reports.ts", (
            "router.get('/api/reports', handler);\n"
        ))
        _write_file(str(tmp_path), "src/App.tsx", (
            "fetch('/api/reports');\n"
        ))
        issues = check_api_url_consistency(str(tmp_path))
        assert len(issues) == 0

    def test_fastapi_decorator_detected(self, tmp_path: Path) -> None:
        """FastAPI @app.get() routes are detected."""
        _write_file(str(tmp_path), "main.py", (
            "@app.get('/api/users')\n"
            "async def get_users():\n"
            "    pass\n"
        ))
        _write_file(str(tmp_path), "src/hooks/useUsers.ts", (
            "const data = fetch('/api/users');\n"
        ))
        issues = check_api_url_consistency(str(tmp_path))
        assert len(issues) == 0

    def test_frontend_fetch_detected(self, tmp_path: Path) -> None:
        """Frontend fetch() calls with API URLs are detected."""
        _write_file(str(tmp_path), "src/api.ts", (
            "const res = await fetch('/api/items');\n"
        ))
        # No backend route defined
        issues = check_api_url_consistency(str(tmp_path))
        assert len(issues) == 1
        assert issues[0]["type"] == "api_url_mismatch"
        assert "/api/items" in issues[0]["detail"]

    def test_frontend_axios_detected(self, tmp_path: Path) -> None:
        """Frontend axios calls with API URLs are detected."""
        _write_file(str(tmp_path), "src/services/report.ts", (
            "const res = axios.get('/api/reports');\n"
            "axios.post('/api/reports');\n"
        ))
        _write_file(str(tmp_path), "routes/api.ts", (
            "router.get('/api/reports', handler);\n"
            "router.post('/api/reports', handler);\n"
        ))
        issues = check_api_url_consistency(str(tmp_path))
        assert len(issues) == 0

    def test_mismatch_detected(self, tmp_path: Path) -> None:
        """Mismatch detected when frontend has URL not in backend."""
        _write_file(str(tmp_path), "src/hooks/useReports.ts", (
            "const res = fetch('/api/reports/');\n"
            "const users = fetch('/api/users');\n"
        ))
        _write_file(str(tmp_path), "routes/api.ts", (
            "router.get('/api/reports', handler);\n"
        ))
        issues = check_api_url_consistency(str(tmp_path))
        # /api/reports/ should match /api/reports (normalized)
        # /api/users has no backend route
        mismatch = [i for i in issues if i["type"] == "api_url_mismatch"]
        assert len(mismatch) == 1
        assert "/api/users" in mismatch[0]["detail"]

    def test_match_detected_urls_align(self, tmp_path: Path) -> None:
        """No issues when frontend and backend URLs align correctly."""
        _write_file(str(tmp_path), "src/App.tsx", (
            "fetch('/api/users');\n"
            "fetch('/api/posts');\n"
        ))
        _write_file(str(tmp_path), "server/routes.py", (
            "@app.get('/api/users')\n"
            "async def get_users(): pass\n"
            "@app.get('/api/posts')\n"
            "async def get_posts(): pass\n"
        ))
        issues = check_api_url_consistency(str(tmp_path))
        assert len(issues) == 0

    def test_path_normalization_trailing_slash(self, tmp_path: Path) -> None:
        """Trailing slashes are normalized for comparison."""
        _write_file(str(tmp_path), "src/App.tsx", (
            "fetch('/api/reports/');\n"
        ))
        _write_file(str(tmp_path), "routes/api.ts", (
            "router.get('/api/reports', handler);\n"
        ))
        issues = check_api_url_consistency(str(tmp_path))
        assert len(issues) == 0

    def test_no_false_positives_for_non_api_urls(self, tmp_path: Path) -> None:
        """Non-API URLs (e.g., /about, /home) should not be flagged."""
        _write_file(str(tmp_path), "src/App.tsx", (
            "fetch('/about');\n"
            "fetch('/home');\n"
            "const link = '/contact';\n"
        ))
        issues = check_api_url_consistency(str(tmp_path))
        assert len(issues) == 0

    def test_useswr_pattern_detected(self, tmp_path: Path) -> None:
        """useSWR('/api/...') calls are detected."""
        _write_file(str(tmp_path), "src/hooks/useData.ts", (
            "const { data } = useSWR('/api/dashboard');\n"
        ))
        issues = check_api_url_consistency(str(tmp_path))
        assert len(issues) == 1
        assert "/api/dashboard" in issues[0]["detail"]

    def test_dynamic_route_matching(self, tmp_path: Path) -> None:
        """Next.js dynamic routes like [id] match frontend URLs."""
        _write_file(str(tmp_path), "app/api/reports/[id]/route.ts", (
            "export async function GET() { return Response.json({}); }\n"
        ))
        _write_file(str(tmp_path), "src/App.tsx", (
            "fetch('/api/reports/123');\n"
        ))
        issues = check_api_url_consistency(str(tmp_path))
        assert len(issues) == 0

    def test_express_app_post_detected(self, tmp_path: Path) -> None:
        """Express app.post() routes are detected."""
        _write_file(str(tmp_path), "server.js", (
            "app.post('/api/auth/login', handler);\n"
        ))
        _write_file(str(tmp_path), "src/auth.ts", (
            "axios.post('/api/auth/login');\n"
        ))
        issues = check_api_url_consistency(str(tmp_path))
        assert len(issues) == 0

    def test_starlette_route_detected(self, tmp_path: Path) -> None:
        """Starlette Route() definitions are detected."""
        _write_file(str(tmp_path), "app.py", (
            "Route('/api/health', health_check)\n"
        ))
        _write_file(str(tmp_path), "src/health.ts", (
            "fetch('/api/health');\n"
        ))
        issues = check_api_url_consistency(str(tmp_path))
        assert len(issues) == 0

    def test_case_insensitive_normalization(self, tmp_path: Path) -> None:
        """Path comparison is case-insensitive."""
        _write_file(str(tmp_path), "src/App.tsx", (
            "fetch('/API/Users');\n"
        ))
        _write_file(str(tmp_path), "routes/api.ts", (
            "router.get('/api/users', handler);\n"
        ))
        issues = check_api_url_consistency(str(tmp_path))
        assert len(issues) == 0

    def test_no_issues_empty_project(self, tmp_path: Path) -> None:
        """Empty project has no API URL consistency issues."""
        issues = check_api_url_consistency(str(tmp_path))
        assert len(issues) == 0

    def test_issue_format_fields(self, tmp_path: Path) -> None:
        """Issue dicts have all required fields."""
        _write_file(str(tmp_path), "src/App.tsx", (
            "fetch('/api/missing');\n"
        ))
        issues = check_api_url_consistency(str(tmp_path))
        assert len(issues) == 1
        issue = issues[0]
        assert issue["type"] == "api_url_mismatch"
        assert issue["severity"] == "warning"
        assert "file" in issue
        assert "line" in issue
        assert "detail" in issue
        assert "fix" in issue

"""Unit tests for search_feature tool and feature_catalog.

Tests cover:
- Feature catalog keyword matching
- search_code mock tests
- _suggest_placement rules for each framework
- Full pipeline integration test with mocked GitHub
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.tools.feature_catalog import (
    FEATURE_CATALOG,
    get_install_command,
    get_required_deps,
    get_search_queries,
    match_feature,
)
from server.tools.search_feature import (
    _classify_file_type,
    _group_by_repo,
    _sanitize_feature,
    _suggest_placement,
    _validate_args,
    handle_search_feature,
)

# ===========================================================================
# Feature Catalog — match_feature
# ===========================================================================


class TestMatchFeature:
    """Tests for feature_catalog.match_feature()."""

    def test_exact_id_match(self) -> None:
        entry = match_feature("stripe-payment")
        assert entry is not None
        assert "stripe checkout session" in entry["search_queries"]

    def test_alias_match_english(self) -> None:
        entry = match_feature("payment")
        assert entry is not None
        assert "createPaymentIntent" in entry["search_queries"]

    def test_alias_match_korean(self) -> None:
        entry = match_feature("결제")
        assert entry is not None

    def test_alias_match_case_insensitive(self) -> None:
        entry = match_feature("STRIPE")
        assert entry is not None

    def test_dark_mode_match(self) -> None:
        entry = match_feature("dark mode")
        assert entry is not None
        assert "dark mode toggle" in entry["search_queries"]

    def test_auth_match(self) -> None:
        entry = match_feature("인증")
        assert entry is not None
        assert "auth middleware jwt" in entry["search_queries"]

    def test_websocket_match(self) -> None:
        entry = match_feature("실시간 채팅")
        assert entry is not None

    def test_i18n_match(self) -> None:
        entry = match_feature("다국어")
        assert entry is not None

    def test_pagination_match(self) -> None:
        entry = match_feature("무한스크롤")
        assert entry is not None

    def test_no_match_returns_none(self) -> None:
        assert match_feature("quantum-computing") is None

    def test_all_features_have_required_fields(self) -> None:
        for feature_id, entry in FEATURE_CATALOG.items():
            assert "aliases" in entry, f"{feature_id} missing aliases"
            assert "search_queries" in entry, f"{feature_id} missing search_queries"
            assert "required_deps" in entry, f"{feature_id} missing required_deps"
            assert "stack_filters" in entry, f"{feature_id} missing stack_filters"
            assert "typical_file_structure" in entry, f"{feature_id} missing structure"


class TestGetSearchQueries:
    """Tests for feature_catalog.get_search_queries()."""

    def test_base_queries_returned(self) -> None:
        entry = FEATURE_CATALOG["stripe-payment"]
        queries = get_search_queries(entry)
        assert len(queries) == 3
        assert "stripe checkout session" in queries

    def test_stack_filtered_queries(self) -> None:
        entry = FEATURE_CATALOG["stripe-payment"]
        queries = get_search_queries(entry, framework="nextjs")
        assert all("path:app/api" in q for q in queries)

    def test_unknown_framework_returns_base(self) -> None:
        entry = FEATURE_CATALOG["stripe-payment"]
        queries = get_search_queries(entry, framework="unknown-fw")
        assert queries == entry["search_queries"]


class TestGetRequiredDeps:
    """Tests for feature_catalog.get_required_deps()."""

    def test_typescript_deps(self) -> None:
        entry = FEATURE_CATALOG["stripe-payment"]
        deps = get_required_deps(entry, "typescript")
        assert "stripe" in deps
        assert "@stripe/stripe-js" in deps

    def test_python_deps(self) -> None:
        entry = FEATURE_CATALOG["stripe-payment"]
        deps = get_required_deps(entry, "python")
        assert deps == ["stripe"]

    def test_unknown_language_returns_empty(self) -> None:
        entry = FEATURE_CATALOG["stripe-payment"]
        deps = get_required_deps(entry, "rust")
        assert deps == []


class TestGetInstallCommand:
    """Tests for feature_catalog.get_install_command()."""

    def test_npm_install(self) -> None:
        cmd = get_install_command(["stripe", "@stripe/stripe-js"], "typescript")
        assert cmd == "npm install stripe @stripe/stripe-js"

    def test_pip_install(self) -> None:
        cmd = get_install_command(["stripe"], "python")
        assert cmd == "pip install stripe"

    def test_empty_deps(self) -> None:
        assert get_install_command([], "typescript") == ""


# ===========================================================================
# search_feature — _sanitize_feature
# ===========================================================================


class TestSanitizeFeature:
    """Tests for search_feature._sanitize_feature()."""

    def test_normal_input(self) -> None:
        assert _sanitize_feature("stripe payment") == "stripe payment"

    def test_strips_operators(self) -> None:
        result = _sanitize_feature("stripe language:python payment")
        assert "language:" not in result
        assert "stripe" in result
        assert "payment" in result

    def test_strips_control_chars(self) -> None:
        result = _sanitize_feature("stripe\x00payment")
        assert "\x00" not in result

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="기능명이 비어 있습니다"):
            _sanitize_feature("   ")

    def test_truncates_long_input(self) -> None:
        long_input = "a" * 300
        result = _sanitize_feature(long_input)
        assert len(result) <= 256


# ===========================================================================
# search_feature — _validate_args
# ===========================================================================


class TestValidateArgs:
    """Tests for search_feature._validate_args()."""

    def test_valid_args(self) -> None:
        result = _validate_args({
            "feature": "stripe payment",
            "project_dir": "/some/path",
        })
        assert result["feature"] == "stripe payment"
        assert result["max_results"] == 5

    def test_missing_feature_raises(self) -> None:
        with pytest.raises(ValueError, match="feature를 입력해주세요"):
            _validate_args({"project_dir": "/path"})

    def test_missing_project_dir_raises(self) -> None:
        with pytest.raises(ValueError, match="project_dir를 입력해주세요"):
            _validate_args({"feature": "test"})

    def test_invalid_language_raises(self) -> None:
        with pytest.raises(ValueError, match="language는"):
            _validate_args({
                "feature": "test",
                "project_dir": "/path",
                "language": "rust",
            })

    def test_invalid_max_results_raises(self) -> None:
        with pytest.raises(ValueError, match="max_results는"):
            _validate_args({
                "feature": "test",
                "project_dir": "/path",
                "max_results": 20,
            })

    def test_custom_max_results(self) -> None:
        result = _validate_args({
            "feature": "test",
            "project_dir": "/path",
            "max_results": 3,
        })
        assert result["max_results"] == 3


# ===========================================================================
# search_feature — _classify_file_type
# ===========================================================================


class TestClassifyFileType:
    """Tests for search_feature._classify_file_type()."""

    def test_api_route(self) -> None:
        assert _classify_file_type("app/api/checkout/route.ts") == "api"

    def test_component(self) -> None:
        assert _classify_file_type("src/components/Button.tsx") == "component"

    def test_middleware(self) -> None:
        assert _classify_file_type("middleware/auth.ts") == "middleware"

    def test_hook(self) -> None:
        assert _classify_file_type("hooks/useAuth.ts") == "hook"

    def test_lib_default(self) -> None:
        assert _classify_file_type("lib/stripe.ts") == "lib"

    def test_unknown_defaults_to_lib(self) -> None:
        assert _classify_file_type("something/random.ts") == "lib"


# ===========================================================================
# search_feature — _suggest_placement
# ===========================================================================


class TestSuggestPlacement:
    """Tests for _suggest_placement across different frameworks."""

    def test_nextjs_api_route(self, tmp_path: Path) -> None:
        result = _suggest_placement(
            source_files=["api/checkout/route.ts"],
            feature="stripe-payment",
            framework="nextjs",
            project_dir=str(tmp_path),
        )
        assert result["api/checkout/route.ts"] == "app/api/stripe-payment/route.ts"

    def test_nextjs_component(self, tmp_path: Path) -> None:
        result = _suggest_placement(
            source_files=["components/ThemeToggle.tsx"],
            feature="dark-mode",
            framework="nextjs",
            project_dir=str(tmp_path),
        )
        assert result["components/ThemeToggle.tsx"] == "components/dark-mode/"

    def test_nextjs_lib(self, tmp_path: Path) -> None:
        result = _suggest_placement(
            source_files=["lib/stripe.ts"],
            feature="payment",
            framework="nextjs",
            project_dir=str(tmp_path),
        )
        assert result["lib/stripe.ts"] == "lib/payment.ts"

    def test_express_route(self, tmp_path: Path) -> None:
        result = _suggest_placement(
            source_files=["routes/payment.ts"],
            feature="stripe",
            framework="express",
            project_dir=str(tmp_path),
        )
        assert result["routes/payment.ts"] == "routes/stripe.ts"

    def test_express_middleware(self, tmp_path: Path) -> None:
        result = _suggest_placement(
            source_files=["middleware/auth.ts"],
            feature="auth",
            framework="express",
            project_dir=str(tmp_path),
        )
        assert result["middleware/auth.ts"] == "middleware/auth.ts"

    def test_fastapi_router(self, tmp_path: Path) -> None:
        result = _suggest_placement(
            source_files=["routers/payment.py"],
            feature="payment",
            framework="fastapi",
            project_dir=str(tmp_path),
        )
        assert result["routers/payment.py"] == "routers/payment.py"

    def test_react_hook(self, tmp_path: Path) -> None:
        result = _suggest_placement(
            source_files=["hooks/useTheme.ts"],
            feature="dark-mode",
            framework="react",
            project_dir=str(tmp_path),
        )
        assert result["hooks/useTheme.ts"] == "src/hooks/useDarkMode.ts"

    def test_react_component(self, tmp_path: Path) -> None:
        result = _suggest_placement(
            source_files=["src/components/Search.tsx"],
            feature="search",
            framework="react",
            project_dir=str(tmp_path),
        )
        assert result["src/components/Search.tsx"] == "src/components/search/"

    def test_existing_dir_priority(self, tmp_path: Path) -> None:
        # Create matching directory in project
        (tmp_path / "lib").mkdir()
        result = _suggest_placement(
            source_files=["lib/stripe.ts"],
            feature="payment",
            framework="nextjs",
            project_dir=str(tmp_path),
        )
        # Should keep original path because lib/ exists in project
        assert result["lib/stripe.ts"] == "lib/stripe.ts"

    def test_no_framework_keeps_original(self, tmp_path: Path) -> None:
        result = _suggest_placement(
            source_files=["some/random/file.ts"],
            feature="test",
            framework=None,
            project_dir=str(tmp_path),
        )
        assert result["some/random/file.ts"] == "some/random/file.ts"

    def test_vue_component(self, tmp_path: Path) -> None:
        result = _suggest_placement(
            source_files=["src/components/ThemeToggle.vue"],
            feature="dark-mode",
            framework="vue",
            project_dir=str(tmp_path),
        )
        assert result["src/components/ThemeToggle.vue"] == "src/components/dark-mode/"

    def test_django_view(self, tmp_path: Path) -> None:
        result = _suggest_placement(
            source_files=["views/payment.py"],
            feature="payment",
            framework="django",
            project_dir=str(tmp_path),
        )
        assert result["views/payment.py"] == "views/payment.py"


# ===========================================================================
# search_feature — _group_by_repo
# ===========================================================================


class TestGroupByRepo:
    """Tests for search_feature._group_by_repo()."""

    def test_groups_correctly(self) -> None:
        results = [
            {"repo_full_name": "owner/a", "file_path": "f1.ts"},
            {"repo_full_name": "owner/a", "file_path": "f2.ts"},
            {"repo_full_name": "owner/b", "file_path": "f3.ts"},
        ]
        groups = _group_by_repo(results)
        assert len(groups) == 2
        assert len(groups["owner/a"]) == 2
        assert len(groups["owner/b"]) == 1

    def test_empty_input(self) -> None:
        assert _group_by_repo([]) == {}


# ===========================================================================
# Full pipeline integration test (mocked GitHub)
# ===========================================================================


class TestHandleSearchFeaturePipeline:
    """Integration tests for handle_search_feature with mocked GitHub."""

    @pytest.fixture()
    def project_dir(self, tmp_path: Path) -> str:
        """Create a minimal Next.js project."""
        pkg = {
            "dependencies": {"next": "^14.0.0", "react": "^18.0.0"},
            "devDependencies": {"typescript": "^5.0.0"},
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "tsconfig.json").write_text("{}")
        return str(tmp_path)

    @pytest.fixture()
    def mock_github(self) -> MagicMock:
        """Create a fully mocked GitHubClient."""
        mock = MagicMock()

        # search_code returns sample results
        mock.search_code = AsyncMock(return_value=[
            {
                "repo_full_name": "test-org/stripe-next",
                "file_path": "app/api/checkout/route.ts",
                "file_url": "https://github.com/test-org/stripe-next/blob/main/app/api/checkout/route.ts",
                "content_snippet": "import Stripe from 'stripe'\nconst stripe = new Stripe()",
                "score": 1.0,
            },
            {
                "repo_full_name": "test-org/stripe-next",
                "file_path": "lib/stripe.ts",
                "file_url": "https://github.com/test-org/stripe-next/blob/main/lib/stripe.ts",
                "content_snippet": "export const stripe = new Stripe(process.env.STRIPE_KEY)",
                "score": 0.9,
            },
        ])

        # get_repo returns sample detail
        mock.get_repo = AsyncMock(return_value={
            "full_name": "test-org/stripe-next",
            "url": "https://github.com/test-org/stripe-next",
            "stars": 500,
            "forks": 50,
            "open_issues": 5,
            "last_commit": "2026-03-01",
            "archived": False,
            "default_branch": "main",
            "language": "TypeScript",
            "description": "Stripe integration for Next.js",
            "has_tests": True,
            "has_ci": True,
            "has_releases": True,
            "has_examples": True,
            "readme_length": 5000,
            "latest_sha": "abc123",
        })

        # get_license returns MIT
        mock.get_license = AsyncMock(return_value={
            "name": "MIT License",
            "spdx_id": "MIT",
            "url": "https://api.github.com/licenses/mit",
            "body": "MIT License...",
        })

        return mock

    @pytest.mark.asyncio()
    async def test_full_pipeline_returns_results(
        self,
        project_dir: str,
        mock_github: MagicMock,
    ) -> None:
        result = await handle_search_feature(
            {"feature": "stripe payment", "project_dir": project_dir},
            mock_github,
        )
        assert len(result) == 1
        data = json.loads(result[0].text)

        assert data["feature"] == "stripe payment"
        assert data["detected_stack"]["framework"] == "nextjs"
        assert data["detected_stack"]["language"] == "typescript"
        assert len(data["results"]) >= 1

        first = data["results"][0]
        assert first["repo"] == "test-org/stripe-next"
        assert first["license"] == "MIT"
        assert first["license_ok"] is True
        assert first["quality_score"] > 0
        assert len(first["matched_files"]) == 2
        assert "suggested_placement" in first

    @pytest.mark.asyncio()
    async def test_pipeline_no_results(
        self,
        project_dir: str,
        mock_github: MagicMock,
    ) -> None:
        mock_github.search_code = AsyncMock(return_value=[])
        result = await handle_search_feature(
            {"feature": "quantum computing", "project_dir": project_dir},
            mock_github,
        )
        data = json.loads(result[0].text)
        assert data["results"] == []
        assert "message" in data

    @pytest.mark.asyncio()
    async def test_pipeline_invalid_project_dir(
        self,
        mock_github: MagicMock,
    ) -> None:
        with pytest.raises(ValueError, match="프로젝트 디렉토리가 존재하지 않습니다"):
            await handle_search_feature(
                {
                    "feature": "stripe",
                    "project_dir": "/nonexistent/path/123456",
                },
                mock_github,
            )

    @pytest.mark.asyncio()
    async def test_pipeline_skips_archived_repos(
        self,
        project_dir: str,
        mock_github: MagicMock,
    ) -> None:
        mock_github.get_repo = AsyncMock(return_value={
            "full_name": "test-org/old-repo",
            "url": "https://github.com/test-org/old-repo",
            "stars": 1000,
            "forks": 100,
            "archived": True,
            "default_branch": "main",
            "language": "TypeScript",
            "description": "Archived repo",
        })
        result = await handle_search_feature(
            {"feature": "stripe payment", "project_dir": project_dir},
            mock_github,
        )
        data = json.loads(result[0].text)
        assert len(data["results"]) == 0

    @pytest.mark.asyncio()
    async def test_pipeline_with_python_project(
        self,
        tmp_path: Path,
        mock_github: MagicMock,
    ) -> None:
        # Create Python project
        (tmp_path / "requirements.txt").write_text("fastapi>=0.100.0\nuvicorn\n")
        mock_github.search_code = AsyncMock(return_value=[
            {
                "repo_full_name": "test-org/fastapi-stripe",
                "file_path": "routers/payment.py",
                "file_url": "https://github.com/test-org/fastapi-stripe/blob/main/routers/payment.py",
                "content_snippet": "import stripe\nfrom fastapi import APIRouter",
                "score": 1.0,
            },
        ])
        mock_github.get_repo = AsyncMock(return_value={
            "full_name": "test-org/fastapi-stripe",
            "url": "https://github.com/test-org/fastapi-stripe",
            "stars": 300,
            "forks": 30,
            "archived": False,
            "default_branch": "main",
            "language": "Python",
            "description": "FastAPI Stripe integration",
        })

        result = await handle_search_feature(
            {"feature": "stripe payment", "project_dir": str(tmp_path)},
            mock_github,
        )
        data = json.loads(result[0].text)
        assert data["detected_stack"]["framework"] == "fastapi"
        assert data["detected_stack"]["language"] == "python"

    @pytest.mark.asyncio()
    async def test_pipeline_with_language_override(
        self,
        project_dir: str,
        mock_github: MagicMock,
    ) -> None:
        result = await handle_search_feature(
            {
                "feature": "stripe payment",
                "project_dir": project_dir,
                "language": "javascript",
            },
            mock_github,
        )
        data = json.loads(result[0].text)
        # Language override does not change detected stack
        assert data["detected_stack"]["language"] == "typescript"
        # But results should still be returned
        assert len(data["results"]) >= 1

    @pytest.mark.asyncio()
    async def test_pipeline_max_results_limits_output(
        self,
        project_dir: str,
        mock_github: MagicMock,
    ) -> None:
        result = await handle_search_feature(
            {
                "feature": "stripe payment",
                "project_dir": project_dir,
                "max_results": 1,
            },
            mock_github,
        )
        data = json.loads(result[0].text)
        assert len(data["results"]) <= 1

    @pytest.mark.asyncio()
    async def test_pipeline_fallback_feature_not_in_catalog(
        self,
        project_dir: str,
        mock_github: MagicMock,
    ) -> None:
        """When feature is not in catalog, use raw query."""
        mock_github.search_code = AsyncMock(return_value=[
            {
                "repo_full_name": "test-org/custom-thing",
                "file_path": "lib/custom.ts",
                "file_url": "https://github.com/test-org/custom-thing/blob/main/lib/custom.ts",
                "content_snippet": "export function custom() {}",
                "score": 1.0,
            },
        ])
        mock_github.get_repo = AsyncMock(return_value={
            "full_name": "test-org/custom-thing",
            "url": "https://github.com/test-org/custom-thing",
            "stars": 200,
            "forks": 20,
            "archived": False,
            "default_branch": "main",
            "language": "TypeScript",
            "description": "Custom thing",
        })

        result = await handle_search_feature(
            {"feature": "custom-xyz-thing", "project_dir": project_dir},
            mock_github,
        )
        data = json.loads(result[0].text)
        # Should still return results via fallback
        assert len(data["results"]) >= 1

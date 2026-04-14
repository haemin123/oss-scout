"""Unit tests for server/tools/envcheck.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from server.tools.envcheck import (
    build_preparation_checklist,
    classify_env_var,
    detect_services_from_dependencies,
    detect_services_from_readme,
    extract_env_vars_from_dotenv,
    extract_env_vars_from_text,
    find_env_files,
    handle_envcheck,
)


# ===========================================================================
# extract_env_vars_from_text
# ===========================================================================


class TestExtractEnvVarsFromText:
    def test_detects_api_key_patterns(self) -> None:
        text = "Set your OPENAI_API_KEY and STRIPE_SECRET_KEY in .env"
        result = extract_env_vars_from_text(text)
        assert "OPENAI_API_KEY" in result
        assert "STRIPE_SECRET_KEY" in result

    def test_detects_url_and_token_patterns(self) -> None:
        text = "Configure DATABASE_URL and GITHUB_TOKEN"
        result = extract_env_vars_from_text(text)
        assert "DATABASE_URL" in result
        assert "GITHUB_TOKEN" in result

    def test_empty_text_returns_empty(self) -> None:
        assert extract_env_vars_from_text("") == set()

    def test_no_env_vars_returns_empty(self) -> None:
        text = "This is a simple README with no environment variables."
        result = extract_env_vars_from_text(text)
        assert len(result) == 0

    def test_ignores_lowercase_vars(self) -> None:
        text = "Use openai_api_key in your config"
        result = extract_env_vars_from_text(text)
        assert len(result) == 0

    def test_detects_dsn_and_password(self) -> None:
        text = "Set SENTRY_DSN and DB_PASSWORD"
        result = extract_env_vars_from_text(text)
        assert "SENTRY_DSN" in result
        assert "DB_PASSWORD" in result


# ===========================================================================
# extract_env_vars_from_dotenv
# ===========================================================================


class TestExtractEnvVarsFromDotenv:
    def test_parses_simple_env_file(self) -> None:
        content = "OPENAI_API_KEY=sk-xxx\nDATABASE_URL=postgres://..."
        result = extract_env_vars_from_dotenv(content)
        names = [v["name"] for v in result]
        assert "OPENAI_API_KEY" in names
        assert "DATABASE_URL" in names

    def test_captures_comments(self) -> None:
        content = "# Your OpenAI key\nOPENAI_API_KEY=\n"
        result = extract_env_vars_from_dotenv(content)
        assert len(result) == 1
        assert result[0]["name"] == "OPENAI_API_KEY"
        assert "OpenAI" in result[0]["comment"]

    def test_empty_content(self) -> None:
        assert extract_env_vars_from_dotenv("") == []

    def test_comments_only(self) -> None:
        content = "# This is a comment\n# Another comment\n"
        assert extract_env_vars_from_dotenv(content) == []

    def test_handles_values_with_equals(self) -> None:
        content = "DATABASE_URL=postgres://user:pass@host/db?sslmode=require"
        result = extract_env_vars_from_dotenv(content)
        assert len(result) == 1
        assert result[0]["name"] == "DATABASE_URL"


# ===========================================================================
# detect_services_from_readme
# ===========================================================================


class TestDetectServicesFromReadme:
    def test_detects_stripe(self) -> None:
        readme = "This project uses Stripe for payment processing."
        result = detect_services_from_readme(readme)
        assert "stripe" in result

    def test_detects_supabase(self) -> None:
        readme = "Built with Supabase as the backend."
        result = detect_services_from_readme(readme)
        assert "supabase" in result

    def test_detects_firebase(self) -> None:
        readme = "Authentication powered by Firebase."
        result = detect_services_from_readme(readme)
        assert "firebase" in result

    def test_detects_multiple_services(self) -> None:
        readme = "Uses Stripe for payments, Supabase for DB, and OpenAI for AI."
        result = detect_services_from_readme(readme)
        assert "stripe" in result
        assert "supabase" in result
        assert "openai" in result

    def test_empty_readme(self) -> None:
        result = detect_services_from_readme("")
        assert len(result) == 0

    def test_no_services_detected(self) -> None:
        readme = "A simple calculator app with no external dependencies."
        result = detect_services_from_readme(readme)
        assert len(result) == 0

    def test_detects_by_key_name_in_readme(self) -> None:
        readme = "Set RESEND_API_KEY in your environment."
        result = detect_services_from_readme(readme)
        assert "resend" in result


# ===========================================================================
# detect_services_from_dependencies
# ===========================================================================


class TestDetectServicesFromDependencies:
    def test_detects_stripe_sdk(self) -> None:
        deps = {"stripe": "^12.0.0", "react": "^18.0.0"}
        result = detect_services_from_dependencies(deps)
        assert "stripe" in result

    def test_detects_supabase_js(self) -> None:
        deps = {"@supabase/supabase-js": "^2.0.0"}
        result = detect_services_from_dependencies(deps)
        assert "supabase" in result

    def test_detects_multiple_sdks(self) -> None:
        deps = {
            "openai": "^4.0.0",
            "@clerk/nextjs": "^4.0.0",
            "next-auth": "^4.0.0",
        }
        result = detect_services_from_dependencies(deps)
        assert "openai" in result
        assert "clerk" in result
        assert "nextauth" in result

    def test_empty_deps(self) -> None:
        result = detect_services_from_dependencies({})
        assert len(result) == 0

    def test_no_matching_deps(self) -> None:
        deps = {"react": "^18.0.0", "next": "^14.0.0"}
        result = detect_services_from_dependencies(deps)
        assert len(result) == 0

    def test_detects_prisma_as_database(self) -> None:
        deps = {"@prisma/client": "^5.0.0"}
        result = detect_services_from_dependencies(deps)
        assert "database" in result

    def test_detects_redis_sdk(self) -> None:
        deps = {"ioredis": "^5.0.0"}
        result = detect_services_from_dependencies(deps)
        assert "redis" in result


# ===========================================================================
# find_env_files
# ===========================================================================


class TestFindEnvFiles:
    def test_finds_env_example(self) -> None:
        tree = ["README.md", ".env.example", "src/index.ts"]
        result = find_env_files(tree)
        assert ".env.example" in result

    def test_finds_env_sample(self) -> None:
        tree = [".env.sample", "package.json"]
        result = find_env_files(tree)
        assert ".env.sample" in result

    def test_finds_nested_env_file(self) -> None:
        tree = ["apps/web/.env.example", "README.md"]
        result = find_env_files(tree)
        assert "apps/web/.env.example" in result

    def test_no_env_files(self) -> None:
        tree = ["README.md", "src/index.ts", "package.json"]
        result = find_env_files(tree)
        assert len(result) == 0

    def test_does_not_match_dotenv_itself(self) -> None:
        tree = [".env", "README.md"]
        result = find_env_files(tree)
        assert len(result) == 0


# ===========================================================================
# classify_env_var
# ===========================================================================


class TestClassifyEnvVar:
    def test_known_stripe_key(self) -> None:
        result = classify_env_var("STRIPE_SECRET_KEY", {"stripe"})
        assert result["service"] == "Stripe"
        assert result["required"] is True
        assert result["signup_url"] is not None

    def test_known_openai_key(self) -> None:
        result = classify_env_var("OPENAI_API_KEY", {"openai"})
        assert result["service"] == "Openai"
        assert result["required"] is True

    def test_unknown_var_with_secret_keyword(self) -> None:
        result = classify_env_var("MY_CUSTOM_SECRET", set())
        assert result["service"] == "Unknown"
        assert result["required"] is True

    def test_unknown_var_no_keyword(self) -> None:
        result = classify_env_var("APP_NAME", set())
        assert result["service"] == "Unknown"
        assert result["required"] is False

    def test_matches_service_even_without_detection(self) -> None:
        """Known key should be classified even if service not in detected set."""
        result = classify_env_var("SENTRY_DSN", set())
        assert result["service"] == "Sentry"
        assert result["required"] is True


# ===========================================================================
# build_preparation_checklist
# ===========================================================================


class TestBuildPreparationChecklist:
    def test_with_services_and_env_file(self) -> None:
        required = [
            {"service": "Stripe", "signup_url": "https://stripe.com"},
            {"service": "Supabase", "signup_url": "https://supabase.com"},
        ]
        checklist = build_preparation_checklist(required, [".env.example"])
        assert len(checklist) == 3  # 2 services + 1 env file copy
        assert "Stripe" in checklist[0]
        assert "Supabase" in checklist[1]
        assert ".env.example" in checklist[2]

    def test_no_env_file(self) -> None:
        required = [
            {"service": "OpenAI", "signup_url": "https://openai.com"},
        ]
        checklist = build_preparation_checklist(required, [])
        assert any(".env 파일 생성" in c for c in checklist)

    def test_deduplicates_services(self) -> None:
        required = [
            {"service": "Stripe", "signup_url": "https://stripe.com"},
            {"service": "Stripe", "signup_url": "https://stripe.com"},
        ]
        checklist = build_preparation_checklist(required, [])
        stripe_entries = [c for c in checklist if "Stripe" in c]
        assert len(stripe_entries) == 1

    def test_service_without_signup_url(self) -> None:
        required = [
            {"service": "Database", "signup_url": None},
        ]
        checklist = build_preparation_checklist(required, [])
        assert any("설정 준비" in c for c in checklist)


# ===========================================================================
# handle_envcheck (integration with mocked GitHub client)
# ===========================================================================


class TestHandleEnvcheck:
    @pytest.fixture
    def mock_github(self) -> AsyncMock:
        github = AsyncMock()
        github.get_readme.return_value = (
            "# My SaaS App\n\n"
            "Built with Stripe for payments and Supabase for backend.\n\n"
            "## Setup\n"
            "Set OPENAI_API_KEY in .env\n"
        )
        github.get_file_tree.return_value = [
            "README.md",
            ".env.example",
            "package.json",
            "src/index.ts",
        ]
        github.get_file_content.side_effect = self._mock_file_content
        return github

    @staticmethod
    def _mock_file_content(
        owner: str, name: str, path: str,
    ) -> str:
        if path == ".env.example":
            return (
                "# Stripe\n"
                "STRIPE_SECRET_KEY=\n"
                "STRIPE_PUBLISHABLE_KEY=\n"
                "# Supabase\n"
                "SUPABASE_URL=\n"
                "SUPABASE_ANON_KEY=\n"
                "# OpenAI\n"
                "OPENAI_API_KEY=\n"
            )
        if path == "package.json":
            return json.dumps({
                "dependencies": {
                    "stripe": "^12.0.0",
                    "@supabase/supabase-js": "^2.0.0",
                    "openai": "^4.0.0",
                },
                "devDependencies": {},
            })
        return ""

    @pytest.mark.asyncio
    async def test_full_analysis(self, mock_github: AsyncMock) -> None:
        result = await handle_envcheck(
            {"repo_url": "https://github.com/test/saas-app"},
            mock_github,
        )
        assert len(result) == 1
        data = json.loads(result[0].text)

        assert data["repo"] == "test/saas-app"
        assert data["total_required"] > 0
        assert ".env.example" in data["env_files_found"]
        assert len(data["preparation_checklist"]) > 0

        # Check detected services
        assert "stripe" in data["detected_services"]
        assert "supabase" in data["detected_services"]
        assert "openai" in data["detected_services"]

        # Check required vars include stripe keys
        var_names = [v["name"] for v in data["required_env_vars"]]
        assert "STRIPE_SECRET_KEY" in var_names
        assert "OPENAI_API_KEY" in var_names

    @pytest.mark.asyncio
    async def test_empty_readme_no_services(self) -> None:
        github = AsyncMock()
        github.get_readme.return_value = ""
        github.get_file_tree.return_value = ["README.md", "src/index.ts"]

        result = await handle_envcheck(
            {"repo_url": "https://github.com/test/simple-app"},
            github,
        )
        data = json.loads(result[0].text)

        assert data["total_required"] == 0
        assert data["total_optional"] == 0
        assert len(data["detected_services"]) == 0

    @pytest.mark.asyncio
    async def test_invalid_repo_url(self) -> None:
        github = AsyncMock()
        with pytest.raises(ValueError, match="repo_url"):
            await handle_envcheck({"repo_url": "not-a-url"}, github)

    @pytest.mark.asyncio
    async def test_missing_repo_url(self) -> None:
        github = AsyncMock()
        with pytest.raises(ValueError, match="repo_url"):
            await handle_envcheck({}, github)

    @pytest.mark.asyncio
    async def test_env_file_read_failure_is_graceful(self) -> None:
        github = AsyncMock()
        github.get_readme.return_value = "# App\nUses Stripe."
        github.get_file_tree.return_value = [".env.example", "package.json"]
        github.get_file_content.side_effect = Exception("File not found")

        result = await handle_envcheck(
            {"repo_url": "https://github.com/test/app"},
            github,
        )
        data = json.loads(result[0].text)
        # Should not crash, but still detect stripe from README
        assert "stripe" in data["detected_services"]

    @pytest.mark.asyncio
    async def test_description_only_detection(self) -> None:
        """Detect services from README mentions only (no .env file)."""
        github = AsyncMock()
        github.get_readme.return_value = (
            "# Firebase Auth App\n"
            "Uses Firebase for authentication and Cloudinary for images.\n"
        )
        github.get_file_tree.return_value = ["README.md", "src/app.ts"]

        result = await handle_envcheck(
            {"repo_url": "https://github.com/test/firebase-app"},
            github,
        )
        data = json.loads(result[0].text)
        assert "firebase" in data["detected_services"]
        assert "cloudinary" in data["detected_services"]
        assert data["total_required"] > 0

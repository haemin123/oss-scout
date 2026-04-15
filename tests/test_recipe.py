"""Unit tests for server/tools/recipe.py.

Tests cover:
- recipes.yaml loading and validation
- list action (all and filtered)
- info action (existing and missing recipe)
- find_recipes_by_query keyword matching
- apply action (mocked scaffold)
- .env.example generation
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from server.tools.recipe import (
    RECIPE_TOOL,
    _generate_env_example,
    find_recipes_by_query,
    handle_recipe,
    load_recipes,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_RECIPES = {
    "nextjs-saas": {
        "name": "Next.js SaaS 스타터",
        "description": "인증 + 결제 + 대시보드가 포함된 SaaS 보일러플레이트",
        "base_repo": "https://github.com/mickasmt/next-saas-stripe-starter",
        "tech_stack": ["Next.js", "TypeScript", "Stripe", "Prisma"],
        "env_required": ["STRIPE_SECRET_KEY", "DATABASE_URL", "NEXTAUTH_SECRET"],
        "tags": ["saas", "stripe", "auth", "dashboard"],
    },
    "ai-chatbot": {
        "name": "AI 챗봇",
        "description": "LLM 기반 대화형 챗봇 인터페이스",
        "base_repo": "https://github.com/vercel/ai-chatbot",
        "tech_stack": ["Next.js", "TypeScript", "AI SDK"],
        "env_required": ["OPENAI_API_KEY"],
        "tags": ["ai", "chatbot", "llm", "streaming"],
    },
    "cli-tool": {
        "name": "CLI 도구 (Python)",
        "description": "Python Click 기반 커맨드라인 도구",
        "base_repo": "https://github.com/tiangolo/typer",
        "tech_stack": ["Python", "Typer"],
        "tags": ["cli", "command-line", "python"],
    },
}


@pytest.fixture
def recipes_yaml_path(tmp_path: Path) -> Path:
    """Create a temporary recipes.yaml file."""
    config_path = tmp_path / "recipes.yaml"
    config_path.write_text(
        yaml.dump({"recipes": SAMPLE_RECIPES}, allow_unicode=True),
        encoding="utf-8",
    )
    return config_path


@pytest.fixture
def mock_github() -> AsyncMock:
    """Create a mock GitHubClient."""
    return AsyncMock()


# ===========================================================================
# load_recipes
# ===========================================================================


class TestLoadRecipes:
    def test_loads_valid_yaml(self, recipes_yaml_path: Path) -> None:
        recipes = load_recipes(recipes_yaml_path)
        assert "nextjs-saas" in recipes
        assert "ai-chatbot" in recipes
        assert len(recipes) == 3

    def test_raises_on_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="레시피 설정 파일"):
            load_recipes(tmp_path / "nonexistent.yaml")

    def test_raises_on_invalid_format(self, tmp_path: Path) -> None:
        bad_path = tmp_path / "bad.yaml"
        bad_path.write_text("just_a_string", encoding="utf-8")
        with pytest.raises(ValueError, match="recipes"):
            load_recipes(bad_path)

    def test_raises_on_missing_recipes_key(self, tmp_path: Path) -> None:
        bad_path = tmp_path / "bad.yaml"
        bad_path.write_text(yaml.dump({"other": "data"}), encoding="utf-8")
        with pytest.raises(ValueError, match="recipes"):
            load_recipes(bad_path)

    def test_raises_on_non_dict_recipes(self, tmp_path: Path) -> None:
        bad_path = tmp_path / "bad.yaml"
        bad_path.write_text(yaml.dump({"recipes": ["a", "b"]}), encoding="utf-8")
        with pytest.raises(ValueError, match="매핑"):
            load_recipes(bad_path)

    def test_loads_default_config(self) -> None:
        """Verify the actual config/recipes.yaml loads without error."""
        recipes = load_recipes()
        assert isinstance(recipes, dict)
        assert len(recipes) > 0
        # Every recipe should have at minimum name, base_repo, tags
        for recipe_id, recipe in recipes.items():
            assert "name" in recipe, f"{recipe_id} missing 'name'"
            assert "base_repo" in recipe, f"{recipe_id} missing 'base_repo'"
            assert "tags" in recipe, f"{recipe_id} missing 'tags'"


# ===========================================================================
# find_recipes_by_query
# ===========================================================================


class TestFindRecipesByQuery:
    def test_matches_by_tag(self) -> None:
        matches = find_recipes_by_query("chatbot", SAMPLE_RECIPES)
        assert len(matches) >= 1
        assert matches[0][0] == "ai-chatbot"

    def test_matches_by_name(self) -> None:
        matches = find_recipes_by_query("SaaS", SAMPLE_RECIPES)
        assert len(matches) >= 1
        ids = [m[0] for m in matches]
        assert "nextjs-saas" in ids

    def test_matches_by_description(self) -> None:
        matches = find_recipes_by_query("결제", SAMPLE_RECIPES)
        assert len(matches) >= 1
        ids = [m[0] for m in matches]
        assert "nextjs-saas" in ids

    def test_matches_by_recipe_id(self) -> None:
        matches = find_recipes_by_query("cli-tool", SAMPLE_RECIPES)
        assert len(matches) >= 1
        assert matches[0][0] == "cli-tool"

    def test_multiple_keyword_scoring(self) -> None:
        matches = find_recipes_by_query("ai llm chatbot", SAMPLE_RECIPES)
        assert len(matches) >= 1
        # ai-chatbot should have highest score (matches ai, llm, chatbot)
        assert matches[0][0] == "ai-chatbot"
        assert matches[0][2] >= 3

    def test_no_match(self) -> None:
        matches = find_recipes_by_query("blockchain", SAMPLE_RECIPES)
        assert len(matches) == 0

    def test_empty_query(self) -> None:
        matches = find_recipes_by_query("", SAMPLE_RECIPES)
        assert len(matches) == 0

    def test_whitespace_query(self) -> None:
        matches = find_recipes_by_query("   ", SAMPLE_RECIPES)
        assert len(matches) == 0

    def test_sorted_by_score_descending(self) -> None:
        matches = find_recipes_by_query("python", SAMPLE_RECIPES)
        scores = [m[2] for m in matches]
        assert scores == sorted(scores, reverse=True)


# ===========================================================================
# _generate_env_example
# ===========================================================================


class TestGenerateEnvExample:
    def test_creates_file(self, tmp_path: Path) -> None:
        env_path = _generate_env_example(
            tmp_path, ["API_KEY", "DATABASE_URL", "SECRET"]
        )
        assert env_path.exists()
        assert env_path.name == ".env.example"

    def test_contains_all_vars(self, tmp_path: Path) -> None:
        vars_list = ["STRIPE_KEY", "DB_URL"]
        _generate_env_example(tmp_path, vars_list)
        content = (tmp_path / ".env.example").read_text(encoding="utf-8")
        for var in vars_list:
            assert f"{var}=" in content

    def test_empty_vars(self, tmp_path: Path) -> None:
        env_path = _generate_env_example(tmp_path, [])
        content = env_path.read_text(encoding="utf-8")
        assert "Environment variables" in content


# ===========================================================================
# handle_recipe — action="list"
# ===========================================================================


class TestHandleRecipeList:
    @pytest.mark.asyncio
    async def test_list_all(
        self, mock_github: AsyncMock, recipes_yaml_path: Path
    ) -> None:
        with patch("server.tools.recipe.RECIPES_PATH", recipes_yaml_path):
            result = await handle_recipe({"action": "list"}, mock_github)

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["status"] == "success"
        assert data["action"] == "list"
        assert data["count"] == 3

    @pytest.mark.asyncio
    async def test_list_with_query(
        self, mock_github: AsyncMock, recipes_yaml_path: Path
    ) -> None:
        with patch("server.tools.recipe.RECIPES_PATH", recipes_yaml_path):
            result = await handle_recipe(
                {"action": "list", "query": "chatbot"}, mock_github
            )

        data = json.loads(result[0].text)
        assert data["status"] == "success"
        assert data["count"] >= 1
        recipe_ids = [r["id"] for r in data["recipes"]]
        assert "ai-chatbot" in recipe_ids

    @pytest.mark.asyncio
    async def test_list_with_no_match_query(
        self, mock_github: AsyncMock, recipes_yaml_path: Path
    ) -> None:
        with patch("server.tools.recipe.RECIPES_PATH", recipes_yaml_path):
            result = await handle_recipe(
                {"action": "list", "query": "blockchain"}, mock_github
            )

        data = json.loads(result[0].text)
        assert data["count"] == 0
        assert "매칭되는 레시피가 없습니다" in data["message"]


# ===========================================================================
# handle_recipe — action="info"
# ===========================================================================


class TestHandleRecipeInfo:
    @pytest.mark.asyncio
    async def test_info_existing(
        self, mock_github: AsyncMock, recipes_yaml_path: Path
    ) -> None:
        with patch("server.tools.recipe.RECIPES_PATH", recipes_yaml_path):
            result = await handle_recipe(
                {"action": "info", "recipe_id": "ai-chatbot"}, mock_github
            )

        data = json.loads(result[0].text)
        assert data["status"] == "success"
        assert data["action"] == "info"
        assert data["recipe_id"] == "ai-chatbot"
        assert data["name"] == "AI 챗봇"
        assert "OPENAI_API_KEY" in data["env_required"]

    @pytest.mark.asyncio
    async def test_info_nonexistent(
        self, mock_github: AsyncMock, recipes_yaml_path: Path
    ) -> None:
        with patch("server.tools.recipe.RECIPES_PATH", recipes_yaml_path):
            result = await handle_recipe(
                {"action": "info", "recipe_id": "nonexistent"}, mock_github
            )

        data = json.loads(result[0].text)
        assert data["status"] == "error"
        assert "찾을 수 없습니다" in data["error"]

    @pytest.mark.asyncio
    async def test_info_missing_recipe_id(
        self, mock_github: AsyncMock, recipes_yaml_path: Path
    ) -> None:
        with (
            patch("server.tools.recipe.RECIPES_PATH", recipes_yaml_path),
            pytest.raises(ValueError, match="recipe_id"),
        ):
            await handle_recipe({"action": "info"}, mock_github)


# ===========================================================================
# handle_recipe — action="apply" (mocked scaffold)
# ===========================================================================


class TestHandleRecipeApply:
    @pytest.mark.asyncio
    async def test_apply_success(
        self, mock_github: AsyncMock, recipes_yaml_path: Path, tmp_path: Path
    ) -> None:
        target = tmp_path / "my_project"
        scaffold_result_data = {
            "status": "success",
            "path": str(target),
            "files_created": 42,
            "claude_md_path": str(target / "CLAUDE.md"),
            "next_steps": ["npm install"],
        }
        mock_scaffold_return = [
            type("TC", (), {
                "type": "text",
                "text": json.dumps(scaffold_result_data),
            })()
        ]

        with (
            patch("server.tools.recipe.RECIPES_PATH", recipes_yaml_path),
            patch(
                "server.tools.recipe.handle_scaffold",
                new_callable=AsyncMock,
                return_value=mock_scaffold_return,
            ),
        ):
            # Create target dir so .env.example can be written
            target.mkdir(parents=True, exist_ok=True)

            result = await handle_recipe(
                {
                    "action": "apply",
                    "recipe_id": "nextjs-saas",
                    "target_dir": str(target),
                    "project_name": "my-saas",
                },
                mock_github,
            )

        data = json.loads(result[0].text)
        assert data["status"] == "success"
        assert data["action"] == "apply"
        assert data["recipe_id"] == "nextjs-saas"
        assert data["recipe_name"] == "Next.js SaaS 스타터"
        assert data["project_name"] == "my-saas"
        assert data["files_created"] == 42
        assert len(data["env_required"]) == 3
        # .env.example should have been generated
        assert data["env_example_path"] is not None
        assert (target / ".env.example").exists()

    @pytest.mark.asyncio
    async def test_apply_nonexistent_recipe(
        self, mock_github: AsyncMock, recipes_yaml_path: Path
    ) -> None:
        with patch("server.tools.recipe.RECIPES_PATH", recipes_yaml_path):
            result = await handle_recipe(
                {
                    "action": "apply",
                    "recipe_id": "nonexistent",
                    "target_dir": "/tmp/test",  # noqa: S108
                },
                mock_github,
            )

        data = json.loads(result[0].text)
        assert data["status"] == "error"
        assert "찾을 수 없습니다" in data["error"]

    @pytest.mark.asyncio
    async def test_apply_missing_recipe_id(
        self, mock_github: AsyncMock, recipes_yaml_path: Path
    ) -> None:
        with (
            patch("server.tools.recipe.RECIPES_PATH", recipes_yaml_path),
            pytest.raises(ValueError, match="recipe_id"),
        ):
            await handle_recipe(
                {"action": "apply", "target_dir": "/tmp/test"},  # noqa: S108
                mock_github,
            )

    @pytest.mark.asyncio
    async def test_apply_missing_target_dir(
        self, mock_github: AsyncMock, recipes_yaml_path: Path
    ) -> None:
        with (
            patch("server.tools.recipe.RECIPES_PATH", recipes_yaml_path),
            pytest.raises(ValueError, match="target_dir"),
        ):
            await handle_recipe(
                {"action": "apply", "recipe_id": "ai-chatbot"}, mock_github,
            )


# ===========================================================================
# handle_recipe — invalid action
# ===========================================================================


class TestHandleRecipeInvalidAction:
    @pytest.mark.asyncio
    async def test_invalid_action(self, mock_github: AsyncMock) -> None:
        with pytest.raises(ValueError, match="action"):
            await handle_recipe({"action": "invalid"}, mock_github)


# ===========================================================================
# RECIPE_TOOL schema
# ===========================================================================


class TestRecipeToolSchema:
    def test_tool_name(self) -> None:
        assert RECIPE_TOOL.name == "recipe"

    def test_tool_has_required_action(self) -> None:
        schema = RECIPE_TOOL.inputSchema
        assert "action" in schema["properties"]
        assert "action" in schema["required"]

    def test_tool_action_enum(self) -> None:
        action_schema = RECIPE_TOOL.inputSchema["properties"]["action"]
        assert set(action_schema["enum"]) == {"list", "info", "apply"}

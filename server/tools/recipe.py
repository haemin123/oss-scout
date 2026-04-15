"""recipe MCP tool.

Manages pre-defined OSS combination recipes (presets).
Allows listing, inspecting, and applying verified OSS stacks
without going through the full search pipeline.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from mcp.types import TextContent, Tool

from server.core.github_client import GitHubClient
from server.tools.scaffold import handle_scaffold

logger = logging.getLogger("oss-scout")

# Path to the recipes YAML config
RECIPES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "recipes.yaml"


def _log(level: str, event: str, **kwargs: Any) -> None:
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(json.dumps(entry, ensure_ascii=False))


# --- Recipe Loading ----------------------------------------------------------


def load_recipes(path: Path | None = None) -> dict[str, Any]:
    """Load recipes from the YAML config file.

    Returns a dict mapping recipe_id to recipe data.
    Raises FileNotFoundError if the config file is missing.
    """
    config_path = path or RECIPES_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"레시피 설정 파일을 찾을 수 없습니다: {config_path}")

    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "recipes" not in data:
        raise ValueError("레시피 설정 파일 형식이 올바르지 않습니다. 'recipes' 키가 필요합니다.")

    recipes = data["recipes"]
    if not isinstance(recipes, dict):
        raise ValueError("레시피 설정 파일의 'recipes'는 매핑(dict)이어야 합니다.")

    return recipes


# --- Keyword Matching --------------------------------------------------------


def find_recipes_by_query(
    query: str, recipes: dict[str, Any],
) -> list[tuple[str, dict[str, Any], int]]:
    """Search recipes by keyword matching against tags, name, and description.

    Returns a list of (recipe_id, recipe_data, score) sorted by score descending.
    """
    if not query or not query.strip():
        return []

    keywords = query.lower().split()
    matches: list[tuple[str, dict[str, Any], int]] = []

    for recipe_id, recipe in recipes.items():
        tags_text = " ".join(recipe.get("tags", [])).lower()
        name_text = recipe.get("name", "").lower()
        desc_text = recipe.get("description", "").lower()
        searchable = f"{tags_text} {name_text} {desc_text} {recipe_id.lower()}"

        score = sum(1 for kw in keywords if kw in searchable)
        if score > 0:
            matches.append((recipe_id, recipe, score))

    return sorted(matches, key=lambda x: -x[2])


# --- .env.example Generation ------------------------------------------------


def _generate_env_example(target_dir: Path, env_vars: list[str]) -> Path:
    """Generate a .env.example file with placeholder values."""
    lines = [
        "# Environment variables required for this project",
        "# Copy this file to .env and fill in the values",
        "",
    ]
    for var in env_vars:
        lines.append(f"{var}=")

    env_path = target_dir / ".env.example"
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return env_path


# --- Formatting Helpers ------------------------------------------------------


def _format_recipe_list(recipes: dict[str, Any]) -> str:
    """Format all recipes as a readable table."""
    rows: list[dict[str, Any]] = []
    for recipe_id, recipe in recipes.items():
        rows.append({
            "id": recipe_id,
            "name": recipe.get("name", ""),
            "description": recipe.get("description", ""),
            "tech_stack": recipe.get("tech_stack", []),
            "tags": recipe.get("tags", []),
        })

    return json.dumps({
        "status": "success",
        "action": "list",
        "count": len(rows),
        "recipes": rows,
    }, ensure_ascii=False, indent=2)


def _format_recipe_info(recipe_id: str, recipe: dict[str, Any]) -> str:
    """Format a single recipe's detailed info."""
    return json.dumps({
        "status": "success",
        "action": "info",
        "recipe_id": recipe_id,
        "name": recipe.get("name", ""),
        "description": recipe.get("description", ""),
        "base_repo": recipe.get("base_repo", ""),
        "tech_stack": recipe.get("tech_stack", []),
        "env_required": recipe.get("env_required", []),
        "tags": recipe.get("tags", []),
        "keep_only": recipe.get("keep_only"),
    }, ensure_ascii=False, indent=2)


# --- MCP Tool Definition ----------------------------------------------------


RECIPE_TOOL = Tool(
    name="recipe",
    description="미리 정의된 OSS 조합 레시피를 조회하거나 적용합니다.",
    inputSchema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "info", "apply"],
                "description": "list=전체 목록, info=상세 정보, apply=프로젝트 생성",
            },
            "recipe_id": {
                "type": "string",
                "description": "레시피 ID (예: nextjs-saas, ai-chatbot)",
            },
            "target_dir": {
                "type": "string",
                "description": "apply 시 프로젝트 생성 경로",
            },
            "project_name": {
                "type": "string",
                "description": "apply 시 프로젝트명",
            },
            "query": {
                "type": "string",
                "description": "키워드로 레시피 검색 (예: 'AI 챗봇', 'SaaS')",
            },
        },
        "required": ["action"],
    },
)


# --- Handler -----------------------------------------------------------------


async def handle_recipe(
    arguments: dict[str, Any],
    github: GitHubClient,
) -> list[TextContent]:
    """Handle recipe tool invocations.

    Actions:
      - list: Return all available recipes (optionally filtered by query)
      - info: Return detailed info for a specific recipe
      - apply: Scaffold a project from a recipe's base_repo
    """
    action = arguments.get("action", "")
    if action not in ("list", "info", "apply"):
        raise ValueError("action은 'list', 'info', 'apply' 중 하나여야 합니다.")

    recipes = load_recipes()

    # --- list ---
    if action == "list":
        query = arguments.get("query", "")
        if query and query.strip():
            matches = find_recipes_by_query(query, recipes)
            if not matches:
                return [TextContent(
                    type="text",
                    text=json.dumps({
                        "status": "success",
                        "action": "list",
                        "count": 0,
                        "query": query,
                        "recipes": [],
                        "message": f"'{query}'에 매칭되는 레시피가 없습니다.",
                    }, ensure_ascii=False, indent=2),
                )]
            filtered = {rid: rdata for rid, rdata, _ in matches}
            return [TextContent(type="text", text=_format_recipe_list(filtered))]

        return [TextContent(type="text", text=_format_recipe_list(recipes))]

    # --- info ---
    if action == "info":
        recipe_id = arguments.get("recipe_id", "")
        if not recipe_id or not recipe_id.strip():
            raise ValueError("info 액션에는 recipe_id가 필요합니다.")

        recipe_id = recipe_id.strip()
        if recipe_id not in recipes:
            return [TextContent(
                type="text",
                text=json.dumps({
                    "status": "error",
                    "action": "info",
                    "recipe_id": recipe_id,
                    "error": f"레시피 '{recipe_id}'를 찾을 수 없습니다.",
                    "available": list(recipes.keys()),
                }, ensure_ascii=False, indent=2),
            )]

        return [TextContent(
            type="text",
            text=_format_recipe_info(recipe_id, recipes[recipe_id]),
        )]

    # --- apply ---
    recipe_id = arguments.get("recipe_id", "")
    if not recipe_id or not recipe_id.strip():
        raise ValueError("apply 액션에는 recipe_id가 필요합니다.")

    recipe_id = recipe_id.strip()
    if recipe_id not in recipes:
        return [TextContent(
            type="text",
            text=json.dumps({
                "status": "error",
                "action": "apply",
                "recipe_id": recipe_id,
                "error": f"레시피 '{recipe_id}'를 찾을 수 없습니다.",
                "available": list(recipes.keys()),
            }, ensure_ascii=False, indent=2),
        )]

    recipe = recipes[recipe_id]
    target_dir = arguments.get("target_dir", "")
    if not target_dir or not target_dir.strip():
        raise ValueError("apply 액션에는 target_dir가 필요합니다.")

    project_name = arguments.get("project_name", "")
    base_repo = recipe.get("base_repo", "")
    if not base_repo:
        return [TextContent(
            type="text",
            text=json.dumps({
                "status": "error",
                "action": "apply",
                "recipe_id": recipe_id,
                "error": "레시피에 base_repo가 정의되어 있지 않습니다.",
            }, ensure_ascii=False, indent=2),
        )]

    _log("info", "recipe_apply_start", recipe_id=recipe_id, target=target_dir)

    # Build scaffold arguments
    scaffold_args: dict[str, Any] = {
        "repo_url": base_repo,
        "target_dir": target_dir.strip(),
        "generate_claude_md": True,
    }

    # Delegate to scaffold handler
    scaffold_results = await handle_scaffold(scaffold_args, github)

    # Parse scaffold result to augment with recipe info
    scaffold_text = scaffold_results[0].text if scaffold_results else "{}"
    try:
        scaffold_data = json.loads(scaffold_text)
    except json.JSONDecodeError:
        scaffold_data = {"status": "error", "error": "스캐폴드 결과 파싱 실패"}

    # Generate .env.example if env_required is specified
    env_required = recipe.get("env_required", [])
    env_example_path: str | None = None
    if env_required and scaffold_data.get("status") == "success":
        target_path = Path(target_dir.strip())
        if target_path.exists():
            env_path = _generate_env_example(target_path, env_required)
            env_example_path = str(env_path)

    # Build enriched result
    result = {
        "status": scaffold_data.get("status", "error"),
        "action": "apply",
        "recipe_id": recipe_id,
        "recipe_name": recipe.get("name", ""),
        "base_repo": base_repo,
        "tech_stack": recipe.get("tech_stack", []),
        "path": scaffold_data.get("path", target_dir),
        "files_created": scaffold_data.get("files_created", 0),
        "claude_md_path": scaffold_data.get("claude_md_path"),
        "env_example_path": env_example_path,
        "env_required": env_required,
        "next_steps": scaffold_data.get("next_steps", []),
    }

    if project_name:
        result["project_name"] = project_name

    if scaffold_data.get("error"):
        result["error"] = scaffold_data["error"]

    _log("info", "recipe_apply_complete",
         recipe_id=recipe_id,
         status=result["status"],
         files=result.get("files_created", 0))

    return [TextContent(
        type="text",
        text=json.dumps(result, ensure_ascii=False, indent=2),
    )]

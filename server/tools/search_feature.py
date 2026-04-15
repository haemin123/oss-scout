"""search_feature MCP tool.

Searches GitHub Code Search API for feature-level code snippets
that match the user's project stack, then ranks results by quality
and license compliance.

Pipeline:
  1. Detect project stack from project_dir
  2. Match feature keyword to feature_catalog
  3. Build GitHub Code Search query with stack filters
  4. Call search_code() API
  5. Group results by repo
  6. Score each repo (reuse scoring.py)
  7. Check license (reuse license_check.py)
  8. Suggest file placement
  9. Return ranked results
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from mcp.types import TextContent, Tool

from server.core.github_client import GitHubClient
from server.core.license_check import check_license
from server.core.scoring import calculate_popularity_score
from server.tools.extract_component import _extract_imports_from_content, _resolve_npm_packages
from server.tools.feature_catalog import (
    STACK_SEARCH_FILTERS,
    get_install_command,
    get_required_deps,
    get_search_queries,
    match_feature,
)
from server.tools.wiring import detect_project_stack

logger = logging.getLogger("oss-scout")


def _log(level: str, event: str, **kwargs: Any) -> None:
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(json.dumps(entry, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

SEARCH_FEATURE_TOOL = Tool(
    name="search_feature",
    description="기존 프로젝트에 추가할 기능 코드를 GitHub에서 검색합니다",
    inputSchema={
        "type": "object",
        "properties": {
            "feature": {
                "type": "string",
                "description": (
                    "추가하려는 기능 (예: 'stripe payment', 'dark mode')"
                ),
            },
            "project_dir": {
                "type": "string",
                "description": "기존 프로젝트 디렉토리 경로 (스택 자동 감지용)",
            },
            "language": {
                "type": "string",
                "enum": ["typescript", "javascript", "python"],
                "description": "언어 필터 (생략 시 프로젝트에서 자동 감지)",
            },
            "max_results": {
                "type": "integer",
                "default": 5,
                "minimum": 1,
                "maximum": 10,
                "description": "최대 결과 수",
            },
        },
        "required": ["feature", "project_dir"],
    },
)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

_GITHUB_OPERATORS = re.compile(
    r"\b(?:user|org|repo|path|language|stars|forks|size|pushed|created|"
    r"topic|topics|license|is|mirror|archived|in|fork):",
    re.IGNORECASE,
)


def _sanitize_feature(feature: str) -> str:
    """Sanitize feature input: strip operators and control chars."""
    sanitized = feature[:256]
    sanitized = re.sub(r"[\x00-\x1f\x7f]", "", sanitized)
    sanitized = _GITHUB_OPERATORS.sub("", sanitized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if not sanitized:
        raise ValueError("기능명이 비어 있습니다.")
    return sanitized


def _validate_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize search_feature arguments."""
    feature = arguments.get("feature", "")
    if not isinstance(feature, str) or not feature.strip():
        raise ValueError("feature를 입력해주세요.")
    if len(feature) > 200:
        raise ValueError("기능명은 200자 이내로 입력해주세요.")
    feature = _sanitize_feature(feature)

    project_dir = arguments.get("project_dir", "")
    if not isinstance(project_dir, str) or not project_dir.strip():
        raise ValueError("project_dir를 입력해주세요.")

    language = arguments.get("language")
    if language is not None and language not in ("typescript", "javascript", "python"):
        raise ValueError("language는 typescript, javascript, python 중 하나여야 합니다.")

    max_results = arguments.get("max_results", 5)
    if not isinstance(max_results, int) or not (1 <= max_results <= 10):
        raise ValueError("max_results는 1~10 사이의 정수여야 합니다.")

    return {
        "feature": feature,
        "project_dir": project_dir,
        "language": language,
        "max_results": max_results,
    }


# ---------------------------------------------------------------------------
# File placement suggestion
# ---------------------------------------------------------------------------

# Framework -> file type -> target path template
_PLACEMENT_RULES: dict[str, dict[str, str]] = {
    "nextjs": {
        "api": "app/api/{feature}/route.ts",
        "component": "components/{feature}/",
        "lib": "lib/{feature}.ts",
        "middleware": "middleware/{feature}.ts",
        "hook": "hooks/use{Feature}.ts",
    },
    "react": {
        "component": "src/components/{feature}/",
        "hook": "src/hooks/use{Feature}.ts",
        "lib": "src/lib/{feature}.ts",
    },
    "express": {
        "route": "routes/{feature}.ts",
        "middleware": "middleware/{feature}.ts",
        "lib": "lib/{feature}.ts",
    },
    "fastapi": {
        "router": "routers/{feature}.py",
        "middleware": "middleware/{feature}.py",
        "lib": "lib/{feature}.py",
    },
    "vue": {
        "component": "src/components/{feature}/",
        "composable": "src/composables/use{Feature}.ts",
        "lib": "src/lib/{feature}.ts",
    },
    "django": {
        "view": "views/{feature}.py",
        "middleware": "middleware/{feature}.py",
        "lib": "lib/{feature}.py",
    },
    "flask": {
        "route": "routes/{feature}.py",
        "middleware": "middleware/{feature}.py",
        "lib": "lib/{feature}.py",
    },
    "fastify": {
        "route": "routes/{feature}.ts",
        "plugin": "plugins/{feature}.ts",
        "lib": "lib/{feature}.ts",
    },
}

_FILE_TYPE_PATTERNS: dict[str, list[str]] = {
    "api": ["api/"],
    "middleware": ["middleware", "guard"],
    "component": ["component", "ui/", ".tsx", ".vue"],
    "hook": ["hook", "use"],
    "composable": ["composable"],
    "route": ["route", "router", "endpoint"],
    "router": ["router"],
    "view": ["view"],
    "plugin": ["plugin"],
    "lib": ["lib/", "util", "helper", "service"],
}


def _classify_file_type(file_path: str) -> str:
    """Classify a source file path into a type category.

    Returns one of: api, route, router, view, component, hook,
    composable, middleware, lib, plugin.
    """
    lower_path = file_path.lower()
    for file_type, patterns in _FILE_TYPE_PATTERNS.items():
        for pattern in patterns:
            if pattern in lower_path:
                return file_type
    return "lib"


def _suggest_placement(
    source_files: list[str],
    feature: str,
    framework: str | None,
    project_dir: str,
) -> dict[str, str]:
    """Suggest target file placement for extracted source files.

    Priority:
    1. If project has matching directory, use it
    2. If source path is compatible with project structure, keep it
    3. Apply framework-specific default rules
    """
    feature_slug = re.sub(r"[\s\-]+", "-", feature.strip().lower())
    feature_camel = feature_slug.replace("-", " ").title().replace(" ", "")

    rules = _PLACEMENT_RULES.get(framework or "", {})
    project_path = Path(project_dir)

    placement: dict[str, str] = {}

    for source_file in source_files:
        file_type = _classify_file_type(source_file)

        # Priority 2: source path compatible with project structure
        source_dir = str(Path(source_file).parent)
        if (project_path / source_dir).exists():
            placement[source_file] = source_file
            continue

        # Priority 3: framework rules
        if file_type in rules:
            template = rules[file_type]
            target = template.replace(
                "{feature}", feature_slug,
            ).replace(
                "{Feature}", feature_camel,
            )
            placement[source_file] = target
        else:
            # Fallback: keep original path
            placement[source_file] = source_file

    return placement


# ---------------------------------------------------------------------------
# Result grouping
# ---------------------------------------------------------------------------

def _group_by_repo(
    search_results: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group code search results by repository full name."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in search_results:
        repo = item["repo_full_name"]
        if repo not in groups:
            groups[repo] = []
        groups[repo].append(item)
    return groups


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

async def handle_search_feature(
    arguments: dict[str, Any],
    github: GitHubClient,
) -> list[TextContent]:
    """Execute the search_feature pipeline.

    1. Detect project stack
    2. Match feature to catalog
    3. Build Code Search queries
    4. Search GitHub Code API
    5. Group by repo, score, license check
    6. Suggest placement
    7. Return ranked results
    """
    args = _validate_args(arguments)
    feature = args["feature"]
    project_dir = args["project_dir"]
    max_results = args["max_results"]

    _log("info", "search_feature_start", feature=feature, project_dir=project_dir)

    # Step 1: Detect project stack
    project_path = Path(project_dir).resolve()
    if not project_path.exists():
        raise ValueError(f"프로젝트 디렉토리가 존재하지 않습니다: {project_dir}")

    stack = detect_project_stack(project_dir)
    language = args["language"] or stack.get("language") or "typescript"
    framework = stack.get("framework")

    _log("info", "stack_detected", stack=json.dumps(
        {k: v for k, v in stack.items() if v}, ensure_ascii=False,
    ))

    # Step 2: Match feature to catalog
    catalog_entry = match_feature(feature)

    # Step 3: Build search queries
    if catalog_entry:
        queries = get_search_queries(catalog_entry, framework)
        deps = get_required_deps(catalog_entry, language)
    else:
        # Fallback: use raw feature as query
        queries = [feature]
        deps = []

    # Step 4: Search GitHub Code API
    all_code_results: list[dict[str, Any]] = []
    stack_filter = STACK_SEARCH_FILTERS.get(framework or "", {})
    search_language = stack_filter.get("language", language)

    for query in queries[:3]:  # Limit to 3 queries to conserve rate limit
        try:
            results = await github.search_code(
                query=query,
                language=search_language,
                max_results=20,
            )
            all_code_results.extend(results)
        except Exception as e:
            _log("warning", "code_search_failed", query=query, error=str(e)[:200])

    if not all_code_results:
        response = {
            "feature": feature,
            "detected_stack": {k: v for k, v in stack.items() if v is not None},
            "results": [],
            "message": f"'{feature}'에 대한 검색 결과가 없습니다.",
        }
        return [TextContent(
            type="text",
            text=json.dumps(response, ensure_ascii=False, indent=2),
        )]

    # Step 5: Group by repo
    repo_groups = _group_by_repo(all_code_results)

    # Step 6: Score each repo + license check
    scored_repos: list[dict[str, Any]] = []

    for repo_full_name, files in repo_groups.items():
        owner, name = repo_full_name.split("/", 1)

        # Get repo detail for scoring
        try:
            repo_detail = await github.get_repo(owner, name)
        except Exception:
            _log("warning", "repo_detail_failed", repo=repo_full_name)
            continue

        # Skip archived repos
        if repo_detail.get("archived", False):
            continue

        # License check
        try:
            license_data = await github.get_license(owner, name)
            spdx_id = license_data.get("spdx_id")
        except Exception:
            spdx_id = None

        license_result = check_license(spdx_id)

        # Popularity score as a simple quality proxy
        stars = repo_detail.get("stars", 0)
        forks = repo_detail.get("forks", 0)
        pop_score = calculate_popularity_score(stars, forks)
        quality_score = round(pop_score * 100, 1)

        # Deduplicate files within this repo
        seen_paths: set[str] = set()
        unique_files: list[dict[str, Any]] = []
        for f in files:
            if f["file_path"] not in seen_paths:
                seen_paths.add(f["file_path"])
                relevance = "high" if len(files) > 2 else "medium"
                unique_files.append({
                    "path": f["file_path"],
                    "url": f["file_url"],
                    "relevance": relevance,
                    "snippet": f.get("content_snippet", "")[:200],
                })

        # Extract dependencies from snippets
        all_imports: list[str] = []
        for f in files:
            snippet = f.get("content_snippet", "")
            if snippet:
                imports = _extract_imports_from_content(snippet)
                all_imports.extend(imports)

        file_deps = _resolve_npm_packages(all_imports) if language != "python" else []
        combined_deps = sorted(set(deps + file_deps))

        # File placement
        file_paths = [f["path"] for f in unique_files]
        placement = _suggest_placement(file_paths, feature, framework, project_dir)

        install_cmd = get_install_command(combined_deps, language)

        scored_repos.append({
            "repo": repo_full_name,
            "repo_url": f"https://github.com/{repo_full_name}",
            "stars": stars,
            "license": license_result.spdx_id or license_result.license,
            "license_ok": license_result.recommended,
            "quality_score": quality_score,
            "matched_files": unique_files[:10],
            "dependencies_needed": combined_deps,
            "install_command": install_cmd,
            "suggested_placement": placement,
        })

    # Step 7: Rank by quality_score, filter, return top N
    scored_repos.sort(key=lambda x: x["quality_score"], reverse=True)
    top_results = scored_repos[:max_results]

    response = {
        "feature": feature,
        "detected_stack": {k: v for k, v in stack.items() if v is not None},
        "results": top_results,
    }

    _log(
        "info", "search_feature_complete",
        feature=feature,
        results=len(top_results),
        total_repos=len(repo_groups),
    )

    return [TextContent(
        type="text",
        text=json.dumps(response, ensure_ascii=False, indent=2),
    )]

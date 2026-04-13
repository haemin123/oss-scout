"""search_boilerplate MCP tool.

Pipeline (5 stages):
  1. GitHub Search API
  2. License filtering
  3. Quality scoring
  4. Agent validation (license, quality, security, compatibility)
  5. Rank and return top N
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

from mcp.types import TextContent, Tool

from server.agents.base import BaseAgent
from server.agents.license_agent import LicenseAgent
from server.core.github_client import GitHubClient
from server.core.license_check import check_license, is_license_acceptable
from server.core.scoring import calculate
from server.models import RepoInfo

logger = logging.getLogger("oss-scout")

# GitHub Search API operators to strip from user input
_GITHUB_OPERATORS = re.compile(
    r"\b(?:user|org|repo|path|language|stars|forks|size|pushed|created|"
    r"topic|topics|license|is|mirror|archived|in|fork):",
    re.IGNORECASE,
)


def _sanitize_query(query: str) -> str:
    """Remove GitHub Search operators and control chars from user input."""
    sanitized = query[:256]
    sanitized = re.sub(r"[\x00-\x1f\x7f]", "", sanitized)
    sanitized = _GITHUB_OPERATORS.sub("", sanitized)
    sanitized = re.sub(r"\b(NOT|OR|AND)\b", "", sanitized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if not sanitized:
        raise ValueError("검색어가 비어 있습니다.")
    return sanitized


def _validate_search_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize search_boilerplate arguments."""
    query = arguments.get("query", "")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("검색어(query)를 입력해주세요.")
    if len(query) > 200:
        raise ValueError("검색어는 200자 이내로 입력해주세요.")
    query = _sanitize_query(query)

    language = arguments.get("language")
    if language is not None:
        if not isinstance(language, str):
            raise ValueError("language는 문자열이어야 합니다.")
        language = re.sub(r"[^\w\s+#]", "", language).strip() or None

    min_stars = arguments.get("min_stars", 100)
    if not isinstance(min_stars, int) or min_stars < 0:
        raise ValueError("min_stars는 0 이상의 정수여야 합니다.")

    max_results = arguments.get("max_results", 5)
    if not isinstance(max_results, int) or not (1 <= max_results <= 20):
        raise ValueError("max_results는 1~20 사이의 정수여야 합니다.")

    allow_copyleft = bool(arguments.get("allow_copyleft", False))

    return {
        "query": query,
        "language": language,
        "min_stars": min_stars,
        "max_results": max_results,
        "allow_copyleft": allow_copyleft,
    }


def _dict_to_repo_info(data: dict[str, Any], detail: dict[str, Any]) -> RepoInfo:
    """Convert github_client dict responses to a RepoInfo model."""
    last_commit_str = detail.get("last_commit", "unknown")
    try:
        last_commit = date.fromisoformat(last_commit_str)
    except (ValueError, TypeError):
        last_commit = date.today()

    return RepoInfo(
        full_name=data["full_name"],
        url=data["url"],
        stars=data.get("stars", 0),
        forks=detail.get("forks", data.get("forks", 0)),
        last_commit=last_commit,
        archived=data.get("archived", False),
        default_branch=data.get("default_branch", "main"),
        language=data.get("language"),
        description=data.get("description", ""),
        commits_last_6mo=0,
        has_tests=detail.get("has_tests", False),
        has_ci=detail.get("has_ci", False),
        has_releases=detail.get("has_releases", False),
        has_examples=detail.get("has_examples", False),
        has_license=bool(detail.get("readme_length", 0) > 0),
        readme_length=detail.get("readme_length", 0),
    )


def _get_agents() -> list[BaseAgent]:
    """Load all available sub-agents for search validation."""
    agents: list[BaseAgent] = [LicenseAgent()]
    try:
        from server.agents.quality_agent import QualityAgent
        agents.append(QualityAgent())
    except ImportError:
        pass
    try:
        from server.agents.security_agent import SecurityAgent
        agents.append(SecurityAgent())
    except ImportError:
        pass
    try:
        from server.agents.compatibility_agent import CompatibilityAgent
        agents.append(CompatibilityAgent())
    except ImportError:
        pass
    return agents


def _log(level: str, event: str, **kwargs: Any) -> None:
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(json.dumps(entry, ensure_ascii=False))


SEARCH_TOOL = Tool(
    name="search_boilerplate",
    description=(
        "Search GitHub for license-verified, quality-scored open-source "
        "boilerplates matching a natural language query. "
        "Results include sub-agent validation (license, quality, security, compatibility)."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query (e.g., 'Next.js Supabase dashboard')",
            },
            "language": {
                "type": "string",
                "description": "Programming language filter (e.g., 'TypeScript')",
            },
            "min_stars": {
                "type": "integer",
                "default": 100,
                "minimum": 0,
                "description": "Minimum star count",
            },
            "max_results": {
                "type": "integer",
                "default": 5,
                "minimum": 1,
                "maximum": 20,
                "description": "Maximum number of results to return",
            },
            "allow_copyleft": {
                "type": "boolean",
                "default": False,
                "description": "If true, include GPL/LGPL/AGPL licensed repos with warnings",
            },
        },
        "required": ["query"],
    },
)


async def _run_agents_for_repo(
    agents: list[BaseAgent],
    repo_data: dict[str, Any],
) -> dict[str, Any]:
    """Run all agents on a single repo and return results + composite score."""
    agent_results: dict[str, Any] = {}
    weights = {"license": 0.3, "quality": 0.3, "security": 0.2, "compatibility": 0.2}
    weighted_score = 0.0
    total_weight = 0.0

    for agent in agents:
        try:
            result = await agent.analyze(repo_data)
            agent_results[result.agent_name] = {
                "passed": result.passed,
                "score": result.score,
                "findings": result.findings,
                "warnings": result.warnings,
            }
            w = weights.get(result.agent_name, 0.25)
            weighted_score += result.score * w
            total_weight += w
        except Exception as e:
            _log("warning", "agent_failed", agent=agent.name, error=str(e)[:100])

    composite = round(weighted_score / total_weight, 2) if total_weight > 0 else 0.5
    return {"agents": agent_results, "agent_score": composite}


async def handle_search(
    arguments: dict[str, Any],
    github: GitHubClient,
) -> list[TextContent]:
    """Execute the search_boilerplate pipeline.

    Pipeline:
      1. GitHub Search API -> top 20 candidates
      2. License filtering (whitelist/copyleft policy)
      3. Quality scoring
      4. Agent validation (on top N candidates only, for performance)
      5. Rank by combined score, return top N
    """
    args = _validate_search_args(arguments)
    _log("info", "search_start", query=args["query"], language=args["language"])

    # Stage 1: GitHub Search API
    search_results = await github.search_repos(
        query=args["query"],
        language=args["language"],
        min_stars=args["min_stars"],
        max_results=20,
    )

    if not search_results:
        return [TextContent(
            type="text",
            text=json.dumps([], ensure_ascii=False),
        )]

    # Stage 2 + 3: License filtering + Quality scoring
    repo_ids = [r["full_name"] for r in search_results]
    detail_list = await github.get_repos_parallel(repo_ids)

    detail_by_name: dict[str, dict[str, Any]] = {}
    for d in detail_list:
        if "full_name" in d:
            detail_by_name[d["full_name"]] = d

    scored_results: list[dict[str, Any]] = []

    for search_item in search_results:
        full_name = search_item["full_name"]
        detail = detail_by_name.get(full_name, {})

        # Stage 2: License check
        owner, name = full_name.split("/", 1)
        try:
            license_data = await github.get_license(owner, name)
            spdx_id = license_data.get("spdx_id")
        except Exception:
            spdx_id = None

        if not is_license_acceptable(spdx_id, args["allow_copyleft"]):
            continue

        license_result = check_license(spdx_id, args["allow_copyleft"])

        # Stage 3: Quality scoring
        try:
            repo_info = _dict_to_repo_info(search_item, detail)
            score = calculate(repo_info)
        except Exception as e:
            _log("warning", "scoring_failed", repo=full_name, error=str(e)[:100])
            continue

        last_commit_str = detail.get("last_commit", "unknown")
        scored_results.append({
            "repo": full_name,
            "url": search_item["url"],
            "stars": search_item.get("stars", 0),
            "last_commit": last_commit_str,
            "license": license_result.spdx_id or license_result.license,
            "license_ok": license_result.recommended,
            "quality_score": round(score.quality_score, 2),
            "fit_score": 0.5,
            "summary": search_item.get("description", "") or "설명 없음",
            "_detail": detail,
            "_license_data": {"spdx_id": spdx_id},
        })

    # Sort by quality_score, take top N for agent validation
    scored_results.sort(key=lambda x: x["quality_score"], reverse=True)
    top_candidates = scored_results[: args["max_results"]]

    # Stage 4: Agent validation (on top N only)
    agents = _get_agents()
    if agents:
        for item in top_candidates:
            repo_data = item.get("_detail", {})
            # Enrich with license info for agents
            license_info = await github.get_license(
                *item["repo"].split("/", 1)
            )
            repo_data["license_info"] = license_info
            try:
                readme = await github.get_readme(*item["repo"].split("/", 1))
                repo_data["readme_content"] = readme
            except Exception:
                repo_data["readme_content"] = ""

            agent_data = await _run_agents_for_repo(agents, repo_data)
            item["agents"] = agent_data["agents"]
            item["agent_score"] = agent_data["agent_score"]

            # Replace fit_score with agent composite score
            item["fit_score"] = agent_data["agent_score"]

    # Stage 5: Final rank by combined score
    for item in top_candidates:
        item["combined_score"] = round(
            item["quality_score"] * 0.5 + item.get("agent_score", 0.5) * 0.5, 2
        )

    top_candidates.sort(key=lambda x: x["combined_score"], reverse=True)

    # Clean up internal fields before returning
    final_results = []
    for item in top_candidates:
        item.pop("_detail", None)
        item.pop("_license_data", None)
        final_results.append(item)

    _log("info", "search_complete", results=len(final_results), total_candidates=len(search_results))

    return [TextContent(
        type="text",
        text=json.dumps(final_results, ensure_ascii=False, indent=2),
    )]

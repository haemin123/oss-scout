"""Batch parallel execution MCP tools.

Provides batch_search, batch_validate, and batch_scaffold tools
that execute multiple operations concurrently using asyncio.gather.
All operations respect the GitHub API semaphore (10 concurrent requests).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from mcp.types import TextContent, Tool

from server.core.github_client import GitHubClient
from server.tools.scaffold import handle_scaffold
from server.tools.search import handle_search
from server.tools.validate import handle_validate

logger = logging.getLogger("oss-scout")


def _log(level: str, event: str, **kwargs: Any) -> None:
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(json.dumps(entry, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

BATCH_SEARCH_TOOL = Tool(
    name="batch_search",
    description=(
        "여러 검색 쿼리를 병렬로 실행합니다. "
        "기능별로 최적의 OSS를 동시에 찾을 때 사용합니다."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "queries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "description": "기능 라벨 (예: 인증, 결제)",
                        },
                        "query": {"type": "string"},
                        "language": {"type": "string"},
                        "min_stars": {"type": "integer", "default": 100},
                        "max_results": {"type": "integer", "default": 3},
                    },
                    "required": ["label", "query"],
                },
                "description": "병렬로 실행할 검색 쿼리 목록",
            },
        },
        "required": ["queries"],
    },
)

BATCH_VALIDATE_TOOL = Tool(
    name="batch_validate",
    description=(
        "여러 GitHub 레포를 병렬로 검증합니다. "
        "후보 레포들을 동시에 평가할 때 사용합니다."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo_urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "검증할 GitHub 레포 URL 목록",
            },
        },
        "required": ["repo_urls"],
    },
)

BATCH_SCAFFOLD_TOOL = Tool(
    name="batch_scaffold",
    description="여러 GitHub 레포를 각각의 디렉토리에 병렬로 scaffold합니다.",
    inputSchema={
        "type": "object",
        "properties": {
            "repos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "repo_url": {"type": "string"},
                        "target_dir": {"type": "string"},
                        "subdir": {"type": "string"},
                    },
                    "required": ["repo_url", "target_dir"],
                },
            },
        },
        "required": ["repos"],
    },
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _search_single(
    query_spec: dict[str, Any],
    github: GitHubClient,
) -> dict[str, Any]:
    """Run a single search and return labelled results."""
    label = query_spec.get("label", "unknown")
    search_args: dict[str, Any] = {
        "query": query_spec.get("query", ""),
        "language": query_spec.get("language"),
        "min_stars": query_spec.get("min_stars", 100),
        "max_results": query_spec.get("max_results", 3),
    }
    results = await handle_search(search_args, github)
    # handle_search returns list[TextContent]; parse the JSON text back
    raw_text = results[0].text if results else "[]"
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        parsed = raw_text
    return {"label": label, "results": parsed}


async def _validate_single(
    repo_url: str,
    github: GitHubClient,
) -> dict[str, Any]:
    """Run validation on a single repo URL."""
    results = await handle_validate({"repo_url": repo_url}, github)
    raw_text = results[0].text if results else "{}"
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        parsed = {"error": raw_text}
    return {"repo_url": repo_url, "validation": parsed}


async def _scaffold_single(
    repo_spec: dict[str, Any],
    github: GitHubClient,
) -> dict[str, Any]:
    """Run scaffold on a single repo spec."""
    scaffold_args: dict[str, Any] = {
        "repo_url": repo_spec.get("repo_url", ""),
        "target_dir": repo_spec.get("target_dir", ""),
    }
    subdir = repo_spec.get("subdir")
    if subdir:
        scaffold_args["subdir"] = subdir

    results = await handle_scaffold(scaffold_args, github)
    raw_text = results[0].text if results else "{}"
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        parsed = {"error": raw_text}
    return {"repo_url": repo_spec.get("repo_url", ""), "scaffold": parsed}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_batch_search(
    arguments: dict[str, Any],
    github: GitHubClient,
) -> list[TextContent]:
    """Execute multiple search queries in parallel."""
    queries = arguments.get("queries", [])
    if not queries:
        return [TextContent(
            type="text",
            text=json.dumps(
                {"error": "검색 쿼리 목록(queries)이 비어 있습니다."},
                ensure_ascii=False,
            ),
        )]

    _log("info", "batch_search_start", count=len(queries))

    tasks = [_search_single(q, github) for q in queries]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    labelled_results: list[dict[str, Any]] = []
    for i, result in enumerate(raw_results):
        label = queries[i].get("label", f"query_{i}")
        if isinstance(result, BaseException):
            _log("warning", "batch_search_item_failed", label=label, error=str(result)[:200])
            labelled_results.append({
                "label": label,
                "error": f"검색 실패: {type(result).__name__}: {str(result)[:200]}",
                "results": [],
            })
        else:
            labelled_results.append(result)

    _log("info", "batch_search_complete",
         total=len(queries),
         success=sum(1 for r in labelled_results if "error" not in r))

    return [TextContent(
        type="text",
        text=json.dumps(labelled_results, ensure_ascii=False, indent=2),
    )]


async def handle_batch_validate(
    arguments: dict[str, Any],
    github: GitHubClient,
) -> list[TextContent]:
    """Execute multiple repo validations in parallel."""
    repo_urls = arguments.get("repo_urls", [])
    if not repo_urls:
        return [TextContent(
            type="text",
            text=json.dumps(
                {"error": "레포 URL 목록(repo_urls)이 비어 있습니다."},
                ensure_ascii=False,
            ),
        )]

    _log("info", "batch_validate_start", count=len(repo_urls))

    tasks = [_validate_single(url, github) for url in repo_urls]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    validation_results: list[dict[str, Any]] = []
    for i, result in enumerate(raw_results):
        url = repo_urls[i]
        if isinstance(result, BaseException):
            _log("warning", "batch_validate_item_failed", url=url, error=str(result)[:200])
            validation_results.append({
                "repo_url": url,
                "error": f"검증 실패: {type(result).__name__}: {str(result)[:200]}",
                "validation": None,
            })
        else:
            validation_results.append(result)

    _log("info", "batch_validate_complete",
         total=len(repo_urls),
         success=sum(1 for r in validation_results if "error" not in r))

    return [TextContent(
        type="text",
        text=json.dumps(validation_results, ensure_ascii=False, indent=2),
    )]


async def handle_batch_scaffold(
    arguments: dict[str, Any],
    github: GitHubClient,
) -> list[TextContent]:
    """Execute multiple scaffold operations in parallel."""
    repos = arguments.get("repos", [])
    if not repos:
        return [TextContent(
            type="text",
            text=json.dumps(
                {"error": "레포 목록(repos)이 비어 있습니다."},
                ensure_ascii=False,
            ),
        )]

    _log("info", "batch_scaffold_start", count=len(repos))

    tasks = [_scaffold_single(r, github) for r in repos]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    scaffold_results: list[dict[str, Any]] = []
    for i, result in enumerate(raw_results):
        url = repos[i].get("repo_url", f"repo_{i}")
        if isinstance(result, BaseException):
            _log("warning", "batch_scaffold_item_failed", url=url, error=str(result)[:200])
            scaffold_results.append({
                "repo_url": url,
                "error": f"스캐폴딩 실패: {type(result).__name__}: {str(result)[:200]}",
                "scaffold": None,
            })
        else:
            scaffold_results.append(result)

    _log("info", "batch_scaffold_complete",
         total=len(repos),
         success=sum(1 for r in scaffold_results if "error" not in r))

    return [TextContent(
        type="text",
        text=json.dumps(scaffold_results, ensure_ascii=False, indent=2),
    )]

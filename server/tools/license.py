"""check_license MCP tool.

Standalone license verification utility. Can be reused by other tools.
Uses GitHub API license field and license_check policy engine.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

from server.core.github_client import GitHubClient, parse_repo_url
from server.core.license_check import check_license

logger = logging.getLogger("oss-scout")

_REPO_URL_PATTERN = re.compile(
    r"^https://github\.com/[\w.\-]+/[\w.\-]+/?$"
)


def _validate_license_args(arguments: dict[str, Any]) -> str:
    """Validate check_license arguments. Returns repo_url."""
    repo_url = arguments.get("repo_url", "")
    if not isinstance(repo_url, str) or not repo_url.strip():
        raise ValueError("repo_url을 입력해주세요.")
    repo_url = repo_url.strip().rstrip("/")
    if not _REPO_URL_PATTERN.match(repo_url):
        raise ValueError(
            "repo_url은 https://github.com/{owner}/{repo} 형식이어야 합니다."
        )
    return repo_url


def _log(level: str, event: str, **kwargs: Any) -> None:
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(json.dumps(entry, ensure_ascii=False))


LICENSE_TOOL = Tool(
    name="check_license",
    description="Check the license of a GitHub repository and classify it.",
    inputSchema={
        "type": "object",
        "properties": {
            "repo_url": {
                "type": "string",
                "description": "Full GitHub repository URL (https://github.com/owner/name)",
            },
        },
        "required": ["repo_url"],
    },
)


async def handle_license(
    arguments: dict[str, Any],
    github: GitHubClient,
) -> list[TextContent]:
    """Execute the check_license tool.

    1. Validate repo_url
    2. Fetch license from GitHub API
    3. Classify using license_check policy
    4. Return structured result
    """
    repo_url = _validate_license_args(arguments)
    owner, name = parse_repo_url(repo_url)

    _log("info", "license_check_start", repo=f"{owner}/{name}")

    # Fetch license info from GitHub
    try:
        license_data = await github.get_license(owner, name)
        spdx_id = license_data.get("spdx_id")
        license_name = license_data.get("name", "Unknown")
    except Exception as e:
        _log("warning", "license_fetch_failed", repo=f"{owner}/{name}", error=str(e)[:100])
        spdx_id = None
        license_name = "Unknown"

    # Classify using policy
    result = check_license(spdx_id)

    # Cross-validate: if GitHub says something but our check says unknown,
    # use the GitHub-provided name for display
    if result.license == "none" and license_name != "Unknown":
        result_dict = result.model_dump()
        result_dict["license"] = license_name
    else:
        result_dict = result.model_dump()

    _log("info", "license_check_complete", repo=f"{owner}/{name}", category=result.category.value)

    return [TextContent(
        type="text",
        text=json.dumps(result_dict, ensure_ascii=False, indent=2),
    )]

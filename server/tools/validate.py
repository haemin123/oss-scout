"""validate_repo MCP tool.

Runs all available sub-agents (license, quality, security, compatibility)
against a repository and returns a consolidated validation report.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from mcp.types import TextContent, Tool

from server.agents.base import AgentResult, BaseAgent
from server.agents.license_agent import LicenseAgent
from server.core.github_client import GitHubClient, parse_repo_url

logger = logging.getLogger("oss-scout")

_REPO_URL_PATTERN = re.compile(
    r"^https://github\.com/[\w.\-]+/[\w.\-]+/?$"
)


def _log(level: str, event: str, **kwargs: Any) -> None:
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(json.dumps(entry, ensure_ascii=False))


def _get_agents() -> list[BaseAgent]:
    """Load all available sub-agents."""
    agents: list[BaseAgent] = [LicenseAgent()]

    # Import optional agents if implemented
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


def _validate_args(arguments: dict[str, Any]) -> str:
    """Validate validate_repo arguments. Returns repo_url."""
    repo_url = arguments.get("repo_url", "")
    if not isinstance(repo_url, str) or not repo_url.strip():
        raise ValueError("repo_url을 입력해주세요.")
    repo_url = repo_url.strip().rstrip("/")
    if not _REPO_URL_PATTERN.match(repo_url):
        raise ValueError(
            "repo_url은 https://github.com/{owner}/{repo} 형식이어야 합니다."
        )
    return repo_url


VALIDATE_TOOL = Tool(
    name="validate_repo",
    description=(
        "레포에 대한 라이선스/품질/보안/호환성 종합 검증을 수행합니다. "
        "4종 서브 에이전트가 분석 결과를 반환합니다."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo_url": {
                "type": "string",
                "description": "GitHub repository URL (https://github.com/owner/name)",
            },
        },
        "required": ["repo_url"],
    },
)


async def _build_repo_data(
    github: GitHubClient,
    owner: str,
    name: str,
) -> dict[str, Any]:
    """Fetch all repo data needed by sub-agents."""
    repo_data = await github.get_repo(owner, name)
    license_info = await github.get_license(owner, name)
    readme = await github.get_readme(owner, name)
    file_tree = await github.get_file_tree(owner, name)

    repo_data["license_info"] = license_info
    repo_data["readme_content"] = readme
    repo_data["file_tree"] = file_tree

    return repo_data


async def handle_validate(
    arguments: dict[str, Any],
    github: GitHubClient,
) -> list[TextContent]:
    """Execute validate_repo: run all sub-agents and aggregate results."""
    repo_url = _validate_args(arguments)
    owner, name = parse_repo_url(repo_url)

    _log("info", "validate_start", repo=f"{owner}/{name}")

    # Fetch all repo data
    repo_data = await _build_repo_data(github, owner, name)

    # Run all agents
    agents = _get_agents()
    agent_results: dict[str, Any] = {}
    total_score = 0.0
    all_passed = True
    all_findings: list[str] = []
    all_warnings: list[str] = []

    for agent in agents:
        try:
            result = await agent.analyze(repo_data)
            agent_results[result.agent_name] = result.model_dump()
            total_score += result.score
            if not result.passed:
                all_passed = False
            all_findings.extend(result.findings)
            all_warnings.extend(result.warnings)
        except Exception as e:
            _log("warning", "agent_failed", agent=agent.name, error=str(e)[:100])
            agent_results[agent.name] = {
                "agent_name": agent.name,
                "passed": False,
                "score": 0.0,
                "findings": [f"에이전트 실행 실패: {type(e).__name__}"],
                "warnings": [],
            }

    # Compute aggregate score
    num_agents = len(agents) if agents else 1
    aggregate_score = round(total_score / num_agents, 2)

    report = {
        "repo": f"{owner}/{name}",
        "url": repo_url,
        "overall_passed": all_passed,
        "aggregate_score": aggregate_score,
        "agents": agent_results,
        "summary": {
            "findings": all_findings,
            "warnings": all_warnings,
            "agents_run": len(agents),
        },
    }

    _log("info", "validate_complete",
         repo=f"{owner}/{name}",
         passed=all_passed,
         score=aggregate_score,
         agents_run=len(agents))

    return [TextContent(
        type="text",
        text=json.dumps(report, ensure_ascii=False, indent=2),
    )]

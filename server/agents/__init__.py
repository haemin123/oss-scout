"""Rule-based sub-agents for repository analysis."""

from __future__ import annotations

import asyncio
from typing import Any

from server.agents.base import AgentResult, BaseAgent
from server.agents.compatibility_agent import CompatibilityAgent
from server.agents.license_agent import LicenseAgent
from server.agents.quality_agent import QualityAgent
from server.agents.security_agent import SecurityAgent

__all__ = [
    "AgentResult",
    "BaseAgent",
    "CompatibilityAgent",
    "LicenseAgent",
    "QualityAgent",
    "SecurityAgent",
    "run_all_agents",
]


async def run_all_agents(repo_data: dict[str, Any]) -> dict[str, AgentResult]:
    """Run all sub-agents in parallel and return results keyed by agent name.

    Args:
        repo_data: Dictionary from GitHubClient.get_repo() with optional
            extra keys (readme_content, file_tree, license_info, etc.).

    Returns:
        Dict mapping agent name to its AgentResult.
    """
    agents: list[BaseAgent] = [
        LicenseAgent(),
        QualityAgent(),
        SecurityAgent(),
        CompatibilityAgent(),
    ]
    results = await asyncio.gather(
        *[agent.analyze(repo_data) for agent in agents]
    )
    return {r.agent_name: r for r in results}

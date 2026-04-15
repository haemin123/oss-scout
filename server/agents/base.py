"""Base class and result model for rule-based sub-agents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class AgentResult(BaseModel):
    """Result from a sub-agent analysis."""

    agent_name: str
    passed: bool
    score: float = Field(ge=0, le=1)
    findings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class BaseAgent(ABC):
    """Abstract base for all rule-based analysis agents."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Agent identifier."""
        ...

    @abstractmethod
    async def analyze(self, repo_data: dict[str, Any]) -> AgentResult:
        """Run analysis on repo_data and return results.

        Args:
            repo_data: Dictionary from GitHubClient.get_repo() with
                optional extra keys (readme_content, file_tree, license_info).
        """
        ...

"""Pydantic models for OSS Scout MCP Server."""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class LicenseCategory(str, Enum):
    """License classification categories."""

    PERMISSIVE = "permissive"
    COPYLEFT = "copyleft"
    UNKNOWN = "unknown"
    NONE = "none"


class LicenseResult(BaseModel):
    """Result of a license check."""

    license: str
    spdx_id: str
    category: LicenseCategory
    commercial_use_ok: bool
    recommended: bool
    warnings: list[str] = Field(default_factory=list)


class QualityScore(BaseModel):
    """Breakdown of quality scoring components."""

    quality_score: float = Field(ge=0, le=1)
    activity_score: float = Field(ge=0, le=1)
    popularity_score: float = Field(ge=0, le=1)
    maturity_score: float = Field(ge=0, le=1)
    documentation_score: float = Field(ge=0, le=1)
    stack_score: float = Field(ge=0, le=1, default=0.0)
    functional_fit_score: float = Field(ge=0, le=1, default=0.0)
    confidence: str = Field(default="high")


class RepoInfo(BaseModel):
    """Core repository metadata."""

    full_name: str = Field(description="owner/name format")
    url: str
    stars: int = Field(ge=0)
    forks: int = Field(ge=0, default=0)
    last_commit: date
    archived: bool = False
    default_branch: str = "main"
    language: str | None = None
    description: str | None = None
    commits_last_6mo: int = Field(ge=0, default=0)
    has_tests: bool = False
    has_ci: bool = False
    has_releases: bool = False
    has_examples: bool = False
    has_license: bool = False
    readme_length: int = Field(ge=0, default=0)
    confidence: str = Field(default="high")
    file_tree: list[str] = Field(default_factory=list)
    readme_content: str = Field(default="")
    latest_sha: str = Field(default="")

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, v: str) -> str:
        if "/" not in v or len(v.split("/")) != 2:
            raise ValueError("full_name must be in 'owner/name' format")
        return v


class ExplainResult(BaseModel):
    """Response from explain_repo tool."""

    repo: str
    description: str
    tech_stack: list[str] = Field(default_factory=list)
    file_tree_summary: str = ""
    how_to_use: str = ""
    caveats: str = ""
    license: str = ""


class ScaffoldResult(BaseModel):
    """Response from scaffold tool."""

    status: str = Field(pattern=r"^(success|error)$")
    path: str
    files_created: int = Field(ge=0)
    claude_md_path: str | None = None
    next_steps: list[str] = Field(default_factory=list)
    error_message: str | None = None

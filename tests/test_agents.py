"""Unit tests for server/agents/ sub-agents."""

from __future__ import annotations

import pytest

from server.agents import run_all_agents
from server.agents.compatibility_agent import CompatibilityAgent
from server.agents.license_agent import LicenseAgent
from server.agents.quality_agent import QualityAgent
from server.agents.security_agent import SecurityAgent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_repo(**overrides: object) -> dict:
    """Create a base repo_data dict with sensible defaults."""
    data: dict = {
        "full_name": "test/repo",
        "url": "https://github.com/test/repo",
        "stars": 500,
        "forks": 50,
        "open_issues": 10,
        "archived": False,
        "has_tests": True,
        "has_ci": True,
        "has_releases": True,
        "has_examples": True,
        "has_license": True,
        "readme_length": 3000,
        "readme_content": "# My Project\n\n## Installation\nnpm install\n\n## Usage\nRun it.",
        "file_tree": [
            "README.md",
            "LICENSE",
            "package.json",
            "src/index.ts",
            "tests/index.test.ts",
            ".github/workflows/ci.yml",
        ],
        "license_info": {
            "spdx_id": "MIT",
            "name": "MIT License",
            "body": "MIT License\n\nPermission is hereby granted, free of charge...",
        },
        "dependency_count": 20,
    }
    data.update(overrides)
    return data


# ===========================================================================
# LicenseAgent
# ===========================================================================


class TestLicenseAgent:
    @pytest.fixture
    def agent(self) -> LicenseAgent:
        return LicenseAgent()

    @pytest.mark.asyncio
    async def test_pass_mit_license_consistent(self, agent: LicenseAgent) -> None:
        """MIT in API and LICENSE file content -- should pass."""
        repo = _base_repo()
        result = await agent.analyze(repo)
        assert result.passed is True
        assert result.score >= 0.8
        assert result.agent_name == "license"
        assert len(result.findings) == 0

    @pytest.mark.asyncio
    async def test_warning_license_mismatch(self, agent: LicenseAgent) -> None:
        """API says MIT but LICENSE file content looks like Apache."""
        repo = _base_repo(
            license_info={
                "spdx_id": "MIT",
                "name": "MIT License",
                "body": "Apache License\nVersion 2.0\n...",
            }
        )
        result = await agent.analyze(repo)
        assert any("불일치" in w for w in result.warnings)
        assert result.score < 1.0

    @pytest.mark.asyncio
    async def test_fail_no_license(self, agent: LicenseAgent) -> None:
        """No license detected at all."""
        repo = _base_repo(
            license_info={
                "spdx_id": "",
                "name": "",
                "body": "",
            }
        )
        result = await agent.analyze(repo)
        assert result.passed is False
        assert len(result.findings) >= 1

    @pytest.mark.asyncio
    async def test_warning_api_only_no_body(self, agent: LicenseAgent) -> None:
        """API has license but no LICENSE file body for cross-validation."""
        repo = _base_repo(
            license_info={
                "spdx_id": "MIT",
                "name": "MIT License",
                "body": "",
            }
        )
        result = await agent.analyze(repo)
        assert any("교차 검증" in w for w in result.warnings)


# ===========================================================================
# QualityAgent
# ===========================================================================


class TestQualityAgent:
    @pytest.fixture
    def agent(self) -> QualityAgent:
        return QualityAgent()

    @pytest.mark.asyncio
    async def test_pass_good_quality(self, agent: QualityAgent) -> None:
        """Repo with tests, CI, good README -- should pass."""
        repo = _base_repo()
        result = await agent.analyze(repo)
        assert result.passed is True
        assert result.score >= 0.8
        assert result.agent_name == "quality"

    @pytest.mark.asyncio
    async def test_warning_no_readme_sections(self, agent: QualityAgent) -> None:
        """README exists but lacks install/usage sections."""
        repo = _base_repo(
            readme_content="# Hello\nThis is a project.",
        )
        result = await agent.analyze(repo)
        assert any("install/usage" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_fail_no_readme(self, agent: QualityAgent) -> None:
        """No README at all."""
        repo = _base_repo(
            readme_length=0,
            readme_content="",
        )
        result = await agent.analyze(repo)
        assert result.passed is False
        assert any("README" in f for f in result.findings)

    @pytest.mark.asyncio
    async def test_warning_high_issue_ratio(self, agent: QualityAgent) -> None:
        """Many open issues relative to stars."""
        repo = _base_repo(
            stars=200,
            open_issues=50,
        )
        result = await agent.analyze(repo)
        assert any("이슈 비율" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_fail_no_tests_no_ci(self, agent: QualityAgent) -> None:
        """No tests and no CI."""
        repo = _base_repo(
            has_tests=False,
            has_ci=False,
            file_tree=["README.md", "src/index.ts"],
        )
        result = await agent.analyze(repo)
        assert result.passed is False
        assert result.score < 0.8


# ===========================================================================
# SecurityAgent
# ===========================================================================


class TestSecurityAgent:
    @pytest.fixture
    def agent(self) -> SecurityAgent:
        return SecurityAgent()

    @pytest.mark.asyncio
    async def test_pass_clean_repo(self, agent: SecurityAgent) -> None:
        """No dangerous files, not archived."""
        repo = _base_repo()
        result = await agent.analyze(repo)
        assert result.passed is True
        assert result.score >= 0.8
        assert result.agent_name == "security"

    @pytest.mark.asyncio
    async def test_finding_dangerous_files(self, agent: SecurityAgent) -> None:
        """Repo contains .env and credentials.json."""
        repo = _base_repo(
            file_tree=[
                "README.md",
                ".env",
                "credentials.json",
                "src/index.ts",
            ],
        )
        result = await agent.analyze(repo)
        assert result.passed is False
        assert any("보안 위험 파일" in f for f in result.findings)

    @pytest.mark.asyncio
    async def test_warning_archived(self, agent: SecurityAgent) -> None:
        """Archived repo should warn about missing security patches."""
        repo = _base_repo(archived=True)
        result = await agent.analyze(repo)
        assert any("아카이브" in w for w in result.warnings)
        assert result.score < 1.0

    @pytest.mark.asyncio
    async def test_warning_many_dependencies(self, agent: SecurityAgent) -> None:
        """Repo with >100 dependencies."""
        repo = _base_repo(dependency_count=150)
        result = await agent.analyze(repo)
        assert any("의존성 수" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_finding_pem_files(self, agent: SecurityAgent) -> None:
        """Repo contains .pem key files."""
        repo = _base_repo(
            file_tree=["README.md", "certs/server.pem", "certs/private.key"],
        )
        result = await agent.analyze(repo)
        assert result.passed is False


# ===========================================================================
# CompatibilityAgent
# ===========================================================================


class TestCompatibilityAgent:
    @pytest.fixture
    def agent(self) -> CompatibilityAgent:
        return CompatibilityAgent()

    @pytest.mark.asyncio
    async def test_pass_no_constraints(self, agent: CompatibilityAgent) -> None:
        """Repo with no special requirements."""
        repo = _base_repo()
        result = await agent.analyze(repo)
        assert result.passed is True
        assert result.score >= 0.8
        assert result.agent_name == "compatibility"

    @pytest.mark.asyncio
    async def test_warning_node_version(self, agent: CompatibilityAgent) -> None:
        """package.json specifies node engine version."""
        repo = _base_repo(
            package_json={
                "engines": {"node": ">=18.0.0"},
                "dependencies": {},
                "devDependencies": {},
                "scripts": {},
            },
        )
        result = await agent.analyze(repo)
        assert any("Node.js" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_finding_old_node(self, agent: CompatibilityAgent) -> None:
        """Very old Node.js requirement."""
        repo = _base_repo(
            package_json={
                "engines": {"node": ">=10.0.0"},
                "dependencies": {},
                "devDependencies": {},
                "scripts": {},
            },
        )
        result = await agent.analyze(repo)
        assert result.passed is False
        assert any("오래된" in f for f in result.findings)

    @pytest.mark.asyncio
    async def test_warning_native_addon(self, agent: CompatibilityAgent) -> None:
        """Repo has native build files."""
        repo = _base_repo(
            file_tree=["README.md", "binding.gyp", "src/addon.cc"],
        )
        result = await agent.analyze(repo)
        assert any("네이티브" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_warning_python_version(self, agent: CompatibilityAgent) -> None:
        """pyproject.toml with python version requirement."""
        repo = _base_repo(
            pyproject_content='[project]\nrequires-python = ">=3.11"\n',
        )
        result = await agent.analyze(repo)
        assert any("Python" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_finding_old_python(self, agent: CompatibilityAgent) -> None:
        """Very old Python requirement."""
        repo = _base_repo(
            pyproject_content='[project]\nrequires-python = ">=2.7"\n',
        )
        result = await agent.analyze(repo)
        assert any("오래된" in f for f in result.findings)


# ===========================================================================
# run_all_agents integration
# ===========================================================================


class TestRunAllAgents:
    @pytest.mark.asyncio
    async def test_runs_all_four_agents(self) -> None:
        """All 4 agents run and return results."""
        repo = _base_repo()
        results = await run_all_agents(repo)
        assert set(results.keys()) == {"license", "quality", "security", "compatibility"}
        for name, result in results.items():
            assert result.agent_name == name
            assert 0.0 <= result.score <= 1.0

    @pytest.mark.asyncio
    async def test_all_pass_on_good_repo(self) -> None:
        """All agents should pass on a well-structured repo."""
        repo = _base_repo()
        results = await run_all_agents(repo)
        for result in results.values():
            assert result.passed is True

"""Code quality consistency verification agent.

Checks README quality, test infrastructure, CI config,
and issue health. No LLM calls.
"""

from __future__ import annotations

import re

from server.agents.base import AgentResult, BaseAgent

# Sections we look for in README (case-insensitive)
_README_SECTIONS = [
    r"install",
    r"usage",
    r"getting\s+started",
    r"quick\s*start",
    r"setup",
]

# Patterns for test scripts in package.json / pyproject.toml
_TEST_SCRIPT_PATTERNS = [
    r'"test"',
    r'"test:',
    r'\[tool\.pytest',
    r"pytest",
    r"jest",
    r"mocha",
    r"vitest",
]

# Known CI config files
_CI_FILES = {
    ".github/workflows",
    ".circleci/config.yml",
    ".travis.yml",
    "Jenkinsfile",
    ".gitlab-ci.yml",
    "azure-pipelines.yml",
}


class QualityAgent(BaseAgent):
    """Validates code quality signals beyond the scoring engine."""

    @property
    def name(self) -> str:
        return "quality"

    async def analyze(self, repo_data: dict) -> AgentResult:
        findings: list[str] = []
        warnings: list[str] = []
        score = 1.0

        readme_content = repo_data.get("readme_content", "")
        readme_length = repo_data.get("readme_length", 0)
        file_tree: list[str] = repo_data.get("file_tree", [])
        has_tests = repo_data.get("has_tests", False)
        has_ci = repo_data.get("has_ci", False)
        open_issues = repo_data.get("open_issues", 0)
        stars = repo_data.get("stars", 0)

        # 1. README existence and length
        if readme_length == 0 and not readme_content:
            findings.append("README 파일이 없거나 비어있습니다.")
            score -= 0.3
        elif readme_length < 200:
            warnings.append(
                f"README가 매우 짧습니다 ({readme_length}자). "
                "충분한 문서화가 필요합니다."
            )
            score -= 0.15

        # 2. README section checks
        if readme_content:
            lower_readme = readme_content.lower()
            found_sections = []
            for pattern in _README_SECTIONS:
                if re.search(pattern, lower_readme):
                    found_sections.append(pattern)
            if not found_sections:
                warnings.append(
                    "README에 install/usage/getting started 섹션이 없습니다."
                )
                score -= 0.1

        # 3. Test script presence
        if not has_tests:
            test_script_found = False
            file_tree_lower = [f.lower() for f in file_tree]
            for f in file_tree_lower:
                if any(
                    d in f
                    for d in ("tests/", "test/", "__tests__/", "spec/")
                ):
                    test_script_found = True
                    break
            if not test_script_found:
                findings.append("테스트 디렉토리나 테스트 스크립트를 찾을 수 없습니다.")
                score -= 0.2

        # 4. CI configuration
        if not has_ci:
            ci_found = False
            for ci_file in _CI_FILES:
                if any(f.startswith(ci_file) for f in file_tree):
                    ci_found = True
                    break
            if not ci_found:
                warnings.append("CI/CD 설정 파일이 감지되지 않았습니다.")
                score -= 0.1

        # 5. Issue health ratio
        if stars > 100 and open_issues > 0:
            issue_ratio = open_issues / max(stars, 1)
            if issue_ratio > 0.1:
                warnings.append(
                    f"열린 이슈 비율이 높습니다 "
                    f"({open_issues} issues / {stars} stars = "
                    f"{issue_ratio:.2%})."
                )
                score -= 0.1

        score = max(0.0, min(1.0, score))
        passed = score >= 0.5 and len(findings) == 0

        return AgentResult(
            agent_name=self.name,
            passed=passed,
            score=round(score, 2),
            findings=findings,
            warnings=warnings,
        )

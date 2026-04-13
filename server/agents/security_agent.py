"""Security risk detection agent.

Scans file tree and README for security red flags.
No LLM calls.
"""

from __future__ import annotations

import re

from server.agents.base import AgentResult, BaseAgent

# Dangerous files that should not be in a public repo
_DANGEROUS_FILES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "credentials.json",
    "service-account.json",
    "id_rsa",
    "id_ed25519",
}

# Dangerous file extensions
_DANGEROUS_EXTENSIONS = {".pem", ".key", ".p12", ".pfx", ".jks"}

# Security keywords in README (positive signal)
_SECURITY_KEYWORDS = [
    r"security",
    r"vulnerability",
    r"cve-\d{4}",
    r"security\s+policy",
    r"responsible\s+disclosure",
]

# Dependency file patterns
_DEPENDENCY_FILES = {
    "package.json",
    "requirements.txt",
    "Pipfile",
    "poetry.lock",
    "Cargo.toml",
    "go.sum",
    "pom.xml",
    "build.gradle",
}


class SecurityAgent(BaseAgent):
    """Detects security risks in repository structure and metadata."""

    @property
    def name(self) -> str:
        return "security"

    async def analyze(self, repo_data: dict) -> AgentResult:
        findings: list[str] = []
        warnings: list[str] = []
        score = 1.0

        file_tree: list[str] = repo_data.get("file_tree", [])
        readme_content = repo_data.get("readme_content", "")
        archived = repo_data.get("archived", False)
        dependency_count = repo_data.get("dependency_count", 0)

        # 1. Dangerous files in file tree
        dangerous_found: list[str] = []
        for filepath in file_tree:
            filename = filepath.rsplit("/", 1)[-1] if "/" in filepath else filepath

            if filename in _DANGEROUS_FILES:
                dangerous_found.append(filepath)
                continue

            for ext in _DANGEROUS_EXTENSIONS:
                if filename.endswith(ext):
                    dangerous_found.append(filepath)
                    break

        if dangerous_found:
            findings.append(
                f"보안 위험 파일 감지: {', '.join(dangerous_found[:5])}"
                + (f" 외 {len(dangerous_found) - 5}건" if len(dangerous_found) > 5 else "")
            )
            score -= min(0.4, 0.1 * len(dangerous_found))

        # 2. Security awareness in README
        if readme_content:
            lower_readme = readme_content.lower()
            security_mentions = sum(
                1 for pattern in _SECURITY_KEYWORDS
                if re.search(pattern, lower_readme)
            )
            if security_mentions == 0:
                warnings.append(
                    "README에 보안 관련 언급이 없습니다 "
                    "(security policy, vulnerability reporting 등)."
                )
                score -= 0.05

        # 3. Dependency count anomaly
        if dependency_count > 100:
            warnings.append(
                f"의존성 수가 매우 많습니다 ({dependency_count}개). "
                "공급망 공격 위험이 증가할 수 있습니다."
            )
            score -= 0.15
        elif dependency_count > 50:
            warnings.append(
                f"의존성 수가 다소 많습니다 ({dependency_count}개)."
            )
            score -= 0.05

        # 4. Archived repo warning
        if archived:
            warnings.append(
                "이 레포는 아카이브 상태입니다. "
                "보안 패치가 더 이상 제공되지 않을 수 있습니다."
            )
            score -= 0.2

        # 5. Check for SECURITY.md or security policy
        security_files = [
            f for f in file_tree
            if f.lower() in (
                "security.md", ".github/security.md",
                "security.txt", ".well-known/security.txt",
            )
        ]
        if security_files:
            # Positive signal - has security policy
            score = min(1.0, score + 0.05)

        score = max(0.0, min(1.0, score))
        passed = score >= 0.5 and len(findings) == 0

        return AgentResult(
            agent_name=self.name,
            passed=passed,
            score=round(score, 2),
            findings=findings,
            warnings=warnings,
        )

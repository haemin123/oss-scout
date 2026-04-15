"""Compatibility verification agent.

Checks runtime requirements, system dependencies, and native addon presence.
No LLM calls.
"""

from __future__ import annotations

import re
from typing import Any

from server.agents.base import AgentResult, BaseAgent

# Native addon indicators
_NATIVE_ADDON_INDICATORS = {
    "binding.gyp",     # node-gyp
    "Cargo.toml",      # Rust native module
    "CMakeLists.txt",  # CMake
    "Makefile",         # C/C++ build
    "configure",        # autotools
    "meson.build",     # Meson build
}

# System requirement keywords in README
_SYSTEM_REQUIREMENT_PATTERNS = [
    r"requires?\b",
    r"prerequisites?\b",
    r"system\s+requirements?\b",
    r"dependencies\b.*install",
    r"before\s+you\s+begin",
    r"make\s+sure\s+you\s+have",
]

# Known Docker base image patterns
_DOCKER_BASE_PATTERN = re.compile(
    r"^\s*FROM\s+(\S+)", re.MULTILINE | re.IGNORECASE
)


class CompatibilityAgent(BaseAgent):
    """Verifies runtime compatibility and system requirements."""

    @property
    def name(self) -> str:
        return "compatibility"

    async def analyze(self, repo_data: dict[str, Any]) -> AgentResult:
        findings: list[str] = []
        warnings: list[str] = []
        score = 1.0

        file_tree: list[str] = repo_data.get("file_tree", [])
        readme_content = repo_data.get("readme_content", "")
        package_json: dict[str, Any] | None = repo_data.get("package_json")
        pyproject_content: str = repo_data.get("pyproject_content", "")
        dockerfile_content: str = repo_data.get("dockerfile_content", "")

        # 1. Node.js engine requirements
        if package_json and isinstance(package_json, dict):
            engines = package_json.get("engines", {})
            node_version = engines.get("node", "")
            if node_version:
                warnings.append(
                    f"Node.js 버전 요구사항: {node_version}"
                )
                # Check for very old Node requirement
                match = re.search(r"(\d+)", node_version)
                if match and int(match.group(1)) < 16:
                    findings.append(
                        f"Node.js {node_version} 요구 - "
                        "매우 오래된 버전입니다. 호환성 문제가 있을 수 있습니다."
                    )
                    score -= 0.2

            npm_version = engines.get("npm", "")
            if npm_version:
                warnings.append(f"npm 버전 요구사항: {npm_version}")

        # 2. Python version requirements
        if pyproject_content:
            python_match = re.search(
                r'requires-python\s*=\s*"([^"]+)"', pyproject_content
            )
            if python_match:
                req = python_match.group(1)
                warnings.append(f"Python 버전 요구사항: {req}")
                # Check for very old Python
                ver_match = re.search(r"(\d+)\.(\d+)", req)
                if ver_match:
                    major, minor = int(ver_match.group(1)), int(ver_match.group(2))
                    if major < 3 or (major == 3 and minor < 8):
                        findings.append(
                            f"Python {major}.{minor} 요구 - "
                            "오래된 버전입니다."
                        )
                        score -= 0.15

        # 3. Dockerfile base image check
        if dockerfile_content:
            match = _DOCKER_BASE_PATTERN.search(dockerfile_content)
            if match:
                base_image = match.group(1)
                warnings.append(f"Docker 베이스 이미지: {base_image}")
                if ":latest" in base_image:
                    warnings.append(
                        "Docker 이미지에 :latest 태그 사용 - "
                        "빌드 재현성이 보장되지 않습니다."
                    )
                    score -= 0.05

        # 4. System requirements in README
        if readme_content:
            lower_readme = readme_content.lower()
            sys_req_found = any(
                re.search(p, lower_readme)
                for p in _SYSTEM_REQUIREMENT_PATTERNS
            )
            if sys_req_found:
                warnings.append(
                    "README에 시스템 요구사항이 언급되어 있습니다. "
                    "설치 전 확인이 필요합니다."
                )

        # 5. Native addon detection
        native_files = [
            f for f in file_tree
            if any(
                f.endswith(indicator) or f == indicator
                for indicator in _NATIVE_ADDON_INDICATORS
            )
        ]
        if native_files:
            warnings.append(
                f"네이티브 빌드 파일 감지: {', '.join(native_files[:3])}. "
                "컴파일러/빌드 도구가 필요할 수 있습니다."
            )
            score -= 0.1

        # Check for node-gyp in package.json
        if package_json and isinstance(package_json, dict):
            scripts = package_json.get("scripts", {})
            deps = package_json.get("dependencies", {})
            dev_deps = package_json.get("devDependencies", {})
            all_deps = {**deps, **dev_deps}

            if "node-gyp" in all_deps or any(
                "node-gyp" in str(v) for v in scripts.values()
            ):
                findings.append(
                    "node-gyp 의존성 감지 - "
                    "C++ 컴파일러와 Python이 빌드에 필요합니다."
                )
                score -= 0.15

        score = max(0.0, min(1.0, score))
        passed = score >= 0.5 and len(findings) == 0

        return AgentResult(
            agent_name=self.name,
            passed=passed,
            score=round(score, 2),
            findings=findings,
            warnings=warnings,
        )

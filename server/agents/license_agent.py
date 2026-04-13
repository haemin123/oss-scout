"""License consistency verification agent.

Cross-validates GitHub API license field against LICENSE file content
using keyword matching. No LLM calls.
"""

from __future__ import annotations

import re

from server.agents.base import AgentResult, BaseAgent
from server.core.license_check import check_license

# Keywords found in common LICENSE file bodies
_LICENSE_KEYWORDS: dict[str, list[str]] = {
    "MIT": ["permission is hereby granted, free of charge"],
    "Apache-2.0": ["apache license", "version 2.0"],
    "GPL-3.0": ["gnu general public license", "version 3"],
    "GPL-2.0": ["gnu general public license", "version 2"],
    "BSD-2-Clause": ["redistribution and use in source and binary forms"],
    "BSD-3-Clause": [
        "redistribution and use in source and binary forms",
        "neither the name",
    ],
    "ISC": ["isc license"],
    "Unlicense": ["this is free and unencumbered software"],
    "LGPL-3.0": ["gnu lesser general public license", "version 3"],
    "LGPL-2.1": ["gnu lesser general public license", "version 2.1"],
    "AGPL-3.0": ["gnu affero general public license"],
    "MPL-2.0": ["mozilla public license", "version 2.0"],
    "0BSD": ["permission to use, copy, modify"],
}


def _detect_license_from_content(content: str) -> str | None:
    """Detect license type from LICENSE file content via keyword matching."""
    lower = content.lower()
    for spdx_id, keywords in _LICENSE_KEYWORDS.items():
        if all(kw in lower for kw in keywords):
            return spdx_id
    return None


class LicenseAgent(BaseAgent):
    """Cross-validates license between GitHub API and LICENSE file content."""

    @property
    def name(self) -> str:
        return "license"

    async def analyze(self, repo_data: dict) -> AgentResult:
        findings: list[str] = []
        warnings: list[str] = []
        score = 1.0

        # Get API-reported license
        license_info = repo_data.get("license_info", {})
        api_spdx = license_info.get("spdx_id", "")
        license_body = license_info.get("body", "")

        # Run policy check
        policy_result = check_license(api_spdx if api_spdx else None)

        if not api_spdx or api_spdx == "NOASSERTION":
            findings.append("GitHub API에서 라이선스를 감지하지 못했습니다.")
            score -= 0.5

        # Cross-validate with LICENSE file content
        if license_body:
            detected = _detect_license_from_content(license_body)
            if detected and api_spdx and detected != api_spdx:
                warnings.append(
                    f"라이선스 불일치: GitHub API는 '{api_spdx}'이지만 "
                    f"LICENSE 파일 내용은 '{detected}'으로 감지됩니다."
                )
                score -= 0.3
            elif detected and not api_spdx:
                warnings.append(
                    f"GitHub API에 라이선스 정보가 없지만 "
                    f"LICENSE 파일에서 '{detected}'이 감지되었습니다."
                )
                score -= 0.1
        elif api_spdx and api_spdx != "NOASSERTION":
            warnings.append(
                "LICENSE 파일 내용을 가져올 수 없어 교차 검증이 불가합니다."
            )
            score -= 0.1

        # Add policy warnings
        if policy_result.warnings:
            warnings.extend(policy_result.warnings)

        if not policy_result.recommended:
            score -= 0.2

        score = max(0.0, min(1.0, score))
        passed = score >= 0.5 and len(findings) == 0

        return AgentResult(
            agent_name=self.name,
            passed=passed,
            score=round(score, 2),
            findings=findings,
            warnings=warnings,
        )

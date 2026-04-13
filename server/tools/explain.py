"""explain_repo MCP tool.

Generates a structured summary of a repository's architecture,
usage, and caveats using data from GitHub API. No LLM calls.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from mcp.types import TextContent, Tool

from server.core.github_client import GitHubClient, parse_repo_url
from server.core.license_check import check_license

logger = logging.getLogger("oss-scout")

_REPO_URL_PATTERN = re.compile(
    r"^https://github\.com/[\w.\-]+/[\w.\-]+/?$"
)

# Common tech stack indicators by file/dir presence
_TECH_INDICATORS: dict[str, list[str]] = {
    "package.json": ["Node.js"],
    "tsconfig.json": ["TypeScript"],
    "pyproject.toml": ["Python"],
    "requirements.txt": ["Python"],
    "Cargo.toml": ["Rust"],
    "go.mod": ["Go"],
    "pom.xml": ["Java", "Maven"],
    "build.gradle": ["Java", "Gradle"],
    "Gemfile": ["Ruby"],
    "Dockerfile": ["Docker"],
    "docker-compose.yml": ["Docker Compose"],
    "docker-compose.yaml": ["Docker Compose"],
    ".github/workflows": ["GitHub Actions"],
    "next.config.js": ["Next.js"],
    "next.config.mjs": ["Next.js"],
    "next.config.ts": ["Next.js"],
    "nuxt.config.ts": ["Nuxt"],
    "vite.config.ts": ["Vite"],
    "tailwind.config.js": ["Tailwind CSS"],
    "tailwind.config.ts": ["Tailwind CSS"],
    "prisma": ["Prisma"],
    "supabase": ["Supabase"],
    ".eslintrc": ["ESLint"],
    "jest.config": ["Jest"],
    "vitest.config": ["Vitest"],
}

# README sections for how-to-use extraction
_SETUP_PATTERNS = [
    r"#+\s*(install|setup|getting\s+started|quick\s*start)",
    r"#+\s*(usage|how\s+to\s+use|run)",
]


def _log(level: str, event: str, **kwargs: Any) -> None:
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(
        json.dumps(entry, ensure_ascii=False)
    )


def _validate_args(arguments: dict[str, Any]) -> tuple[str, str | None]:
    """Validate explain_repo arguments. Returns (repo_url, focus)."""
    repo_url = arguments.get("repo_url", "")
    if not isinstance(repo_url, str) or not repo_url.strip():
        raise ValueError("repo_url을 입력해주세요.")
    repo_url = repo_url.strip().rstrip("/")
    if not _REPO_URL_PATTERN.match(repo_url):
        raise ValueError(
            "repo_url은 https://github.com/{owner}/{repo} 형식이어야 합니다."
        )

    focus = arguments.get("focus")
    if focus is not None:
        if focus not in ("setup", "architecture", "license"):
            raise ValueError(
                "focus는 'setup', 'architecture', 'license' 중 하나여야 합니다."
            )
    return repo_url, focus


def _detect_tech_stack(file_tree: list[str]) -> list[str]:
    """Detect tech stack from file tree."""
    found: list[str] = []
    for filepath in file_tree:
        filename = filepath.rsplit("/", 1)[-1] if "/" in filepath else filepath
        for indicator, techs in _TECH_INDICATORS.items():
            if filename == indicator or filepath.startswith(indicator):
                found.extend(techs)
    return sorted(set(found))


def _summarize_file_tree(file_tree: list[str]) -> str:
    """Summarize important directories from file tree."""
    top_dirs: dict[str, int] = {}
    for path in file_tree:
        parts = path.split("/")
        if len(parts) >= 1:
            top = parts[0]
            top_dirs[top] = top_dirs.get(top, 0) + 1

    lines: list[str] = []
    for name, count in sorted(top_dirs.items(), key=lambda x: -x[1])[:15]:
        lines.append(f"- {name}/ ({count} files)" if count > 1 else f"- {name}")
    return "\n".join(lines) if lines else "파일 트리를 가져올 수 없습니다."


def _extract_how_to_use(readme: str) -> str:
    """Extract setup/usage section from README."""
    if not readme:
        return "README에서 설치/사용법 정보를 찾을 수 없습니다."

    for pattern in _SETUP_PATTERNS:
        match = re.search(pattern, readme, re.IGNORECASE | re.MULTILINE)
        if match:
            start = match.start()
            # Find the next heading or end
            next_heading = re.search(
                r"\n#+\s", readme[match.end():]
            )
            end = match.end() + next_heading.start() if next_heading else len(readme)
            section = readme[start:end].strip()
            if len(section) > 1000:
                section = section[:1000] + "..."
            return section

    return "README에서 설치/사용법 섹션을 찾을 수 없습니다."


def _build_caveats(
    archived: bool,
    license_spdx: str | None,
    stars: int,
    readme_length: int,
) -> str:
    """Build caveats string from repo metadata."""
    caveats: list[str] = []

    if archived:
        caveats.append("이 레포는 아카이브 상태입니다. 더 이상 유지보수되지 않습니다.")

    if license_spdx:
        result = check_license(license_spdx)
        if result.warnings:
            caveats.extend(result.warnings)
    else:
        caveats.append("라이선스가 감지되지 않았습니다. 사용 전 확인이 필요합니다.")

    if stars < 50:
        caveats.append(f"스타 수가 적습니다 ({stars}). 충분히 검증되지 않았을 수 있습니다.")

    if readme_length < 200:
        caveats.append("README가 매우 짧습니다. 문서화가 부족할 수 있습니다.")

    return "\n".join(f"- {c}" for c in caveats) if caveats else "특별한 주의사항 없음."


EXPLAIN_TOOL = Tool(
    name="explain_repo",
    description=(
        "특정 GitHub 레포의 구조, 기술 스택, 용도, 주의점을 요약합니다."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo_url": {
                "type": "string",
                "description": "GitHub repository URL (https://github.com/owner/name)",
            },
            "focus": {
                "type": "string",
                "enum": ["setup", "architecture", "license"],
                "description": "Optional focus area for the explanation",
            },
        },
        "required": ["repo_url"],
    },
)


async def handle_explain(
    arguments: dict[str, Any],
    github: GitHubClient,
) -> list[TextContent]:
    """Execute explain_repo: analyze and summarize a repository."""
    repo_url, focus = _validate_args(arguments)
    owner, name = parse_repo_url(repo_url)

    _log("info", "explain_start", repo=f"{owner}/{name}", focus=focus)

    # Fetch data
    repo_data = await github.get_repo(owner, name)
    readme = await github.get_readme(owner, name)
    file_tree = await github.get_file_tree(owner, name)
    license_info = await github.get_license(owner, name)

    # Build explanation
    tech_stack = _detect_tech_stack(file_tree)
    file_tree_summary = _summarize_file_tree(file_tree)
    how_to_use = _extract_how_to_use(readme)
    spdx_id = license_info.get("spdx_id", "")
    caveats = _build_caveats(
        archived=repo_data.get("archived", False),
        license_spdx=spdx_id if spdx_id else None,
        stars=repo_data.get("stars", 0),
        readme_length=repo_data.get("readme_length", 0),
    )

    result = {
        "repo": f"{owner}/{name}",
        "description": repo_data.get("description", "") or "설명 없음",
        "tech_stack": tech_stack,
        "file_tree_summary": file_tree_summary,
        "how_to_use": how_to_use,
        "caveats": caveats,
        "license": spdx_id or "Unknown",
    }

    # If focus is specified, emphasize that section
    if focus == "license":
        license_detail = check_license(spdx_id if spdx_id else None)
        result["license_detail"] = license_detail.model_dump()
    elif focus == "architecture":
        result["file_tree_full"] = file_tree[:50]

    _log("info", "explain_complete", repo=f"{owner}/{name}")

    return [TextContent(
        type="text",
        text=json.dumps(result, ensure_ascii=False, indent=2),
    )]

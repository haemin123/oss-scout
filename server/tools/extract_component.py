"""extract_component MCP tool.

Extracts specific component/feature code from a GitHub repository
by matching file paths against component keyword patterns and
tracking 1-depth import dependencies.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from mcp.types import TextContent, Tool

from server.core.github_client import GitHubClient, parse_repo_url

logger = logging.getLogger("oss-scout")


def _log(level: str, event: str, **kwargs: Any) -> None:
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(json.dumps(entry, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Component keyword -> file pattern mapping
# ---------------------------------------------------------------------------

COMPONENT_PATTERNS: dict[str, list[str]] = {
    "chat": ["chat", "message", "conversation", "thread"],
    "auth": ["auth", "login", "signup", "session", "middleware/auth"],
    "payment": ["payment", "stripe", "checkout", "billing", "subscription"],
    "upload": ["upload", "file", "dropzone", "storage"],
    "dashboard": ["dashboard", "admin", "analytics", "chart", "graph"],
    "email": ["email", "mail", "newsletter", "template"],
    "notification": ["notification", "toast", "alert", "banner"],
    "search": ["search", "filter", "query"],
    "gallery": ["gallery", "image", "photo", "carousel", "slider"],
    "form": ["form", "input", "field", "validation"],
    "table": ["table", "grid", "list", "datatable"],
    "nav": ["nav", "header", "sidebar", "menu", "breadcrumb"],
}

# Common framework/utility packages that don't need explicit install
_BUILTIN_PACKAGES = frozenset({
    "react", "react-dom", "next", "vue", "nuxt", "svelte",
    "angular", "express", "fastify", "path", "fs", "os",
    "url", "http", "https", "crypto", "stream", "util",
    "child_process", "events", "buffer", "assert", "querystring",
})


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

EXTRACT_COMPONENT_TOOL = Tool(
    name="extract_component",
    description="GitHub 레포에서 특정 컴포넌트나 기능 코드만 추출합니다.",
    inputSchema={
        "type": "object",
        "properties": {
            "repo_url": {
                "type": "string",
                "description": "GitHub 레포 URL",
            },
            "component": {
                "type": "string",
                "description": "추출할 컴포넌트/기능 (예: 'chat-input', 'auth-middleware')",
            },
            "output_dir": {
                "type": "string",
                "description": "추출 파일 저장 경로",
            },
        },
        "required": ["repo_url", "component"],
    },
)


# ---------------------------------------------------------------------------
# Core logic (pure functions for testability)
# ---------------------------------------------------------------------------

def _normalize_component(component: str) -> str:
    """Normalize component name: lowercase, strip, collapse whitespace."""
    return re.sub(r"[\s\-_]+", "-", component.strip().lower())


def _get_keywords_for_component(component: str) -> list[str]:
    """Resolve a component name to a list of search keywords.

    Uses COMPONENT_PATTERNS if the component matches a known category,
    otherwise falls back to splitting the component name into keywords.
    """
    normalized = _normalize_component(component)
    parts = normalized.split("-")

    keywords: list[str] = []
    for part in parts:
        if part in COMPONENT_PATTERNS:
            keywords.extend(COMPONENT_PATTERNS[part])
        else:
            keywords.append(part)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)
    return unique


def _match_files(file_tree: list[str], keywords: list[str]) -> list[str]:
    """Return files from file_tree whose path contains any keyword.

    Matching is case-insensitive on the path basename and directory names.
    Excludes common non-source files (node_modules, .git, lock files, etc.).
    """
    exclude_patterns = {
        "node_modules", ".git", "dist", "build", ".next",
        "__pycache__", ".cache", "coverage",
    }
    exclude_extensions = {
        ".lock", ".log", ".map", ".min.js", ".min.css",
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
        ".woff", ".woff2", ".ttf", ".eot",
    }

    matched: list[str] = []
    for filepath in file_tree:
        lower_path = filepath.lower()

        # Skip excluded directories
        if any(excl in lower_path.split("/") for excl in exclude_patterns):
            continue

        # Skip excluded extensions
        if any(lower_path.endswith(ext) for ext in exclude_extensions):
            continue

        # Check if any keyword appears in the path
        for kw in keywords:
            if kw.lower() in lower_path:
                matched.append(filepath)
                break

    return sorted(matched)


def _extract_imports_from_content(content: str) -> list[str]:
    """Extract import paths from JS/TS file content.

    Handles:
      - import ... from "package"
      - import ... from 'package'
      - require("package")
      - require('package')
    """
    imports: list[str] = []

    # ES module imports
    es_pattern = re.compile(r"""(?:import|export)\s+.*?from\s+['"]([^'"]+)['"]""")
    imports.extend(es_pattern.findall(content))

    # CommonJS require
    cjs_pattern = re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""")
    imports.extend(cjs_pattern.findall(content))

    return imports


def _resolve_npm_packages(import_paths: list[str]) -> list[str]:
    """Extract unique npm package names from import paths.

    Filters out relative imports (starting with . or /) and
    framework/builtin packages.
    """
    packages: set[str] = set()
    for imp in import_paths:
        # Skip relative imports
        if imp.startswith(".") or imp.startswith("/"):
            continue
        # Scoped package: @scope/name
        if imp.startswith("@"):
            parts = imp.split("/")
            pkg = f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else imp
        else:
            pkg = imp.split("/")[0]

        if pkg not in _BUILTIN_PACKAGES:
            packages.add(pkg)

    return sorted(packages)


def _build_install_command(packages: list[str]) -> str:
    """Build an npm install command for the given packages.

    Only includes packages that are not builtin/framework.
    Returns empty string if no packages need installing.
    """
    if not packages:
        return ""
    return f"npm install {' '.join(packages)}"


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

async def handle_extract_component(
    arguments: dict[str, Any],
    github: GitHubClient,
) -> list[TextContent]:
    """Execute the extract_component pipeline.

    1. Validate inputs
    2. Get file tree from GitHub
    3. Match files by component keywords
    4. Fetch content of matched files and extract imports
    5. Resolve npm dependencies
    6. Return result
    """
    # Validate inputs
    repo_url = arguments.get("repo_url", "")
    if not isinstance(repo_url, str) or not repo_url.strip():
        raise ValueError("repo_url을 입력해주세요.")
    repo_url = repo_url.strip().rstrip("/")
    owner, name = parse_repo_url(repo_url)

    component = arguments.get("component", "")
    if not isinstance(component, str) or not component.strip():
        raise ValueError("component를 입력해주세요.")

    _log("info", "extract_component_start",
         repo=f"{owner}/{name}", component=component)

    # Get file tree (deep scan)
    file_tree = await github.get_file_tree(owner, name, depth=10)

    # Match files
    keywords = _get_keywords_for_component(component)
    matched_files = _match_files(file_tree, keywords)

    if not matched_files:
        result = {
            "component": component,
            "files": [],
            "dependencies": [],
            "install_command": "",
            "total_files": 0,
            "message": f"'{component}'와 매칭되는 파일을 찾을 수 없습니다.",
        }
        return [TextContent(
            type="text",
            text=json.dumps(result, ensure_ascii=False, indent=2),
        )]

    # Fetch file contents and extract imports (limit to source files)
    source_extensions = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
    all_imports: list[str] = []

    for filepath in matched_files:
        if not any(filepath.endswith(ext) for ext in source_extensions):
            continue
        try:
            content = await github.get_file_content(owner, name, filepath)
            imports = _extract_imports_from_content(content)
            all_imports.extend(imports)
        except Exception:
            # Skip files that can't be fetched (e.g., too large)
            _log("warning", "file_content_fetch_failed",
                 repo=f"{owner}/{name}", file=filepath)

    # Resolve dependencies
    dependencies = _resolve_npm_packages(all_imports)
    install_command = _build_install_command(dependencies)

    _log("info", "extract_component_complete",
         repo=f"{owner}/{name}",
         component=component,
         files=len(matched_files),
         deps=len(dependencies))

    result = {
        "component": component,
        "files": matched_files,
        "dependencies": dependencies,
        "install_command": install_command,
        "total_files": len(matched_files),
    }

    return [TextContent(
        type="text",
        text=json.dumps(result, ensure_ascii=False, indent=2),
    )]

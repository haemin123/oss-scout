"""inject_feature MCP tool.

Extracts selected code from a GitHub repo and provides
integration guidance (placement, dependencies, env vars, conflicts).

Key principle: Does NOT write files -- only returns code content
+ placement guide for Claude Code to apply.

Pipeline:
  1. Validate inputs (repo_url, feature, files, project_dir)
  2. Detect project stack
  3. Fetch file contents from GitHub (batch)
  4. Extract import dependencies per file
  5. Resolve 1-depth dependency files from same repo
  6. Determine placement (user-specified or auto-suggest)
  7. Check for conflicts in user's project
  8. Detect required npm/pip dependencies
  9. Detect required env vars
 10. Check license of source repo
 11. Return comprehensive result as TextContent JSON
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from mcp.types import TextContent, Tool

from server.core.github_client import GitHubClient, parse_repo_url
from server.core.license_check import check_license
from server.tools.extract_component import (
    _extract_imports_from_content,
    _resolve_npm_packages,
)
from server.tools.search_feature import _suggest_placement
from server.tools.wiring import detect_project_stack

logger = logging.getLogger("oss-scout")


def _log(level: str, event: str, **kwargs: Any) -> None:
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(json.dumps(entry, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

INJECT_FEATURE_TOOL = Tool(
    name="inject_feature",
    description="검색된 기능 코드를 추출하여 배치 가이드와 함께 반환합니다",
    inputSchema={
        "type": "object",
        "properties": {
            "repo_url": {
                "type": "string",
                "description": "코드를 추출할 GitHub 레포 URL",
            },
            "feature": {
                "type": "string",
                "description": "기능명",
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "추출할 파일 경로 목록",
            },
            "project_dir": {
                "type": "string",
                "description": "사용자의 기존 프로젝트 경로",
            },
            "placement": {
                "type": "object",
                "description": "커스텀 파일 배치 맵 (source->target)",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["repo_url", "feature", "files", "project_dir"],
    },
)


# ---------------------------------------------------------------------------
# Python import extraction
# ---------------------------------------------------------------------------

_PY_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE,
)


def _extract_python_imports(content: str) -> list[str]:
    """Extract top-level module names from Python import statements."""
    modules: list[str] = []
    for match in _PY_IMPORT_RE.finditer(content):
        mod = match.group(1) or match.group(2)
        if mod:
            modules.append(mod.split(".")[0])
    return modules


# ---------------------------------------------------------------------------
# Env var detection
# ---------------------------------------------------------------------------

# process.env.VAR_NAME  or  process.env["VAR_NAME"]  or  process.env['VAR_NAME']
_JS_ENV_RE = re.compile(
    r"""process\.env\.([A-Z_][A-Z0-9_]*)"""
    r"""|process\.env\[['"]([A-Z_][A-Z0-9_]*)['"]\]""",
)

# os.getenv("VAR")  or  os.environ["VAR"]  or  os.environ.get("VAR")
_PY_ENV_RE = re.compile(
    r"""os\.getenv\(\s*['"]([A-Z_][A-Z0-9_]*)['"]"""
    r"""|os\.environ\[['"]([A-Z_][A-Z0-9_]*)['"]\]"""
    r"""|os\.environ\.get\(\s*['"]([A-Z_][A-Z0-9_]*)['"]""",
)

# Common env var descriptions (heuristic)
_ENV_DESCRIPTIONS: dict[str, str] = {
    "STRIPE_SECRET_KEY": "Stripe 비밀 키",
    "STRIPE_PUBLISHABLE_KEY": "Stripe 공개 키",
    "STRIPE_WEBHOOK_SECRET": "Stripe 웹훅 시크릿",
    "DATABASE_URL": "데이터베이스 연결 URL",
    "NEXTAUTH_SECRET": "NextAuth 세션 비밀 키",
    "NEXTAUTH_URL": "NextAuth 콜백 URL",
    "NEXT_PUBLIC_SUPABASE_URL": "Supabase 프로젝트 URL",
    "NEXT_PUBLIC_SUPABASE_ANON_KEY": "Supabase 익명 키",
    "SUPABASE_SERVICE_ROLE_KEY": "Supabase 서비스 역할 키",
    "OPENAI_API_KEY": "OpenAI API 키",
    "AWS_ACCESS_KEY_ID": "AWS 액세스 키 ID",
    "AWS_SECRET_ACCESS_KEY": "AWS 시크릿 액세스 키",
    "REDIS_URL": "Redis 연결 URL",
    "SMTP_HOST": "SMTP 서버 호스트",
    "SMTP_PORT": "SMTP 서버 포트",
    "SMTP_USER": "SMTP 사용자명",
    "SMTP_PASSWORD": "SMTP 비밀번호",
    "SECRET_KEY": "애플리케이션 비밀 키",
    "JWT_SECRET": "JWT 서명 키",
}


def _extract_env_vars(content: str) -> list[dict[str, str]]:
    """Scan file content for environment variable references.

    Returns a list of dicts with 'name' and 'description' keys.
    Deduplicates by variable name.
    """
    found: dict[str, str] = {}

    for match in _JS_ENV_RE.finditer(content):
        var_name = match.group(1) or match.group(2)
        if var_name and var_name not in found:
            found[var_name] = _ENV_DESCRIPTIONS.get(var_name, f"{var_name} 환경 변수")

    for match in _PY_ENV_RE.finditer(content):
        var_name = match.group(1) or match.group(2) or match.group(3)
        if var_name and var_name not in found:
            found[var_name] = _ENV_DESCRIPTIONS.get(var_name, f"{var_name} 환경 변수")

    return [{"name": k, "description": v} for k, v in sorted(found.items())]


# ---------------------------------------------------------------------------
# Dependency detection
# ---------------------------------------------------------------------------

# Standard library modules that should not be listed as pip dependencies
_PYTHON_STDLIB = frozenset({
    "os", "sys", "re", "json", "pathlib", "typing", "collections",
    "functools", "itertools", "datetime", "time", "math", "hashlib",
    "logging", "unittest", "io", "abc", "enum", "dataclasses",
    "contextlib", "asyncio", "http", "urllib", "socket", "ssl",
    "subprocess", "shutil", "tempfile", "glob", "copy", "uuid",
    "base64", "hmac", "secrets", "string", "textwrap", "struct",
    "csv", "configparser", "argparse", "threading", "multiprocessing",
})


def _detect_dependencies(
    contents: dict[str, str],
    language: str,
) -> dict[str, Any]:
    """Detect npm or pip package dependencies from file contents.

    Args:
        contents: Mapping of file path to file content.
        language: Detected language ('typescript', 'javascript', 'python').

    Returns:
        Dict with 'npm' or 'pip' list and 'install_command'.
    """
    if language == "python":
        all_modules: set[str] = set()
        for content in contents.values():
            py_imports = _extract_python_imports(content)
            for mod in py_imports:
                if mod not in _PYTHON_STDLIB:
                    all_modules.add(mod)
        pkgs = sorted(all_modules)
        install_cmd = f"pip install {' '.join(pkgs)}" if pkgs else ""
        return {"pip": pkgs, "install_command": install_cmd}

    # JS/TS
    all_imports: list[str] = []
    for content in contents.values():
        all_imports.extend(_extract_imports_from_content(content))

    npm_pkgs = _resolve_npm_packages(all_imports)
    install_cmd = f"npm install {' '.join(npm_pkgs)}" if npm_pkgs else ""
    return {"npm": npm_pkgs, "install_command": install_cmd}


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

def _check_conflicts(
    target_paths: list[str],
    project_dir: str,
) -> list[dict[str, str]]:
    """Check if any target file paths already exist in the user's project.

    Returns a list of conflict dicts with 'target_path' and 'reason'.
    """
    conflicts: list[dict[str, str]] = []
    project_path = Path(project_dir)

    for target in target_paths:
        full_path = project_path / target
        if full_path.exists():
            conflicts.append({
                "target_path": target,
                "reason": "이미 존재하는 파일",
            })

    return conflicts


# ---------------------------------------------------------------------------
# Integration notes generation
# ---------------------------------------------------------------------------

# Framework-specific integration hints (rule-based, no LLM)
_INTEGRATION_HINTS: dict[str, list[str]] = {
    "nextjs": [
        "app/layout.tsx에 Provider 추가가 필요할 수 있습니다.",
        "next.config.js에 환경 변수 설정을 확인하세요.",
    ],
    "react": [
        "src/App.tsx 또는 최상위 컴포넌트에 Provider 추가가 필요할 수 있습니다.",
        "환경 변수는 REACT_APP_ 접두사가 필요합니다.",
    ],
    "express": [
        "app.ts 또는 index.ts에 라우터를 등록하세요.",
        "미들웨어 순서에 주의하세요.",
    ],
    "fastapi": [
        "main.py에 라우터를 include_router()로 등록하세요.",
        ".env 파일에 환경 변수를 추가하세요.",
    ],
    "vue": [
        "main.ts에 플러그인이나 컴포넌트를 등록하세요.",
        "환경 변수는 VITE_ 접두사가 필요합니다.",
    ],
    "django": [
        "settings.py INSTALLED_APPS에 앱을 등록하세요.",
        "urls.py에 URL 패턴을 추가하세요.",
    ],
    "flask": [
        "app.py에 Blueprint를 등록하세요.",
        "config.py에 환경 변수를 추가하세요.",
    ],
    "fastify": [
        "app.ts에 플러그인을 등록하세요.",
        "라우트 prefix를 설정하세요.",
    ],
}


def _generate_integration_notes(
    feature: str,
    framework: str | None,
    env_vars: list[dict[str, str]],
    dependencies: dict[str, Any],
    conflicts: list[dict[str, str]],
) -> str:
    """Generate rule-based integration notes.

    Returns a multi-line string with numbered steps.
    """
    notes: list[str] = []
    step = 1

    # Framework-specific hints
    if framework and framework in _INTEGRATION_HINTS:
        for hint in _INTEGRATION_HINTS[framework]:
            notes.append(f"{step}. {hint}")
            step += 1

    # Dependency install
    install_cmd = dependencies.get("install_command", "")
    if install_cmd:
        notes.append(f"{step}. 의존성 설치: `{install_cmd}`")
        step += 1

    # Env vars
    if env_vars:
        var_names = ", ".join(v["name"] for v in env_vars)
        notes.append(f"{step}. .env 파일에 다음 환경 변수를 설정하세요: {var_names}")
        step += 1

    # Conflicts
    if conflicts:
        conflict_paths = ", ".join(c["target_path"] for c in conflicts)
        notes.append(
            f"{step}. 충돌 파일이 있습니다. 기존 코드와 머지가 필요합니다: {conflict_paths}"
        )
        step += 1

    if not notes:
        notes.append(f"1. '{feature}' 기능 파일을 프로젝트에 복사하세요.")

    return "\n".join(notes)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def _validate_inject_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize inject_feature arguments.

    Raises ValueError with Korean messages on invalid input.
    """
    repo_url = arguments.get("repo_url", "")
    if not isinstance(repo_url, str) or not repo_url.strip():
        raise ValueError("repo_url을 입력해주세요.")

    feature = arguments.get("feature", "")
    if not isinstance(feature, str) or not feature.strip():
        raise ValueError("feature를 입력해주세요.")
    if len(feature) > 200:
        raise ValueError("기능명은 200자 이내로 입력해주세요.")

    files = arguments.get("files")
    if not isinstance(files, list) or len(files) == 0:
        raise ValueError("추출할 파일 목록(files)을 입력해주세요.")
    for i, f in enumerate(files):
        if not isinstance(f, str) or not f.strip():
            raise ValueError(f"files[{i}]가 비어 있습니다.")

    project_dir = arguments.get("project_dir", "")
    if not isinstance(project_dir, str) or not project_dir.strip():
        raise ValueError("project_dir를 입력해주세요.")

    placement = arguments.get("placement")
    if placement is not None and not isinstance(placement, dict):
        raise ValueError("placement는 object 형식이어야 합니다.")

    return {
        "repo_url": repo_url.strip().rstrip("/"),
        "feature": feature.strip(),
        "files": [f.strip() for f in files],
        "project_dir": project_dir.strip(),
        "placement": placement,
    }


# ---------------------------------------------------------------------------
# 1-depth dependency resolution
# ---------------------------------------------------------------------------

def _resolve_relative_imports(
    content: str,
    file_path: str,
    language: str,
) -> list[str]:
    """Extract relative import paths and resolve them to repo-relative paths.

    Only resolves 1-depth (direct imports from the same repo).
    Returns a list of repo-relative file paths.
    """
    if language == "python":
        # from .module import X  or  from ..module import X
        py_rel_re = re.compile(r"^\s*from\s+(\.[\w.]*)\s+import", re.MULTILINE)
        results: list[str] = []
        base_dir = str(Path(file_path).parent)
        for match in py_rel_re.finditer(content):
            rel = match.group(1)
            # Count leading dots
            dots = len(rel) - len(rel.lstrip("."))
            module_part = rel.lstrip(".")
            if not module_part:
                continue
            # Navigate up for each dot beyond 1
            target_dir = base_dir
            for _ in range(dots - 1):
                target_dir = str(Path(target_dir).parent)
            module_file = module_part.replace(".", "/") + ".py"
            resolved = f"{target_dir}/{module_file}" if target_dir else module_file
            # Normalize path (remove leading ./)
            resolved = str(Path(resolved))
            results.append(resolved.replace("\\", "/"))
        return results

    # JS/TS: relative imports
    js_rel_re = re.compile(
        r"""(?:import|export)\s+.*?from\s+['"](\.[^'"]+)['"]"""
        r"""|require\s*\(\s*['"](\.[^'"]+)['"]\s*\)""",
    )
    results = []
    base_dir = str(Path(file_path).parent)
    extensions = [".ts", ".tsx", ".js", ".jsx", ".mjs", ""]

    for match in js_rel_re.finditer(content):
        rel_path = match.group(1) or match.group(2)
        if not rel_path:
            continue
        # Resolve relative to file's directory
        raw_resolved = os.path.normpath(os.path.join(base_dir, rel_path))
        raw_resolved = raw_resolved.replace("\\", "/")

        # Try with common extensions
        for ext in extensions:
            candidate = raw_resolved + ext
            results.append(candidate)
            # Also try index file
            if not ext:
                results.append(raw_resolved + "/index.ts")
                results.append(raw_resolved + "/index.tsx")
                results.append(raw_resolved + "/index.js")

    return results


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

async def handle_inject_feature(
    arguments: dict[str, Any],
    github: GitHubClient,
) -> list[TextContent]:
    """Execute the inject_feature pipeline.

    1. Validate inputs
    2. Detect project stack
    3. Fetch file contents from GitHub
    4. Extract import dependencies per file
    5. Resolve 1-depth dependency files
    6. Determine placement
    7. Check for conflicts
    8. Detect npm/pip dependencies
    9. Detect env vars
    10. Check license
    11. Return comprehensive result
    """
    args = _validate_inject_args(arguments)
    repo_url: str = args["repo_url"]
    feature: str = args["feature"]
    files: list[str] = args["files"]
    project_dir: str = args["project_dir"]
    custom_placement: dict[str, str] | None = args["placement"]

    _log("info", "inject_feature_start", feature=feature, repo_url=repo_url)

    # Step 1: Validate repo_url
    owner, name = parse_repo_url(repo_url)
    repo_full_name = f"{owner}/{name}"

    # Step 2: Validate project_dir exists
    project_path = Path(project_dir).resolve()
    if not project_path.exists():
        raise ValueError(f"프로젝트 디렉토리가 존재하지 않습니다: {project_dir}")

    # Step 3: Detect project stack
    stack = detect_project_stack(project_dir)
    language = stack.get("language") or "typescript"
    framework = stack.get("framework")

    _log("info", "stack_detected", language=language, framework=framework)

    # Step 4: Fetch file contents from GitHub (batch)
    file_contents = await github.get_file_content_batch(repo_full_name, files)

    if not file_contents:
        response: dict[str, Any] = {
            "feature": feature,
            "source_repo": repo_full_name,
            "license": None,
            "files": [],
            "dependencies": {},
            "env_vars_needed": [],
            "conflicts": [],
            "integration_notes": "",
            "message": "지정한 파일을 가져올 수 없습니다.",
        }
        return [TextContent(
            type="text",
            text=json.dumps(response, ensure_ascii=False, indent=2),
        )]

    # Step 5: Extract import dependencies and resolve 1-depth deps
    dep_candidates: set[str] = set()
    for fpath, content in file_contents.items():
        rel_imports = _resolve_relative_imports(content, fpath, language)
        for candidate in rel_imports:
            # Only add if not already in requested files
            if candidate not in file_contents:
                dep_candidates.add(candidate)

    # Fetch dependency files (only those that exist in the repo)
    dep_contents: dict[str, str] = {}
    if dep_candidates:
        dep_contents = await github.get_file_content_batch(
            repo_full_name, list(dep_candidates),
        )

    # Step 6: Determine placement
    all_source_files = list(file_contents.keys()) + list(dep_contents.keys())

    if custom_placement:
        placement = dict(custom_placement)
        # For files not in custom placement, use auto-suggest
        unmapped = [f for f in all_source_files if f not in placement]
        if unmapped:
            auto_placement = _suggest_placement(
                unmapped, feature, framework, project_dir,
            )
            placement.update(auto_placement)
    else:
        placement = _suggest_placement(
            all_source_files, feature, framework, project_dir,
        )

    # Step 7: Check for conflicts
    target_paths = list(placement.values())
    conflicts = _check_conflicts(target_paths, project_dir)

    # Step 8: Detect npm/pip dependencies
    all_contents = {**file_contents, **dep_contents}
    dependencies = _detect_dependencies(all_contents, language)

    # Step 9: Detect env vars
    all_env_vars: list[dict[str, str]] = []
    seen_vars: set[str] = set()
    for content in all_contents.values():
        for env_var in _extract_env_vars(content):
            if env_var["name"] not in seen_vars:
                seen_vars.add(env_var["name"])
                all_env_vars.append(env_var)

    # Step 10: Check license
    try:
        license_data = await github.get_license(owner, name)
        spdx_id = license_data.get("spdx_id")
    except Exception:
        spdx_id = None

    license_result = check_license(spdx_id)
    license_str = license_result.spdx_id or license_result.license

    # Step 11: Build file list for output
    output_files: list[dict[str, Any]] = []

    for fpath, content in file_contents.items():
        output_files.append({
            "source_path": fpath,
            "target_path": placement.get(fpath, fpath),
            "content": content,
            "is_dependency": False,
        })

    for fpath, content in dep_contents.items():
        output_files.append({
            "source_path": fpath,
            "target_path": placement.get(fpath, fpath),
            "content": content,
            "is_dependency": True,
        })

    # Integration notes
    integration_notes = _generate_integration_notes(
        feature, framework, all_env_vars, dependencies, conflicts,
    )

    response = {
        "feature": feature,
        "source_repo": repo_full_name,
        "license": license_str,
        "files": output_files,
        "dependencies": dependencies,
        "env_vars_needed": all_env_vars,
        "conflicts": conflicts,
        "integration_notes": integration_notes,
    }

    _log(
        "info", "inject_feature_complete",
        feature=feature,
        files=len(output_files),
        deps=len(dependencies.get("npm", dependencies.get("pip", []))),
    )

    return [TextContent(
        type="text",
        text=json.dumps(response, ensure_ascii=False, indent=2),
    )]

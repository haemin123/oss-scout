"""generate_wiring MCP tool.

Generates connection code (API hooks, auth guards, DB CRUD, etc.)
between components in a project, based on detected tech stack.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mcp.types import TextContent, Tool

from server.tools.wiring_templates import get_template_module

logger = logging.getLogger("oss-scout")


def _log(level: str, event: str, **kwargs: Any) -> None:
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(json.dumps(entry, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

GENERATE_WIRING_TOOL = Tool(
    name="generate_wiring",
    description=(
        "프로젝트 내 컴포넌트 간 연결 코드(API 호출 훅, Auth 가드, DB CRUD 등)를 생성합니다."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": "프로젝트 디렉토리",
            },
            "wiring_type": {
                "type": "string",
                "enum": [
                    "api-hook",
                    "auth-guard",
                    "db-crud",
                    "file-upload",
                    "websocket",
                    "sse-stream",
                    "form-handler",
                    "middleware",
                ],
                "description": "생성할 연결 코드 유형",
            },
            "config": {
                "type": "object",
                "description": (
                    "유형별 설정 "
                    '(예: {"endpoint": "/api/chat", "method": "POST", "streaming": true})'
                ),
            },
        },
        "required": ["project_dir", "wiring_type"],
    },
)

# ---------------------------------------------------------------------------
# Stack detection
# ---------------------------------------------------------------------------

# Dependency -> stack identifier (for package.json)
_JS_DEP_MAP: dict[str, tuple[str, str]] = {
    # (category, stack_id)
    "next": ("framework", "nextjs"),
    "vue": ("framework", "vue"),
    "react": ("framework", "react"),
    "express": ("framework", "express"),
    "fastify": ("framework", "fastify"),
    # Auth
    "next-auth": ("auth", "next-auth"),
    "firebase": ("auth", "firebase"),
    "@clerk/nextjs": ("auth", "clerk"),
    # DB
    "@prisma/client": ("db", "prisma"),
    "prisma": ("db", "prisma"),
    "firebase-admin": ("db", "firestore"),
    "@supabase/supabase-js": ("db", "supabase"),
    "mongoose": ("db", "mongoose"),
    "mongodb": ("db", "mongodb"),
    # Storage
    "@vercel/blob": ("storage", "vercel-blob"),
    "@aws-sdk/client-s3": ("storage", "s3"),
    # UI
    "tailwindcss": ("ui", "tailwind"),
    "@chakra-ui/react": ("ui", "chakra"),
    "@mui/material": ("ui", "mui"),
}

_PYTHON_PKG_MAP: dict[str, tuple[str, str]] = {
    "fastapi": ("framework", "fastapi"),
    "django": ("framework", "django"),
    "flask": ("framework", "flask"),
    "sqlalchemy": ("db", "postgres"),
    "psycopg2": ("db", "postgres"),
    "psycopg2-binary": ("db", "postgres"),
    "pymongo": ("db", "mongodb"),
    "firebase-admin": ("db", "firestore"),
}


def detect_project_stack(project_dir: str) -> dict[str, str | None]:
    """Detect the tech stack of a project by inspecting config files.

    Returns a dict with keys: framework, language, db, auth, storage, ui.
    """
    stack: dict[str, str | None] = {
        "framework": None,
        "language": None,
        "db": None,
        "auth": None,
        "storage": None,
        "ui": None,
    }

    project_path = Path(project_dir)

    # --- Node.js / JavaScript / TypeScript ---
    package_json_path = project_path / "package.json"
    if package_json_path.exists():
        try:
            pkg = json.loads(package_json_path.read_text(encoding="utf-8"))
            all_deps: dict[str, str] = {}
            for key in ("dependencies", "devDependencies"):
                deps = pkg.get(key)
                if isinstance(deps, dict):
                    all_deps.update(deps)

            # Check for TypeScript
            tsconfig = project_path / "tsconfig.json"
            stack["language"] = "typescript" if tsconfig.exists() else "javascript"

            # Map dependencies to stack categories
            for dep_name, (category, stack_id) in _JS_DEP_MAP.items():
                if dep_name in all_deps and stack[category] is None:
                    stack[category] = stack_id

        except (json.JSONDecodeError, OSError):
            pass

    # --- Python ---
    requirements_path = project_path / "requirements.txt"
    pyproject_path = project_path / "pyproject.toml"

    if requirements_path.exists():
        stack["language"] = stack["language"] or "python"
        try:
            text = requirements_path.read_text(encoding="utf-8")
            for line in text.strip().splitlines():
                pkg_name = (
                    line.strip()
                    .split("==")[0]
                    .split(">=")[0]
                    .split("<=")[0]
                    .split("[")[0]
                    .strip()
                    .lower()
                )
                if pkg_name in _PYTHON_PKG_MAP:
                    category, stack_id = _PYTHON_PKG_MAP[pkg_name]
                    if stack[category] is None:
                        stack[category] = stack_id
        except OSError:
            pass

    elif pyproject_path.exists():
        stack["language"] = stack["language"] or "python"
        # Basic pyproject.toml parsing (no toml dependency needed)
        try:
            text = pyproject_path.read_text(encoding="utf-8")
            for pkg_name, (category, stack_id) in _PYTHON_PKG_MAP.items():
                if pkg_name in text and stack[category] is None:
                    stack[category] = stack_id
        except OSError:
            pass

    return stack


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

async def handle_generate_wiring(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Execute the generate_wiring pipeline.

    1. Validate inputs
    2. Detect project stack
    3. Select and run appropriate template
    4. Return generated files
    """
    # --- Validate inputs ---
    project_dir = arguments.get("project_dir", "")
    if not isinstance(project_dir, str) or not project_dir.strip():
        raise ValueError("project_dir를 입력해주세요.")

    wiring_type = arguments.get("wiring_type", "")
    if not isinstance(wiring_type, str) or not wiring_type.strip():
        raise ValueError("wiring_type을 입력해주세요.")

    config: dict[str, Any] = arguments.get("config") or {}
    if not isinstance(config, dict):
        raise ValueError("config는 객체 형식이어야 합니다.")

    project_path = Path(project_dir).resolve()
    if not project_path.exists():
        raise ValueError(f"프로젝트 디렉토리가 존재하지 않습니다: {project_dir}")

    _log("info", "generate_wiring_start", project=project_dir, wiring_type=wiring_type)

    # --- Detect stack ---
    stack = detect_project_stack(project_dir)
    _log("info", "stack_detected", stack=json.dumps(
        {k: v for k, v in stack.items() if v}, ensure_ascii=False,
    ))

    # --- Get template ---
    try:
        template_module = get_template_module(wiring_type)
    except KeyError as e:
        raise ValueError(str(e)) from e

    result = template_module.generate(stack, config)

    # --- Build response ---
    response = {
        "wiring_type": wiring_type,
        "stack_detected": {k: v for k, v in stack.items() if v is not None},
        "files": result["files"],
        "usage_example": result.get("usage_example", ""),
        "dependencies_needed": result.get("dependencies_needed", []),
    }

    _log(
        "info",
        "generate_wiring_complete",
        wiring_type=wiring_type,
        files_count=len(result["files"]),
    )

    return [TextContent(
        type="text",
        text=json.dumps(response, ensure_ascii=False, indent=2),
    )]

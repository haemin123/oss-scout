"""adapt_stack MCP tool.

Detects the current tech stack of a project and generates
a migration plan to convert it to a target stack.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mcp.types import TextContent, Tool

logger = logging.getLogger("oss-scout")


def _log(level: str, event: str, **kwargs: Any) -> None:
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(json.dumps(entry, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Stack migration rules database
# ---------------------------------------------------------------------------

STACK_MIGRATIONS: dict[tuple[str, str], dict[str, Any]] = {
    ("vercel-postgres", "firestore"): {
        "remove_deps": ["@vercel/postgres"],
        "add_deps": ["firebase", "firebase-admin"],
        "file_patterns": ["lib/db.*", "prisma/**"],
        "migration_notes": "Vercel Postgres SQL 쿼리를 Firestore 문서 기반으로 변환 필요",
    },
    ("next-auth", "firebase-auth"): {
        "remove_deps": ["next-auth"],
        "add_deps": ["firebase"],
        "file_patterns": ["auth.*", "middleware.*", "app/api/auth/**"],
        "migration_notes": "NextAuth 프로바이더를 Firebase Auth 프로바이더로 교체",
    },
    ("prisma", "firestore"): {
        "remove_deps": ["@prisma/client", "prisma"],
        "add_deps": ["firebase-admin"],
        "file_patterns": ["prisma/**", "lib/prisma.*"],
        "migration_notes": "Prisma 스키마를 Firestore 컬렉션 구조로 재설계",
    },
    ("supabase", "firebase"): {
        "remove_deps": ["@supabase/supabase-js", "@supabase/auth-helpers-nextjs"],
        "add_deps": ["firebase"],
        "file_patterns": ["lib/supabase.*", "utils/supabase.*"],
        "migration_notes": "Supabase 클라이언트를 Firebase SDK로 교체",
    },
    ("vercel-blob", "firebase-storage"): {
        "remove_deps": ["@vercel/blob"],
        "add_deps": ["firebase"],
        "file_patterns": ["lib/storage.*", "lib/blob.*"],
        "migration_notes": "Vercel Blob API를 Firebase Storage API로 교체",
    },
    ("postgres", "mongodb"): {
        "remove_deps": ["pg", "@prisma/client"],
        "add_deps": ["mongoose", "mongodb"],
        "file_patterns": ["lib/db.*", "models/**"],
        "migration_notes": "SQL 스키마를 MongoDB 스키마로 변환",
    },
    ("express", "fastify"): {
        "remove_deps": ["express"],
        "add_deps": ["fastify"],
        "file_patterns": ["server.*", "routes/**", "middleware/**"],
        "migration_notes": "Express 미들웨어를 Fastify 플러그인으로 변환",
    },
}

# Dependency name -> stack identifier mapping for auto-detection
_DEP_TO_STACK: dict[str, str] = {
    "@vercel/postgres": "vercel-postgres",
    "next-auth": "next-auth",
    "@prisma/client": "prisma",
    "prisma": "prisma",
    "@supabase/supabase-js": "supabase",
    "@supabase/auth-helpers-nextjs": "supabase",
    "@vercel/blob": "vercel-blob",
    "pg": "postgres",
    "mongoose": "mongodb",
    "mongodb": "mongodb",
    "express": "express",
    "fastify": "fastify",
    "firebase": "firebase",
    "firebase-admin": "firebase-admin",
}

# Stack category grouping for detection
_STACK_CATEGORIES: dict[str, list[str]] = {
    "db": [
        "vercel-postgres", "prisma", "supabase", "postgres",
        "mongodb", "firestore", "firebase-admin",
    ],
    "auth": ["next-auth", "firebase-auth", "supabase"],
    "storage": ["vercel-blob", "firebase-storage"],
    "framework": ["express", "fastify"],
}


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

ADAPT_STACK_TOOL = Tool(
    name="adapt_stack",
    description=(
        "프로젝트의 기술 스택을 다른 스택으로 전환하기 위한"
        " 마이그레이션 계획을 생성합니다."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": "프로젝트 디렉토리 (로컬)",
            },
            "target_stack": {
                "type": "object",
                "description": (
                    "변환 대상 스택 (예: {\"db\": \"firestore\", \"auth\": \"firebase-auth\"})"
                ),
            },
        },
        "required": ["project_dir", "target_stack"],
    },
)


# ---------------------------------------------------------------------------
# Core logic (pure functions for testability)
# ---------------------------------------------------------------------------

def detect_stack_from_package_json(package_data: dict[str, Any]) -> dict[str, str]:
    """Detect the current tech stack from package.json dependencies.

    Returns a dict like {"db": "prisma", "auth": "next-auth"}.
    """
    all_deps: dict[str, str] = {}
    for dep_key in ("dependencies", "devDependencies"):
        deps = package_data.get(dep_key)
        if isinstance(deps, dict):
            all_deps.update(deps)

    detected_stacks: set[str] = set()
    for dep_name in all_deps:
        stack_id = _DEP_TO_STACK.get(dep_name)
        if stack_id:
            detected_stacks.add(stack_id)

    # Map detected stacks to categories
    result: dict[str, str] = {}
    for category, stack_ids in _STACK_CATEGORIES.items():
        for stack_id in stack_ids:
            if stack_id in detected_stacks:
                result[category] = stack_id
                break

    return result


def detect_stack_from_requirements(requirements_text: str) -> dict[str, str]:
    """Detect stack from requirements.txt (Python projects)."""
    result: dict[str, str] = {}
    lines = requirements_text.strip().splitlines()
    for line in lines:
        pkg = line.strip().split("==")[0].split(">=")[0].split("<=")[0].split("[")[0].strip()
        lower_pkg = pkg.lower()
        if lower_pkg in ("flask", "django", "fastapi"):
            result["framework"] = lower_pkg
        if lower_pkg in ("sqlalchemy", "psycopg2", "psycopg2-binary"):
            result["db"] = "postgres"
        if lower_pkg == "pymongo":
            result["db"] = "mongodb"
        if lower_pkg == "firebase-admin":
            result["db"] = "firebase-admin"
    return result


def find_affected_files(
    project_files: list[str],
    file_patterns: list[str],
) -> list[str]:
    """Find project files matching migration file patterns.

    Patterns support:
      - "lib/db.*" -> matches lib/db.ts, lib/db.js, etc.
      - "prisma/**" -> matches anything under prisma/
      - "auth.*" -> matches auth.ts, auth.js, etc.
    """
    import fnmatch

    affected: list[str] = []
    for filepath in project_files:
        for pattern in file_patterns:
            # Convert simple patterns to glob-compatible
            if fnmatch.fnmatch(filepath, pattern):
                affected.append(filepath)
                break
            # Also match if the pattern is a prefix directory
            if pattern.endswith("/**") and filepath.startswith(pattern[:-3]):
                affected.append(filepath)
                break
    return sorted(affected)


def _estimate_effort(migrations: list[dict[str, Any]]) -> str:
    """Estimate total migration effort based on the number and type of migrations."""
    if not migrations:
        return "none"
    total_files = sum(len(m.get("affected_files", [])) for m in migrations)
    if total_files > 10 or len(migrations) >= 3:
        return "high"
    if total_files > 5 or len(migrations) >= 2:
        return "medium"
    return "low"


def build_migration_plan(
    current_stack: dict[str, str],
    target_stack: dict[str, str],
    project_files: list[str],
) -> dict[str, Any]:
    """Build a migration plan comparing current vs target stack.

    Returns a complete migration plan with steps, affected files,
    and install/uninstall commands.
    """
    migrations: list[dict[str, Any]] = []
    all_add_deps: list[str] = []
    all_remove_deps: list[str] = []
    total_affected: set[str] = set()

    for category, target_id in target_stack.items():
        current_id = current_stack.get(category)
        if current_id == target_id:
            continue  # Already on target stack
        if current_id is None:
            # No current stack detected for this category — just add
            migrations.append({
                "from": "none",
                "to": target_id,
                "remove_deps": [],
                "add_deps": STACK_MIGRATIONS.get(("none", target_id), {}).get("add_deps", []),
                "affected_files": [],
                "notes": f"{category} 카테고리에 {target_id} 새로 추가",
                "effort": "low",
            })
            continue

        migration_key = (current_id, target_id)
        rule = STACK_MIGRATIONS.get(migration_key)

        if rule:
            affected = find_affected_files(project_files, rule["file_patterns"])
            total_affected.update(affected)
            effort = "high" if len(affected) > 5 else "medium" if len(affected) > 2 else "low"
            migrations.append({
                "from": current_id,
                "to": target_id,
                "remove_deps": rule["remove_deps"],
                "add_deps": rule["add_deps"],
                "affected_files": affected,
                "notes": rule["migration_notes"],
                "effort": effort,
            })
            all_add_deps.extend(rule["add_deps"])
            all_remove_deps.extend(rule["remove_deps"])
        else:
            # Unknown migration path
            migrations.append({
                "from": current_id,
                "to": target_id,
                "remove_deps": [],
                "add_deps": [],
                "affected_files": [],
                "notes": (
                    f"{current_id} -> {target_id}"
                    " 자동 마이그레이션 규칙 없음. 수동 전환 필요."
                ),
                "effort": "unknown",
            })

    # Build install command
    unique_add = sorted(set(all_add_deps))
    unique_remove = sorted(set(all_remove_deps))
    install_parts: list[str] = []
    if unique_add:
        install_parts.append(f"npm install {' '.join(unique_add)}")
    if unique_remove:
        install_parts.append(f"npm uninstall {' '.join(unique_remove)}")
    install_command = " && ".join(install_parts)

    return {
        "current_stack": current_stack,
        "target_stack": target_stack,
        "migrations": migrations,
        "total_affected_files": len(total_affected),
        "estimated_effort": _estimate_effort(migrations),
        "install_command": install_command,
    }


def _scan_project_files(project_dir: Path) -> list[str]:
    """Scan project directory for source files (non-recursive beyond 4 levels).

    Excludes node_modules, .git, dist, build, etc.
    """
    exclude_dirs = {"node_modules", ".git", "dist", "build", ".next", "__pycache__", ".cache"}
    files: list[str] = []
    try:
        for item in project_dir.rglob("*"):
            if item.is_file():
                # Check excluded dirs in path parts
                parts = item.relative_to(project_dir).parts
                if any(p in exclude_dirs for p in parts):
                    continue
                if len(parts) > 5:
                    continue
                files.append(str(item.relative_to(project_dir)).replace("\\", "/"))
    except (PermissionError, OSError):
        pass
    return sorted(files)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

async def handle_adapt_stack(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Execute the adapt_stack pipeline.

    1. Validate inputs
    2. Scan project directory for current stack
    3. Compare with target stack
    4. Generate migration plan
    5. Return result
    """
    # Validate inputs
    project_dir_str = arguments.get("project_dir", "")
    if not isinstance(project_dir_str, str) or not project_dir_str.strip():
        raise ValueError("project_dir를 입력해주세요.")

    target_stack = arguments.get("target_stack")
    if not isinstance(target_stack, dict) or not target_stack:
        raise ValueError("target_stack을 입력해주세요. (예: {\"db\": \"firestore\"})")

    project_dir = Path(project_dir_str).resolve()
    if not project_dir.exists():
        raise ValueError(f"프로젝트 디렉토리가 존재하지 않습니다: {project_dir_str}")

    _log("info", "adapt_stack_start",
         project=project_dir_str, target=json.dumps(target_stack, ensure_ascii=False))

    # Detect current stack
    current_stack: dict[str, str] = {}

    # Check package.json (Node.js projects)
    package_json_path = project_dir / "package.json"
    if package_json_path.exists():
        try:
            package_data = json.loads(package_json_path.read_text(encoding="utf-8"))
            current_stack.update(detect_stack_from_package_json(package_data))
        except (json.JSONDecodeError, OSError):
            _log("warning", "package_json_parse_failed", path=str(package_json_path))

    # Check requirements.txt (Python projects)
    requirements_path = project_dir / "requirements.txt"
    if requirements_path.exists():
        try:
            requirements_text = requirements_path.read_text(encoding="utf-8")
            current_stack.update(detect_stack_from_requirements(requirements_text))
        except OSError:
            _log("warning", "requirements_parse_failed", path=str(requirements_path))

    # Scan project files
    project_files = _scan_project_files(project_dir)

    # Build migration plan
    plan = build_migration_plan(current_stack, target_stack, project_files)

    _log("info", "adapt_stack_complete",
         project=project_dir_str,
         migrations=len(plan["migrations"]),
         effort=plan["estimated_effort"])

    return [TextContent(
        type="text",
        text=json.dumps(plan, ensure_ascii=False, indent=2),
    )]

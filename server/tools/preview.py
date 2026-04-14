"""preview MCP tool.

Detects project type and returns dev server commands
for instant local preview after scaffolding.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mcp.types import TextContent, Tool

logger = logging.getLogger("oss-scout")

PREVIEW_TOOL = Tool(
    name="preview",
    description="프로젝트의 로컬 개발 서버를 시작하여 브라우저에서 바로 확인합니다.",
    inputSchema={
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": "프로젝트 디렉토리 경로",
            },
            "port": {
                "type": "integer",
                "description": "서버 포트 (기본: 자동 감지)",
                "default": 0,
            },
        },
        "required": ["project_dir"],
    },
)


def detect_project_type(project_dir: str) -> dict[str, Any]:
    """프로젝트 유형과 dev server 명령어를 감지합니다."""
    path = Path(project_dir)

    # package.json 기반 (Node.js)
    pkg_json = path / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pkg = {}

        scripts = pkg.get("scripts", {})

        # dev 서버 명령어 우선순위
        if "dev" in scripts:
            return {"type": "node", "command": "npm run dev", "port": 3000}
        if "start" in scripts:
            return {"type": "node", "command": "npm start", "port": 3000}
        if "serve" in scripts:
            return {"type": "node", "command": "npm run serve", "port": 8080}

        # Next.js
        deps = pkg.get("dependencies", {})
        if "next" in deps:
            return {"type": "nextjs", "command": "npx next dev", "port": 3000}

        # Vite
        dev_deps = pkg.get("devDependencies", {})
        if "vite" in dev_deps:
            return {"type": "vite", "command": "npx vite", "port": 5173}

    # Python 기반
    if (path / "manage.py").exists():
        return {"type": "django", "command": "python manage.py runserver", "port": 8000}

    if (path / "app.py").exists() or (path / "main.py").exists():
        return {
            "type": "flask/fastapi",
            "command": "python -m uvicorn main:app --reload",
            "port": 8000,
        }

    # 정적 HTML
    if (path / "index.html").exists():
        return {"type": "static", "command": "python -m http.server", "port": 8080}

    # 감지 실패
    return {"type": "unknown", "command": None, "port": None}


def check_needs_install(project_dir: str, project_type: str) -> bool:
    """의존성 설치가 필요한지 확인합니다."""
    path = Path(project_dir)

    if project_type in ("node", "nextjs", "vite"):
        return not (path / "node_modules").exists()

    if project_type in ("django", "flask/fastapi"):
        return not (path / ".venv").exists() and not (path / "venv").exists()

    return False


def get_install_command(project_dir: str, project_type: str) -> str:
    """패키지 매니저에 맞는 설치 명령어를 반환합니다."""
    path = Path(project_dir)

    if project_type in ("node", "nextjs", "vite"):
        if (path / "pnpm-lock.yaml").exists():
            return "pnpm install"
        if (path / "yarn.lock").exists():
            return "yarn install"
        if (path / "bun.lockb").exists():
            return "bun install"
        return "npm install"

    if project_type in ("django", "flask/fastapi"):
        if (path / "pyproject.toml").exists():
            return "pip install -e ."
        if (path / "requirements.txt").exists():
            return "pip install -r requirements.txt"

    return ""


async def handle_preview(
    arguments: dict[str, Any],
    github: Any = None,
) -> list[TextContent]:
    """preview 툴 핸들러."""
    project_dir = arguments.get("project_dir", "")

    if not project_dir:
        return [TextContent(type="text", text=json.dumps(
            {"error": "project_dir는 필수 입력값입니다."},
            ensure_ascii=False,
        ))]

    path = Path(project_dir)
    if not path.exists() or not path.is_dir():
        return [TextContent(type="text", text=json.dumps(
            {"error": f"디렉토리가 존재하지 않습니다: {project_dir}"},
            ensure_ascii=False,
        ))]

    port = arguments.get("port", 0)

    # 1. 프로젝트 유형 감지
    project_info = detect_project_type(project_dir)

    if project_info["command"] is None:
        return [TextContent(type="text", text=json.dumps({
            "error": "프로젝트 유형을 감지할 수 없습니다.",
            "project_dir": project_dir,
            "suggestion": "수동으로 서버를 시작해주세요.",
        }, ensure_ascii=False, indent=2))]

    # 2. 포트 설정
    actual_port = port if port > 0 else project_info["port"]

    # 3. 의존성 설치 여부 확인
    needs_install = check_needs_install(project_dir, project_info["type"])

    # 4. 결과 반환
    install_cmd = get_install_command(project_dir, project_info["type"]) if needs_install else None

    result: dict[str, Any] = {
        "project_type": project_info["type"],
        "install_command": install_cmd,
        "dev_command": project_info["command"],
        "port": actual_port,
        "url": f"http://localhost:{actual_port}",
        "needs_install": needs_install,
        "instructions": [],
    }

    if needs_install and install_cmd:
        result["instructions"].append(f"의존성 설치: {install_cmd}")
    result["instructions"].append(f"서버 시작: {result['dev_command']}")
    result["instructions"].append(f"브라우저 열기: {result['url']}")

    logger.info(json.dumps({
        "level": "info",
        "event": "preview_detected",
        "project_type": project_info["type"],
        "port": actual_port,
    }, ensure_ascii=False))

    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

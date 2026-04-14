"""Tests for the preview tool."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from server.tools.preview import (
    check_needs_install,
    detect_project_type,
    get_install_command,
    handle_preview,
)


class TestDetectProjectType:
    """detect_project_type 테스트."""

    def test_node_with_dev_script(self, tmp_path: Path) -> None:
        """package.json에 dev 스크립트가 있을 때 node 타입으로 감지."""
        pkg = {"scripts": {"dev": "vite"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        result = detect_project_type(str(tmp_path))
        assert result["type"] == "node"
        assert result["command"] == "npm run dev"
        assert result["port"] == 3000

    def test_node_with_start_script(self, tmp_path: Path) -> None:
        """package.json에 start 스크립트가 있을 때 node 타입으로 감지."""
        pkg = {"scripts": {"start": "react-scripts start"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        result = detect_project_type(str(tmp_path))
        assert result["type"] == "node"
        assert result["command"] == "npm start"
        assert result["port"] == 3000

    def test_node_with_serve_script(self, tmp_path: Path) -> None:
        """package.json에 serve 스크립트가 있을 때 node 타입으로 감지."""
        pkg = {"scripts": {"serve": "vue-cli-service serve"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        result = detect_project_type(str(tmp_path))
        assert result["type"] == "node"
        assert result["command"] == "npm run serve"
        assert result["port"] == 8080

    def test_nextjs_detection(self, tmp_path: Path) -> None:
        """Next.js 프로젝트 감지 (dependencies에 next 포함)."""
        pkg = {"dependencies": {"next": "14.0.0", "react": "18.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        result = detect_project_type(str(tmp_path))
        assert result["type"] == "nextjs"
        assert result["command"] == "npx next dev"
        assert result["port"] == 3000

    def test_vite_detection(self, tmp_path: Path) -> None:
        """Vite 프로젝트 감지 (devDependencies에 vite 포함)."""
        pkg = {"devDependencies": {"vite": "5.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        result = detect_project_type(str(tmp_path))
        assert result["type"] == "vite"
        assert result["command"] == "npx vite"
        assert result["port"] == 5173

    def test_django_detection(self, tmp_path: Path) -> None:
        """manage.py 존재 시 Django 타입으로 감지."""
        (tmp_path / "manage.py").write_text("# django manage.py")

        result = detect_project_type(str(tmp_path))
        assert result["type"] == "django"
        assert result["command"] == "python manage.py runserver"
        assert result["port"] == 8000

    def test_flask_fastapi_detection_main(self, tmp_path: Path) -> None:
        """main.py 존재 시 flask/fastapi 타입으로 감지."""
        (tmp_path / "main.py").write_text("# fastapi app")

        result = detect_project_type(str(tmp_path))
        assert result["type"] == "flask/fastapi"
        assert result["port"] == 8000

    def test_flask_fastapi_detection_app(self, tmp_path: Path) -> None:
        """app.py 존재 시 flask/fastapi 타입으로 감지."""
        (tmp_path / "app.py").write_text("# flask app")

        result = detect_project_type(str(tmp_path))
        assert result["type"] == "flask/fastapi"
        assert result["port"] == 8000

    def test_static_html_detection(self, tmp_path: Path) -> None:
        """index.html만 존재 시 static 타입으로 감지."""
        (tmp_path / "index.html").write_text("<html></html>")

        result = detect_project_type(str(tmp_path))
        assert result["type"] == "static"
        assert result["command"] == "python -m http.server"
        assert result["port"] == 8080

    def test_empty_directory_unknown(self, tmp_path: Path) -> None:
        """빈 디렉토리는 unknown 타입."""
        result = detect_project_type(str(tmp_path))
        assert result["type"] == "unknown"
        assert result["command"] is None
        assert result["port"] is None

    def test_invalid_package_json(self, tmp_path: Path) -> None:
        """잘못된 package.json은 무시하고 다음 감지로 진행."""
        (tmp_path / "package.json").write_text("not valid json{{{")
        (tmp_path / "index.html").write_text("<html></html>")

        result = detect_project_type(str(tmp_path))
        assert result["type"] == "static"

    def test_dev_script_priority_over_nextjs(self, tmp_path: Path) -> None:
        """scripts.dev가 있으면 Next.js보다 우선."""
        pkg = {
            "scripts": {"dev": "next dev"},
            "dependencies": {"next": "14.0.0"},
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        result = detect_project_type(str(tmp_path))
        assert result["type"] == "node"
        assert result["command"] == "npm run dev"


class TestCheckNeedsInstall:
    """check_needs_install 테스트."""

    def test_node_needs_install(self, tmp_path: Path) -> None:
        """node_modules가 없으면 설치 필요."""
        assert check_needs_install(str(tmp_path), "node") is True

    def test_node_already_installed(self, tmp_path: Path) -> None:
        """node_modules가 있으면 설치 불필요."""
        (tmp_path / "node_modules").mkdir()
        assert check_needs_install(str(tmp_path), "node") is False

    def test_nextjs_needs_install(self, tmp_path: Path) -> None:
        """nextjs도 node_modules 기반."""
        assert check_needs_install(str(tmp_path), "nextjs") is True

    def test_vite_needs_install(self, tmp_path: Path) -> None:
        """vite도 node_modules 기반."""
        assert check_needs_install(str(tmp_path), "vite") is True

    def test_django_needs_install(self, tmp_path: Path) -> None:
        """venv 없으면 설치 필요."""
        assert check_needs_install(str(tmp_path), "django") is True

    def test_django_has_venv(self, tmp_path: Path) -> None:
        """venv 있으면 설치 불필요."""
        (tmp_path / "venv").mkdir()
        assert check_needs_install(str(tmp_path), "django") is False

    def test_django_has_dot_venv(self, tmp_path: Path) -> None:
        """.venv 있으면 설치 불필요."""
        (tmp_path / ".venv").mkdir()
        assert check_needs_install(str(tmp_path), "flask/fastapi") is False

    def test_static_no_install(self, tmp_path: Path) -> None:
        """static 타입은 설치 불필요."""
        assert check_needs_install(str(tmp_path), "static") is False

    def test_unknown_no_install(self, tmp_path: Path) -> None:
        """unknown 타입은 설치 불필요."""
        assert check_needs_install(str(tmp_path), "unknown") is False


class TestGetInstallCommand:
    """get_install_command 테스트."""

    def test_npm_default(self, tmp_path: Path) -> None:
        """lockfile 없으면 npm install."""
        assert get_install_command(str(tmp_path), "node") == "npm install"

    def test_pnpm_detected(self, tmp_path: Path) -> None:
        """pnpm-lock.yaml 있으면 pnpm install."""
        (tmp_path / "pnpm-lock.yaml").write_text("")
        assert get_install_command(str(tmp_path), "node") == "pnpm install"

    def test_yarn_detected(self, tmp_path: Path) -> None:
        """yarn.lock 있으면 yarn install."""
        (tmp_path / "yarn.lock").write_text("")
        assert get_install_command(str(tmp_path), "node") == "yarn install"

    def test_bun_detected(self, tmp_path: Path) -> None:
        """bun.lockb 있으면 bun install."""
        (tmp_path / "bun.lockb").write_text("")
        assert get_install_command(str(tmp_path), "node") == "bun install"

    def test_python_requirements(self, tmp_path: Path) -> None:
        """requirements.txt 있으면 pip install -r."""
        (tmp_path / "requirements.txt").write_text("flask")
        assert get_install_command(str(tmp_path), "django") == "pip install -r requirements.txt"

    def test_python_pyproject(self, tmp_path: Path) -> None:
        """pyproject.toml 있으면 pip install -e."""
        (tmp_path / "pyproject.toml").write_text("[project]")
        assert get_install_command(str(tmp_path), "flask/fastapi") == "pip install -e ."

    def test_static_empty(self, tmp_path: Path) -> None:
        """static 타입은 빈 문자열."""
        assert get_install_command(str(tmp_path), "static") == ""

    def test_pnpm_priority_over_yarn(self, tmp_path: Path) -> None:
        """pnpm-lock.yaml이 yarn.lock보다 우선."""
        (tmp_path / "pnpm-lock.yaml").write_text("")
        (tmp_path / "yarn.lock").write_text("")
        assert get_install_command(str(tmp_path), "vite") == "pnpm install"


class TestHandlePreview:
    """handle_preview 통합 테스트."""

    @pytest.mark.asyncio
    async def test_node_project(self, tmp_path: Path) -> None:
        """Node.js 프로젝트 프리뷰."""
        pkg = {"scripts": {"dev": "vite"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        result = await handle_preview({"project_dir": str(tmp_path)})
        data = json.loads(result[0].text)

        assert data["project_type"] == "node"
        assert data["dev_command"] == "npm run dev"
        assert data["needs_install"] is True
        assert data["url"] == "http://localhost:3000"

    @pytest.mark.asyncio
    async def test_custom_port(self, tmp_path: Path) -> None:
        """커스텀 포트 지정."""
        pkg = {"scripts": {"dev": "vite"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        result = await handle_preview({"project_dir": str(tmp_path), "port": 4000})
        data = json.loads(result[0].text)

        assert data["port"] == 4000
        assert data["url"] == "http://localhost:4000"

    @pytest.mark.asyncio
    async def test_unknown_project(self, tmp_path: Path) -> None:
        """빈 디렉토리 에러 처리."""
        result = await handle_preview({"project_dir": str(tmp_path)})
        data = json.loads(result[0].text)

        assert "error" in data
        assert "감지할 수 없습니다" in data["error"]

    @pytest.mark.asyncio
    async def test_nonexistent_directory(self) -> None:
        """존재하지 않는 디렉토리 에러 처리."""
        result = await handle_preview({"project_dir": "/nonexistent/path/xyz"})
        data = json.loads(result[0].text)

        assert "error" in data
        assert "존재하지 않습니다" in data["error"]

    @pytest.mark.asyncio
    async def test_empty_project_dir(self) -> None:
        """빈 project_dir 에러 처리."""
        result = await handle_preview({"project_dir": ""})
        data = json.loads(result[0].text)

        assert "error" in data

    @pytest.mark.asyncio
    async def test_installed_node_project(self, tmp_path: Path) -> None:
        """node_modules가 이미 있는 프로젝트."""
        pkg = {"scripts": {"dev": "vite"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "node_modules").mkdir()

        result = await handle_preview({"project_dir": str(tmp_path)})
        data = json.loads(result[0].text)

        assert data["needs_install"] is False
        assert data["install_command"] is None

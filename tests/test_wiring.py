"""Unit tests for server/tools/wiring.py and wiring_templates.

Tests focus on stack detection, template generation for each wiring type,
config option handling, and unknown stack fallback behavior.
All tests run without network or filesystem access (uses tmp_path).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.tools.wiring import (
    GENERATE_WIRING_TOOL,
    detect_project_stack,
    handle_generate_wiring,
)
from server.tools.wiring_templates import (
    api_hook,
    auth_guard,
    db_crud,
    file_upload,
    form_handler,
    get_template_module,
    middleware,
    sse_stream,
    websocket,
)

# ===========================================================================
# detect_project_stack
# ===========================================================================

class TestDetectProjectStack:
    def test_nextjs_typescript(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"next": "^14.0.0", "react": "^18.0.0"},
        }))
        (tmp_path / "tsconfig.json").write_text("{}")

        stack = detect_project_stack(str(tmp_path))
        assert stack["framework"] == "nextjs"
        assert stack["language"] == "typescript"

    def test_react_javascript(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"react": "^18.0.0"},
        }))

        stack = detect_project_stack(str(tmp_path))
        assert stack["framework"] == "react"
        assert stack["language"] == "javascript"

    def test_vue_project(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"vue": "^3.0.0"},
        }))

        stack = detect_project_stack(str(tmp_path))
        assert stack["framework"] == "vue"

    def test_express_project(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"express": "^4.18.0"},
        }))

        stack = detect_project_stack(str(tmp_path))
        assert stack["framework"] == "express"

    def test_python_fastapi(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("fastapi>=0.100.0\nuvicorn>=0.23.0\n")

        stack = detect_project_stack(str(tmp_path))
        assert stack["framework"] == "fastapi"
        assert stack["language"] == "python"

    def test_python_django_postgres(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("Django==4.2.0\npsycopg2-binary==2.9.0\n")

        stack = detect_project_stack(str(tmp_path))
        assert stack["framework"] == "django"
        assert stack["db"] == "postgres"

    def test_nextjs_with_prisma_nextauth(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {
                "next": "^14.0.0",
                "@prisma/client": "^5.0.0",
                "next-auth": "^4.0.0",
            },
        }))
        (tmp_path / "tsconfig.json").write_text("{}")

        stack = detect_project_stack(str(tmp_path))
        assert stack["framework"] == "nextjs"
        assert stack["db"] == "prisma"
        assert stack["auth"] == "next-auth"
        assert stack["language"] == "typescript"

    def test_nextjs_with_firebase(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"next": "^14.0.0", "firebase": "^10.0.0"},
        }))

        stack = detect_project_stack(str(tmp_path))
        assert stack["auth"] == "firebase"

    def test_supabase_project(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"react": "^18.0.0", "@supabase/supabase-js": "^2.0.0"},
        }))

        stack = detect_project_stack(str(tmp_path))
        assert stack["db"] == "supabase"

    def test_empty_project(self, tmp_path: Path) -> None:
        stack = detect_project_stack(str(tmp_path))
        assert stack["framework"] is None
        assert stack["language"] is None

    def test_pyproject_toml(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "myapp"\ndependencies = ["fastapi", "pymongo"]\n'
        )

        stack = detect_project_stack(str(tmp_path))
        assert stack["language"] == "python"
        assert stack["framework"] == "fastapi"
        assert stack["db"] == "mongodb"


# ===========================================================================
# get_template_module
# ===========================================================================

class TestGetTemplateModule:
    def test_all_types_registered(self) -> None:
        for wiring_type in [
            "api-hook", "auth-guard", "db-crud", "file-upload",
            "websocket", "sse-stream", "form-handler", "middleware",
        ]:
            mod = get_template_module(wiring_type)
            assert hasattr(mod, "generate")

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown wiring_type"):
            get_template_module("nonexistent")


# ===========================================================================
# api_hook template
# ===========================================================================

class TestApiHookTemplate:
    def test_react_default(self) -> None:
        result = api_hook.generate({"framework": "react"}, {})
        assert len(result["files"]) >= 1
        assert "useApi" in result["files"][0]["content"]
        assert result["dependencies_needed"] == []

    def test_react_streaming(self) -> None:
        result = api_hook.generate(
            {"framework": "react"},
            {"streaming": True},
        )
        assert "useApiStream" in result["files"][0]["content"]

    def test_nextjs_with_auth(self) -> None:
        result = api_hook.generate(
            {"framework": "nextjs"},
            {"auth_required": True, "endpoint": "/api/chat"},
        )
        content = result["files"][0]["content"]
        assert "Authorization" in content
        assert "/api/chat" in content

    def test_vue_composable(self) -> None:
        result = api_hook.generate({"framework": "vue"}, {})
        assert "composables/" in result["files"][0]["path"]
        assert "ref" in result["files"][0]["content"]

    def test_python_httpx(self) -> None:
        result = api_hook.generate({"language": "python"}, {})
        assert "httpx" in result["files"][0]["content"]
        assert "httpx" in result["dependencies_needed"]

    def test_python_streaming(self) -> None:
        result = api_hook.generate(
            {"language": "python"},
            {"streaming": True},
        )
        assert "aiter_text" in result["files"][0]["content"]

    def test_custom_endpoint_and_method(self) -> None:
        result = api_hook.generate(
            {"framework": "react"},
            {"endpoint": "/api/users", "method": "GET"},
        )
        content = result["files"][0]["content"]
        assert "/api/users" in content
        assert "GET" in content

    def test_unknown_stack_falls_back_to_react(self) -> None:
        result = api_hook.generate({}, {})
        assert "useState" in result["files"][0]["content"]


# ===========================================================================
# auth_guard template
# ===========================================================================

class TestAuthGuardTemplate:
    def test_nextauth_middleware(self) -> None:
        result = auth_guard.generate(
            {"framework": "nextjs", "auth": "next-auth"}, {},
        )
        content = result["files"][0]["content"]
        assert "getToken" in content
        assert result["files"][0]["path"] == "middleware.ts"

    def test_firebase_nextjs(self) -> None:
        result = auth_guard.generate(
            {"framework": "nextjs", "auth": "firebase"}, {},
        )
        assert len(result["files"]) == 2
        paths = [f["path"] for f in result["files"]]
        assert "hooks/useAuth.ts" in paths
        assert "components/ProtectedRoute.tsx" in paths

    def test_express_jwt(self) -> None:
        result = auth_guard.generate({"framework": "express"}, {})
        assert "jsonwebtoken" in result["dependencies_needed"]
        assert "jwt.verify" in result["files"][0]["content"]

    def test_python_fastapi(self) -> None:
        result = auth_guard.generate({"language": "python"}, {})
        assert "python-jose" in result["dependencies_needed"][0]
        assert "get_current_user" in result["files"][0]["content"]

    def test_generic_react_fallback(self) -> None:
        result = auth_guard.generate({}, {})
        assert "ProtectedRoute" in result["files"][0]["content"]


# ===========================================================================
# db_crud template
# ===========================================================================

class TestDbCrudTemplate:
    def test_firestore_hooks(self) -> None:
        result = db_crud.generate({"db": "firestore"}, {})
        content = result["files"][0]["content"]
        assert "useCollection" in content
        assert "useCrud" in content
        assert "firebase" in result["dependencies_needed"]

    def test_prisma_api_routes(self) -> None:
        result = db_crud.generate(
            {"db": "prisma"},
            {"collection": "posts", "model": "Post"},
        )
        assert len(result["files"]) == 2
        assert "prisma.post.findMany" in result["files"][0]["content"]

    def test_supabase_hooks(self) -> None:
        result = db_crud.generate({"db": "supabase"}, {"collection": "tasks"})
        content = result["files"][0]["content"]
        assert "useSupabaseQuery" in content
        assert "supabase" in content

    def test_mongoose_crud(self) -> None:
        result = db_crud.generate(
            {"db": "mongoose"},
            {"collection": "users", "model": "User"},
        )
        assert len(result["files"]) == 2
        paths = [f["path"] for f in result["files"]]
        assert "models/User.ts" in paths
        assert "controllers/UserController.ts" in paths

    def test_unknown_db_defaults_to_firestore(self) -> None:
        result = db_crud.generate({}, {})
        assert "firebase/firestore" in result["files"][0]["content"]


# ===========================================================================
# file_upload template
# ===========================================================================

class TestFileUploadTemplate:
    def test_firebase_storage(self) -> None:
        result = file_upload.generate({"storage": "firebase-storage"}, {})
        assert "uploadBytesResumable" in result["files"][0]["content"]

    def test_s3_presigned(self) -> None:
        result = file_upload.generate({"storage": "s3"}, {})
        assert len(result["files"]) == 2
        assert "@aws-sdk/client-s3" in result["dependencies_needed"]

    def test_multer_local(self) -> None:
        result = file_upload.generate({"framework": "express"}, {})
        assert "multer" in result["files"][0]["content"]

    def test_custom_max_size(self) -> None:
        result = file_upload.generate({}, {"max_size_mb": 50})
        assert "50" in result["files"][0]["content"]


# ===========================================================================
# websocket template
# ===========================================================================

class TestWebSocketTemplate:
    def test_react_hook(self) -> None:
        result = websocket.generate({"framework": "react"}, {})
        content = result["files"][0]["content"]
        assert "useWebSocket" in content
        assert "WebSocket" in content

    def test_node_server(self) -> None:
        result = websocket.generate({"framework": "express"}, {})
        assert "WebSocketServer" in result["files"][0]["content"]
        assert "ws" in result["dependencies_needed"]

    def test_custom_url(self) -> None:
        result = websocket.generate(
            {"framework": "react"},
            {"url": "wss://example.com/ws"},
        )
        assert "wss://example.com/ws" in result["files"][0]["content"]


# ===========================================================================
# sse_stream template
# ===========================================================================

class TestSSEStreamTemplate:
    def test_nextjs_full(self) -> None:
        result = sse_stream.generate({"framework": "nextjs"}, {})
        assert len(result["files"]) == 2
        paths = [f["path"] for f in result["files"]]
        assert any("route.ts" in p for p in paths)
        assert "hooks/useSSE.ts" in paths

    def test_react_client_only(self) -> None:
        result = sse_stream.generate({"framework": "react"}, {})
        assert len(result["files"]) == 1
        assert "useSSE" in result["files"][0]["content"]

    def test_python_fastapi(self) -> None:
        result = sse_stream.generate({"language": "python"}, {})
        assert "StreamingResponse" in result["files"][0]["content"]

    def test_custom_endpoint(self) -> None:
        result = sse_stream.generate(
            {"framework": "nextjs"},
            {"endpoint": "/api/stream"},
        )
        assert any("/api/stream" in f["path"] for f in result["files"])


# ===========================================================================
# form_handler template
# ===========================================================================

class TestFormHandlerTemplate:
    def test_react_hook_form(self) -> None:
        result = form_handler.generate({"framework": "react"}, {})
        assert len(result["files"]) == 2
        assert "react-hook-form" in result["dependencies_needed"]
        assert "zod" in result["dependencies_needed"]

    def test_custom_fields(self) -> None:
        fields = [
            {"name": "title", "type": "string", "required": True},
            {"name": "url", "type": "url", "required": False},
        ]
        result = form_handler.generate(
            {"framework": "react"},
            {"fields": fields},
        )
        schema_content = result["files"][0]["content"]
        assert "title" in schema_content
        assert "url" in schema_content

    def test_python_pydantic(self) -> None:
        result = form_handler.generate({"language": "python"}, {})
        assert "BaseModel" in result["files"][0]["content"]
        assert "fastapi" in result["dependencies_needed"]


# ===========================================================================
# middleware template
# ===========================================================================

class TestMiddlewareTemplate:
    def test_nextjs_middleware(self) -> None:
        result = middleware.generate({"framework": "nextjs"}, {})
        content = result["files"][0]["content"]
        assert "NextResponse" in content
        assert "X-Content-Type-Options" in content

    def test_express_middleware(self) -> None:
        result = middleware.generate({"framework": "express"}, {})
        assert "applyMiddleware" in result["files"][0]["content"]

    def test_fastify_plugins(self) -> None:
        result = middleware.generate({"framework": "fastify"}, {})
        assert "fastify" in result["dependencies_needed"]

    def test_python_fastapi(self) -> None:
        result = middleware.generate({"language": "python"}, {})
        assert "apply_middleware" in result["files"][0]["content"]

    def test_custom_features(self) -> None:
        result = middleware.generate(
            {"framework": "express"},
            {"features": ["cors", "logging"]},
        )
        content = result["files"][0]["content"]
        assert "cors" in content
        assert "morgan" in content

    def test_rate_limit_feature(self) -> None:
        result = middleware.generate(
            {"framework": "express"},
            {"features": ["rate-limit"]},
        )
        assert "express-rate-limit" in result["dependencies_needed"]


# ===========================================================================
# handle_generate_wiring (integration-level)
# ===========================================================================

class TestHandleGenerateWiring:
    @pytest.mark.asyncio
    async def test_full_pipeline(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"next": "^14.0.0", "react": "^18.0.0"},
        }))
        (tmp_path / "tsconfig.json").write_text("{}")

        result = await handle_generate_wiring({
            "project_dir": str(tmp_path),
            "wiring_type": "api-hook",
            "config": {"endpoint": "/api/test", "method": "GET"},
        })

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["wiring_type"] == "api-hook"
        assert data["stack_detected"]["framework"] == "nextjs"
        assert len(data["files"]) >= 1
        assert "/api/test" in data["files"][0]["content"]

    @pytest.mark.asyncio
    async def test_missing_project_dir(self) -> None:
        with pytest.raises(ValueError, match="project_dir"):
            await handle_generate_wiring({
                "project_dir": "",
                "wiring_type": "api-hook",
            })

    @pytest.mark.asyncio
    async def test_missing_wiring_type(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="wiring_type"):
            await handle_generate_wiring({
                "project_dir": str(tmp_path),
                "wiring_type": "",
            })

    @pytest.mark.asyncio
    async def test_invalid_wiring_type(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Unknown wiring_type"):
            await handle_generate_wiring({
                "project_dir": str(tmp_path),
                "wiring_type": "nonexistent",
            })

    @pytest.mark.asyncio
    async def test_nonexistent_project_dir(self) -> None:
        with pytest.raises(ValueError, match="존재하지 않습니다"):
            await handle_generate_wiring({
                "project_dir": "/nonexistent/path/abc123",
                "wiring_type": "api-hook",
            })

    @pytest.mark.asyncio
    async def test_all_wiring_types(self, tmp_path: Path) -> None:
        """Smoke test: every wiring type generates without error."""
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"react": "^18.0.0"},
        }))

        for wiring_type in [
            "api-hook", "auth-guard", "db-crud", "file-upload",
            "websocket", "sse-stream", "form-handler", "middleware",
        ]:
            result = await handle_generate_wiring({
                "project_dir": str(tmp_path),
                "wiring_type": wiring_type,
            })
            data = json.loads(result[0].text)
            assert data["wiring_type"] == wiring_type
            assert len(data["files"]) >= 1


# ===========================================================================
# Tool schema validation
# ===========================================================================

class TestToolSchema:
    def test_tool_name(self) -> None:
        assert GENERATE_WIRING_TOOL.name == "generate_wiring"

    def test_required_fields(self) -> None:
        schema = GENERATE_WIRING_TOOL.inputSchema
        assert "project_dir" in schema["required"]
        assert "wiring_type" in schema["required"]

    def test_wiring_type_enum(self) -> None:
        schema = GENERATE_WIRING_TOOL.inputSchema
        enum_values = schema["properties"]["wiring_type"]["enum"]
        assert len(enum_values) == 8
        assert "api-hook" in enum_values
        assert "middleware" in enum_values

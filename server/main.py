"""OSS Scout MCP Server — Entry point.

Supports two transport modes:
  - stdio (default): For local `claude mcp add` integration
  - http: For Cloud Run deployment via HTTP/SSE

Architecture: Sub-agent based analysis (no LLM dependency).
Claude Code acts as the LLM layer via MCP Prompts.
"""

import asyncio
import json
import logging
import os
import sys

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    TextContent,
    Tool,
)

from server.core.github_client import GitHubClient, parse_repo_url
from server.tools.batch import (
    BATCH_SCAFFOLD_TOOL,
    BATCH_SEARCH_TOOL,
    BATCH_VALIDATE_TOOL,
    handle_batch_scaffold,
    handle_batch_search,
    handle_batch_validate,
)
from server.tools.adapt_stack import ADAPT_STACK_TOOL, handle_adapt_stack
from server.tools.envcheck import ENVCHECK_TOOL, handle_envcheck
from server.tools.explain import EXPLAIN_TOOL, handle_explain
from server.tools.extract_component import (
    EXTRACT_COMPONENT_TOOL,
    handle_extract_component,
)
from server.tools.integration_check import (
    VALIDATE_INTEGRATION_TOOL,
    handle_validate_integration,
)
from server.tools.merge_repos import MERGE_REPOS_TOOL, handle_merge_repos
from server.tools.preview import PREVIEW_TOOL, handle_preview
from server.tools.license import LICENSE_TOOL, handle_license
from server.tools.recipe import RECIPE_TOOL, handle_recipe
from server.tools.scaffold import SCAFFOLD_TOOL, handle_scaffold
from server.tools.search import SEARCH_TOOL, handle_search
from server.tools.smart_scaffold import SMART_SCAFFOLD_TOOL, handle_smart_scaffold
from server.tools.validate import VALIDATE_TOOL, handle_validate
from server.tools.wiring import GENERATE_WIRING_TOOL, handle_generate_wiring

load_dotenv()

# Structured logging (JSON for Cloud Run compatibility)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("oss-scout")


def _log(level: str, event: str, **kwargs):
    """Emit a structured JSON log line."""
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(json.dumps(entry, ensure_ascii=False))


# --- GitHub Client (initialized once) ----------------------------------------

_github_client: GitHubClient | None = None


def _get_github_client() -> GitHubClient:
    """Lazily initialize the GitHub client."""
    global _github_client  # noqa: PLW0603
    if _github_client is None:
        token = os.getenv("GITHUB_TOKEN", "")
        if not token:
            _log("warning", "github_token_missing",
                 msg="GITHUB_TOKEN not set; API rate limits will be restricted")
        _github_client = GitHubClient(token=token or None)
    return _github_client


# --- MCP Server -----------------------------------------------------------

app = Server("oss-scout")


# --- Tools ----------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="hello",
            description="Health-check tool. Returns a greeting to confirm the server is running.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name to greet",
                    },
                },
                "required": ["name"],
            },
        ),
        SEARCH_TOOL,
        LICENSE_TOOL,
        VALIDATE_TOOL,
        EXPLAIN_TOOL,
        SCAFFOLD_TOOL,
        BATCH_SEARCH_TOOL,
        BATCH_VALIDATE_TOOL,
        BATCH_SCAFFOLD_TOOL,
        PREVIEW_TOOL,
        ENVCHECK_TOOL,
        SMART_SCAFFOLD_TOOL,
        RECIPE_TOOL,
        VALIDATE_INTEGRATION_TOOL,
        EXTRACT_COMPONENT_TOOL,
        ADAPT_STACK_TOOL,
        MERGE_REPOS_TOOL,
        GENERATE_WIRING_TOOL,
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "hello":
        user_name = arguments.get("name", "World")
        _log("info", "tool_called", tool="hello", name=user_name)
        return [
            TextContent(
                type="text",
                text=f"Hello, {user_name}! OSS Scout is ready.",
            )
        ]

    if name == "search_boilerplate":
        _log("info", "tool_called", tool="search_boilerplate")
        try:
            github = _get_github_client()
            return await handle_search(arguments, github)
        except ValueError as e:
            return [TextContent(type="text", text=json.dumps(
                {"error": str(e)}, ensure_ascii=False,
            ))]
        except Exception as e:
            _log("error", "search_failed", error=str(e)[:200])
            return [TextContent(type="text", text=json.dumps(
                {"error": "검색 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."},
                ensure_ascii=False,
            ))]

    if name == "check_license":
        _log("info", "tool_called", tool="check_license")
        try:
            github = _get_github_client()
            return await handle_license(arguments, github)
        except ValueError as e:
            return [TextContent(type="text", text=json.dumps(
                {"error": str(e)}, ensure_ascii=False,
            ))]
        except Exception as e:
            _log("error", "license_check_failed", error=str(e)[:200])
            return [TextContent(type="text", text=json.dumps(
                {"error": "라이선스 확인 중 오류가 발생했습니다."},
                ensure_ascii=False,
            ))]

    if name == "validate_repo":
        _log("info", "tool_called", tool="validate_repo")
        try:
            github = _get_github_client()
            return await handle_validate(arguments, github)
        except ValueError as e:
            return [TextContent(type="text", text=json.dumps(
                {"error": str(e)}, ensure_ascii=False,
            ))]
        except Exception as e:
            _log("error", "validate_failed", error=str(e)[:200])
            return [TextContent(type="text", text=json.dumps(
                {"error": "레포 검증 중 오류가 발생했습니다."},
                ensure_ascii=False,
            ))]

    if name == "explain_repo":
        _log("info", "tool_called", tool="explain_repo")
        try:
            github = _get_github_client()
            return await handle_explain(arguments, github)
        except ValueError as e:
            return [TextContent(type="text", text=json.dumps(
                {"error": str(e)}, ensure_ascii=False,
            ))]
        except Exception as e:
            _log("error", "explain_failed", error=str(e)[:200])
            return [TextContent(type="text", text=json.dumps(
                {"error": "레포 분석 중 오류가 발생했습니다."},
                ensure_ascii=False,
            ))]

    if name == "scaffold":
        _log("info", "tool_called", tool="scaffold")
        try:
            github = _get_github_client()
            return await handle_scaffold(arguments, github)
        except ValueError as e:
            return [TextContent(type="text", text=json.dumps(
                {"error": str(e)}, ensure_ascii=False,
            ))]
        except Exception as e:
            _log("error", "scaffold_failed", error=str(e)[:200])
            return [TextContent(type="text", text=json.dumps(
                {"error": "스캐폴딩 중 오류가 발생했습니다."},
                ensure_ascii=False,
            ))]

    if name == "batch_search":
        _log("info", "tool_called", tool="batch_search")
        try:
            github = _get_github_client()
            return await handle_batch_search(arguments, github)
        except Exception as e:
            _log("error", "batch_search_failed", error=str(e)[:200])
            return [TextContent(type="text", text=json.dumps(
                {"error": "배치 검색 중 오류가 발생했습니다."},
                ensure_ascii=False,
            ))]

    if name == "batch_validate":
        _log("info", "tool_called", tool="batch_validate")
        try:
            github = _get_github_client()
            return await handle_batch_validate(arguments, github)
        except Exception as e:
            _log("error", "batch_validate_failed", error=str(e)[:200])
            return [TextContent(type="text", text=json.dumps(
                {"error": "배치 검증 중 오류가 발생했습니다."},
                ensure_ascii=False,
            ))]

    if name == "batch_scaffold":
        _log("info", "tool_called", tool="batch_scaffold")
        try:
            github = _get_github_client()
            return await handle_batch_scaffold(arguments, github)
        except Exception as e:
            _log("error", "batch_scaffold_failed", error=str(e)[:200])
            return [TextContent(type="text", text=json.dumps(
                {"error": "배치 스캐폴딩 중 오류가 발생했습니다."},
                ensure_ascii=False,
            ))]

    if name == "check_env":
        _log("info", "tool_called", tool="check_env")
        try:
            github = _get_github_client()
            return await handle_envcheck(arguments, github)
        except ValueError as e:
            return [TextContent(type="text", text=json.dumps(
                {"error": str(e)}, ensure_ascii=False,
            ))]
        except Exception as e:
            _log("error", "envcheck_failed", error=str(e)[:200])
            return [TextContent(type="text", text=json.dumps(
                {"error": "환경변수 분석 중 오류가 발생했습니다."},
                ensure_ascii=False,
            ))]

    if name == "preview":
        _log("info", "tool_called", tool="preview")
        try:
            return await handle_preview(arguments)
        except ValueError as e:
            return [TextContent(type="text", text=json.dumps(
                {"error": str(e)}, ensure_ascii=False,
            ))]
        except Exception as e:
            _log("error", "preview_failed", error=str(e)[:200])
            return [TextContent(type="text", text=json.dumps(
                {"error": "프리뷰 감지 중 오류가 발생했습니다."},
                ensure_ascii=False,
            ))]

    if name == "recipe":
        _log("info", "tool_called", tool="recipe")
        try:
            github = _get_github_client()
            return await handle_recipe(arguments, github)
        except ValueError as e:
            return [TextContent(type="text", text=json.dumps(
                {"error": str(e)}, ensure_ascii=False,
            ))]
        except Exception as e:
            _log("error", "recipe_failed", error=str(e)[:200])
            return [TextContent(type="text", text=json.dumps(
                {"error": "레시피 처리 중 오류가 발생했습니다."},
                ensure_ascii=False,
            ))]

    if name == "smart_scaffold":
        _log("info", "tool_called", tool="smart_scaffold")
        try:
            github = _get_github_client()
            return await handle_smart_scaffold(arguments, github)
        except ValueError as e:
            return [TextContent(type="text", text=json.dumps(
                {"error": str(e)}, ensure_ascii=False,
            ))]
        except Exception as e:
            _log("error", "smart_scaffold_failed", error=str(e)[:200])
            return [TextContent(type="text", text=json.dumps(
                {"error": "스마트 스캐폴딩 중 오류가 발생했습니다."},
                ensure_ascii=False,
            ))]

    if name == "validate_integration":
        _log("info", "tool_called", tool="validate_integration")
        try:
            return await handle_validate_integration(arguments)
        except ValueError as e:
            return [TextContent(type="text", text=json.dumps(
                {"error": str(e)}, ensure_ascii=False,
            ))]
        except Exception as e:
            _log("error", "validate_integration_failed", error=str(e)[:200])
            return [TextContent(type="text", text=json.dumps(
                {"error": "통합 검증 중 오류가 발생했습니다."},
                ensure_ascii=False,
            ))]

    if name == "extract_component":
        _log("info", "tool_called", tool="extract_component")
        try:
            github = _get_github_client()
            return await handle_extract_component(arguments, github)
        except ValueError as e:
            return [TextContent(type="text", text=json.dumps(
                {"error": str(e)}, ensure_ascii=False,
            ))]
        except Exception as e:
            _log("error", "extract_component_failed", error=str(e)[:200])
            return [TextContent(type="text", text=json.dumps(
                {"error": "컴포넌트 추출 중 오류가 발생했습니다."},
                ensure_ascii=False,
            ))]

    if name == "adapt_stack":
        _log("info", "tool_called", tool="adapt_stack")
        try:
            return await handle_adapt_stack(arguments)
        except ValueError as e:
            return [TextContent(type="text", text=json.dumps(
                {"error": str(e)}, ensure_ascii=False,
            ))]
        except Exception as e:
            _log("error", "adapt_stack_failed", error=str(e)[:200])
            return [TextContent(type="text", text=json.dumps(
                {"error": "스택 변환 분석 중 오류가 발생했습니다."},
                ensure_ascii=False,
            ))]

    if name == "merge_repos":
        _log("info", "tool_called", tool="merge_repos")
        try:
            github = _get_github_client()
            return await handle_merge_repos(arguments, github)
        except ValueError as e:
            return [TextContent(type="text", text=json.dumps(
                {"error": str(e)}, ensure_ascii=False,
            ))]
        except Exception as e:
            _log("error", "merge_repos_failed", error=str(e)[:200])
            return [TextContent(type="text", text=json.dumps(
                {"error": "레포 머지 중 오류가 발생했습니다."},
                ensure_ascii=False,
            ))]

    if name == "generate_wiring":
        _log("info", "tool_called", tool="generate_wiring")
        try:
            return await handle_generate_wiring(arguments)
        except ValueError as e:
            return [TextContent(type="text", text=json.dumps(
                {"error": str(e)}, ensure_ascii=False,
            ))]
        except Exception as e:
            _log("error", "generate_wiring_failed", error=str(e)[:200])
            return [TextContent(type="text", text=json.dumps(
                {"error": "연결 코드 생성 중 오류가 발생했습니다."},
                ensure_ascii=False,
            ))]

    raise ValueError(f"Unknown tool: {name}")


# --- MCP Prompts -----------------------------------------------------------

@app.list_prompts()
async def list_prompts() -> list[Prompt]:
    return [
        Prompt(
            name="analyze_candidates",
            description="검색 결과 후보들을 분석하고 최적 선택을 추천합니다",
            arguments=[
                PromptArgument(
                    name="query",
                    description="원래 검색 쿼리",
                    required=True,
                ),
                PromptArgument(
                    name="results",
                    description="search_boilerplate 결과 JSON",
                    required=True,
                ),
            ],
        ),
        Prompt(
            name="evaluate_repo",
            description="특정 레포의 적합성을 심층 평가합니다",
            arguments=[
                PromptArgument(
                    name="repo_url",
                    description="GitHub 레포 URL",
                    required=True,
                ),
                PromptArgument(
                    name="purpose",
                    description="프로젝트 목적/요구사항",
                    required=True,
                ),
            ],
        ),
    ]


@app.get_prompt()
async def get_prompt(name: str, arguments: dict | None) -> GetPromptResult:
    arguments = arguments or {}

    if name == "analyze_candidates":
        query = arguments.get("query", "")
        results = arguments.get("results", "[]")
        return GetPromptResult(
            description="검색 결과 후보 분석 및 최적 선택 추천",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=f"""다음은 "{query}" 검색 결과입니다. 각 후보를 분석하고 최적의 선택을 추천해주세요.

## 검색 결과
{results}

## 분석 요청사항

1. **각 후보 평가**: quality_score, license, stars, 최근 커밋 날짜를 기반으로 장단점을 정리해주세요.
2. **에이전트 검증 결과 해석**: agents 필드가 있다면 각 에이전트(license, quality, security, compatibility)의 findings와 warnings를 해석해주세요.
3. **추천**: 사용자의 검색 의도("{query}")에 가장 적합한 후보 1~2개를 추천하고 이유를 설명해주세요.
4. **주의사항**: 라이선스 경고, 보안 이슈, 호환성 문제가 있다면 반드시 언급해주세요.
5. **다음 단계**: 선택 후 `scaffold` 명령으로 프로젝트를 생성하는 방법을 안내해주세요.

한국어로 답변해주세요.""",
                    ),
                ),
            ],
        )

    if name == "evaluate_repo":
        repo_url = arguments.get("repo_url", "")
        purpose = arguments.get("purpose", "")
        return GetPromptResult(
            description="레포 적합성 심층 평가",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=f"""다음 레포의 적합성을 심층 평가해주세요.

## 대상 레포
{repo_url}

## 프로젝트 목적
{purpose}

## 평가 요청사항

1. **기술 스택 분석**: 이 레포가 사용하는 기술 스택이 목적에 적합한지 평가해주세요.
2. **구조 분석**: 프로젝트 구조가 잘 설계되었는지, 확장 가능한지 평가해주세요.
3. **라이선스**: 상업적 사용이 가능한지, 주의사항이 있는지 확인해주세요.
4. **품질**: 테스트, CI/CD, 문서화 수준을 평가해주세요.
5. **보안**: 알려진 보안 이슈나 우려사항이 있는지 확인해주세요.

## scaffold 전 체크리스트
- [ ] 라이선스가 프로젝트 요구사항과 호환되는가?
- [ ] 최근 6개월 내 활발한 유지보수가 되고 있는가?
- [ ] 의존성에 알려진 취약점이 없는가?
- [ ] 프로젝트 구조가 목적에 맞게 커스터마이즈 가능한가?
- [ ] README에 충분한 설치/사용 가이드가 있는가?

`validate_repo` 툴을 먼저 실행하여 자동 검증 결과를 확인한 후, 위 항목들을 종합적으로 평가해주세요.
한국어로 답변해주세요.""",
                    ),
                ),
            ],
        )

    raise ValueError(f"Unknown prompt: {name}")


# --- Transport -------------------------------------------------------------

async def run_stdio():
    """Run the server over stdio transport."""
    _log("info", "server_starting", transport="stdio")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


async def run_http(port: int):
    """Run the server over HTTP/SSE transport."""
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route
    import uvicorn

    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await app.run(
                streams[0], streams[1], app.create_initialization_options()
            )

    starlette_app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )

    _log("info", "server_starting", transport="http", port=port)

    config = uvicorn.Config(starlette_app, host="0.0.0.0", port=port)
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    transport = os.getenv("MCP_TRANSPORT", "stdio")

    if transport == "stdio":
        await run_stdio()
    elif transport == "http":
        try:
            port = int(os.getenv("MCP_PORT", "8080"))
        except ValueError:
            _log("error", "invalid_port", port=os.getenv("MCP_PORT"))
            sys.exit(1)
        await run_http(port)
    else:
        _log("error", "invalid_transport", transport=transport)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

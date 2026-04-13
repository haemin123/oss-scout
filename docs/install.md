# OSS Scout Installation Guide

## Quick Start (30 seconds)

```bash
claude mcp add oss-scout -- uvx --from git+https://github.com/haemin123/oss-scout python -m server.main
```

That's it. You can now use OSS Scout tools in Claude Code.

## Local Installation (pip)

```bash
git clone https://github.com/haemin123/oss-scout.git
cd oss-scout
pip install -e .
claude mcp add oss-scout -- python -m server.main
```

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GITHUB_TOKEN` | Recommended | -- | GitHub Personal Access Token. Without it, API rate limits are severely restricted (60 req/hour vs 5,000). |
| `CACHE_DIR` | No | `~/.oss-scout/cache` | SQLite cache directory |
| `CACHE_TTL_HOURS` | No | `24` | Cache time-to-live in hours |

### Setting GITHUB_TOKEN

1. Go to [GitHub Settings > Developer settings > Personal access tokens](https://github.com/settings/tokens)
2. Generate a new token (classic) with `public_repo` scope
3. Set the environment variable:

```bash
# Option 1: In .env file (local dev)
echo "GITHUB_TOKEN=YOUR_GITHUB_TOKEN" > .env

# Option 2: System environment
export GITHUB_TOKEN=YOUR_GITHUB_TOKEN
```

## Available MCP Tools (6)

| Tool | Description |
|---|---|
| `search_boilerplate` | 자연어 쿼리로 GitHub 보일러플레이트를 검색합니다. 라이선스 필터링, 품질 스코어링, 4종 서브 에이전트 검증을 포함합니다. |
| `explain_repo` | 레포의 기술 스택, 디렉토리 구조, 설치 방법, 주의사항을 분석합니다. |
| `scaffold` | 레포를 대상 디렉토리에 복사합니다 (git history 제외). LICENSE 파일을 보존하고 CLAUDE.md를 자동 생성합니다. |
| `check_license` | 레포의 라이선스를 판정합니다. GitHub API와 LICENSE 파일 내용을 교차 검증합니다. |
| `validate_repo` | 4종 서브 에이전트(라이선스, 품질, 보안, 호환성)로 레포를 종합 검증합니다. |
| `hello` | 서버 상태 확인용 헬스체크 도구입니다. |

## MCP Prompts (2)

MCP Prompts는 Claude Code가 분석 결과를 해석할 때 사용하는 프롬프트 템플릿입니다.

| Prompt | Description |
|---|---|
| `analyze_candidates` | 검색 결과 후보들을 비교 분석하고 최적의 선택을 추천합니다. |
| `evaluate_repo` | 특정 레포의 적합성을 프로젝트 목적에 맞춰 심층 평가합니다. |

## Usage Examples

Claude Code에서 자연어로 요청하면 됩니다:

```
"Next.js Supabase 대시보드 보일러플레이트 찾아줘"

"vercel/next.js 레포 분석해줘"

"이 레포를 ./my-project에 스캐폴딩해줘"

"fastapi-users/fastapi-users 라이선스 확인해줘"

"shadcn/ui 레포 종합 검증해줘"
```

## Codex Integration

### Option 1: CLI flag

```bash
codex --mcp-config .mcp.json
```

### Option 2: .mcp.json file

프로젝트 루트에 `.mcp.json` 파일을 생성합니다:

```json
{
  "mcpServers": {
    "oss-scout": {
      "command": "python",
      "args": ["-m", "server.main"],
      "cwd": "/path/to/oss-scout",
      "env": {
        "GITHUB_TOKEN": "${GITHUB_TOKEN}"
      }
    }
  }
}
```

### Option 3: uvx (no clone needed)

```json
{
  "mcpServers": {
    "oss-scout": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/haemin123/oss-scout",
        "python", "-m", "server.main"
      ],
      "env": {
        "GITHUB_TOKEN": "${GITHUB_TOKEN}"
      }
    }
  }
}
```

## Troubleshooting

### GITHUB_TOKEN 없이 사용하면?

서버는 정상 시작되지만 GitHub API rate limit이 시간당 60회로 제한됩니다. 경고 로그가 출력됩니다. 검색 결과가 제한적일 수 있으므로 토큰 설정을 권장합니다.

### Windows에서 동작하나?

네. Python 3.11+ 와 pip이 설치되어 있으면 Windows, macOS, Linux 모두 지원합니다.

### Python 3.11 미만이면?

`requires-python = ">=3.11"` 이므로 설치 시 에러가 발생합니다. Python 3.11 이상을 설치해주세요.

### MCP 서버 연결 확인

```bash
claude mcp list
```

`oss-scout` 서버가 목록에 나타나고 6개 툴이 등록되어 있으면 정상입니다.

### 캐시 초기화

SQLite 캐시를 초기화하려면:

```bash
rm -rf ~/.oss-scout/cache
```

### 로그 확인

서버 로그는 stderr로 출력됩니다. 디버깅 시:

```bash
LOG_LEVEL=DEBUG claude mcp add oss-scout -- python -m server.main
```

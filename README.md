# OSS Scout

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-104%20passed-brightgreen.svg)](#testing)

GitHub에서 라이선스 검증된 고품질 오픈소스 보일러플레이트를 찾아 Claude Code / Codex에서 바로 스캐폴딩해주는 MCP 서버.

## Quick Start

```bash
claude mcp add oss-scout -- uvx --from git+https://github.com/haemin123/oss-scout python -m server.main
```

Or install locally:

```bash
git clone https://github.com/haemin123/oss-scout.git
cd oss-scout
pip install -e .
claude mcp add oss-scout -- python -m server.main
```

Set up your GitHub token for better rate limits:

```bash
export GITHUB_TOKEN=ghp_your_token_here
```

See [docs/install.md](docs/install.md) for the full installation guide, Codex integration, and troubleshooting.

## Tech Stack

| Item | Choice |
|------|--------|
| Language | Python 3.11+ |
| Framework | MCP Python SDK (`mcp`) |
| Transport | stdio (default) + HTTP/SSE |
| Analysis | Rule-based sub-agents (4 types) |
| LLM Judgment | MCP Prompts (Claude Code as LLM) |
| GitHub Client | PyGithub |
| Cache | SQLite (local, TTL 24h) |
| Scaffolding | tarball download + extraction |

## MCP Tools (6)

| Tool | Description |
|------|-------------|
| `search_boilerplate` | 자연어 쿼리로 GitHub 보일러플레이트 검색. 라이선스 필터링 + 품질 스코어링 + 서브 에이전트 검증 포함. |
| `explain_repo` | 레포의 기술 스택, 구조, 설치 방법, 주의사항 분석. |
| `scaffold` | 레포를 대상 디렉토리에 복사 (git history 제외). LICENSE 보존 + CLAUDE.md 자동 생성. |
| `check_license` | 라이선스 판정. GitHub API + LICENSE 파일 교차 검증. |
| `validate_repo` | 4종 서브 에이전트로 레포 종합 검증 (라이선스, 품질, 보안, 호환성). |
| `hello` | 서버 헬스체크. |

## MCP Prompts (2)

| Prompt | Description |
|--------|-------------|
| `analyze_candidates` | 검색 결과 후보 비교 분석 및 최적 선택 추천. |
| `evaluate_repo` | 특정 레포의 프로젝트 목적 적합성 심층 평가. |

## Sub-agents (4)

검색 및 검증 시 자동 실행되는 규칙 기반 분석 에이전트:

| Agent | Role |
|-------|------|
| **License Agent** | GitHub API 라이선스 필드와 LICENSE 파일 내용을 교차 검증. 불일치 감지. |
| **Quality Agent** | README 품질, 테스트/CI 존재 여부, 이슈 건강도 분석. |
| **Security Agent** | 위험 파일(.env, .pem, credentials.json) 감지, 아카이브 경고, 의존성 수 이상 탐지. |
| **Compatibility Agent** | Node/Python 버전 요구사항, Docker 베이스 이미지, 네이티브 빌드 도구 필요 여부 확인. |

모든 에이전트는 LLM 호출 없이 순수 규칙 기반으로 동작합니다.

## Usage Examples

Claude Code에서 자연어로 요청:

```
"Next.js Supabase 대시보드 보일러플레이트 찾아줘"

"vercel/next.js 레포 분석해줘"

"이 레포를 ./my-project에 스캐폴딩해줘"

"shadcn/ui 레포 종합 검증해줘"
```

## Testing

```bash
pip install -e ".[dev]"
pytest tests/           # Run all tests
pytest --cov=server tests/  # With coverage
```

```
104 tests passed (agents: 22, license: 26, scoring: 27, scaffold: 29)
```

## Development

```bash
ruff check .            # Lint
ruff format .           # Format
mypy server/            # Type check
```

## License

MIT

# OSS Scout MCP Server

MCP server that finds license/quality-verified open-source boilerplates from GitHub and scaffolds them directly inside Claude Code / Codex.

## Tech Stack

| Item | Choice |
|------|--------|
| Language | Python 3.11+ |
| Framework | MCP Python SDK (`mcp`) |
| Transport | HTTP/SSE (stdio supported) |
| Runtime | Local / Cloud Run (optional) |
| Cache | SQLite (local, TTL 24h) |
| Analysis | Rule-based sub-agents (4 types) |
| LLM Judgment | MCP Prompts (Claude Code as LLM) |
| GitHub Client | PyGithub |
| Scaffolding | tarball download + extraction |
| Secrets | Environment variables (`.env`) |
| Test | pytest + pytest-asyncio |
| Lint/Format | ruff + mypy (strict mode) |

## Directory Structure

```
oss-scout/
├── server/
│   ├── main.py              # MCP server entrypoint (Tools + Prompts)
│   ├── models.py            # Pydantic models
│   ├── tools/               # MCP tool handlers
│   │   ├── search.py        # search_boilerplate
│   │   ├── explain.py       # explain_repo
│   │   ├── scaffold.py      # scaffold
│   │   ├── license.py       # check_license
│   │   ├── validate.py      # validate_repo (sub-agent orchestration)
│   │   └── batch.py         # batch_search, batch_validate, batch_scaffold (parallel)
│   ├── agents/              # Rule-based sub-agents
│   │   ├── base.py          # BaseAgent, AgentResult
│   │   ├── license_agent.py # License cross-validation
│   │   ├── quality_agent.py # Code quality analysis
│   │   ├── security_agent.py    # Security risk detection
│   │   └── compatibility_agent.py  # Compatibility check
│   └── core/                # Business logic
│       ├── github_client.py # GitHub API wrapper (rate limit + cache)
│       ├── scoring.py       # Quality scoring engine
│       ├── license_check.py # License policy enforcement
│       └── local_cache.py   # SQLite local cache
├── config/
│   └── license_policy.yaml  # License whitelist/warn/block
├── tests/
└── docs/
```

## Coding Conventions

- All code formatted with `ruff` (line length 100)
- Type-checked with `mypy --strict`
- All public functions must have type annotations
- All I/O-bound operations must be `async/await`
- CPU-bound pure computations (e.g., scoring) may be synchronous
- Pydantic models for all MCP tool inputs and outputs
- Imports sorted by ruff (isort-compatible)

## Commit Convention

Conventional Commits format:
- `feat:` new feature
- `fix:` bug fix
- `chore:` maintenance (deps, config)
- `test:` test additions/changes
- `docs:` documentation only
- `refactor:` code restructuring without behavior change

## Security Rules (MANDATORY)

1. **Never hardcode secrets** (API keys, tokens, passwords) in source code
2. **Secret loading**: environment variables or `.env` file (local dev)
3. **Path traversal prevention**: `target_dir` must be validated with `Path.is_relative_to(cwd)` -- never use `str.startswith()`
4. **Tarball extraction safety**: reject symlinks, absolute paths, `..` segments; limit file count (10K) and size (100MB)
5. **repo_url validation**: only `https://github.com/{owner}/{repo}` format allowed
6. **GitHub search query sanitization**: strip GitHub search operators from user input
7. **Log masking**: never log tokens, keys, or credentials -- use automatic masking patterns
8. **Error responses**: return user-friendly Korean messages; never expose stack traces or internal paths
9. **LICENSE file preservation**: always preserve the original LICENSE file during scaffold

## Test Rules

- Unit tests required for: `scoring`, `license_check`, `search`, `agents` modules
- GitHub API calls must be mocked (`pytest-mock`)
- Integration tests marked with `@pytest.mark.integration`
- Coverage target: 90%+ for core logic (scoring, license_check, agents)
- Run tests: `pytest tests/`
- Run with coverage: `pytest --cov=server tests/`

## Running the MCP Server

```bash
# Local (stdio mode)
MCP_TRANSPORT=stdio python -m server.main

# Local (HTTP mode)
MCP_TRANSPORT=http MCP_PORT=8080 python -m server.main

# Add to Claude Code
claude mcp add oss-scout -- python -m server.main
```

## MCP Prompts

Two MCP Prompts are available for Claude Code to use:
- `analyze_candidates`: Analyze search results and recommend the best option
- `evaluate_repo`: Deep evaluation of a specific repo for a given purpose

## Ambiguity Protocol

If a requirement is ambiguous, **stop and ask the user** before implementing. Do not guess.

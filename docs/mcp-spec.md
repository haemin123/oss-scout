# OSS Scout MCP Server - Protocol & API Specification

> Version: 1.0.0-draft  
> Last updated: 2026-04-13  
> Source of truth: `ossmaker.md`

---

## Table of Contents

1. [MCP Server Configuration](#1-mcp-server-configuration)
2. [MCP Tool Specifications](#2-mcp-tool-specifications)
3. [Pydantic Model Design](#3-pydantic-model-design)
4. [LLM Integration Design](#4-llm-integration-design)
5. [GitHub Client Design](#5-github-client-design)
6. [Installation & Integration Guide](#6-installation--integration-guide)

---

## 1. MCP Server Configuration

### 1.1 Server Entry Point (`server/main.py`)

The server initializes using the official MCP Python SDK (`mcp`). It supports two transport modes: **HTTP/SSE** (default for Cloud Run) and **stdio** (for local Claude Code integration).

#### Initialization Flow

```
1. Load environment variables (.env / Secret Manager)
2. Initialize GitHub client (PyGithub + token validation)
3. Initialize Firestore cache client
4. Initialize Vertex AI LLM client
5. Register MCP tools (4 tools)
6. Start transport (HTTP/SSE or stdio based on MCP_TRANSPORT)
```

#### Entry Point Code Structure

```python
# server/main.py
import asyncio
import os
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route, Mount

from server.tools.search import register as register_search
from server.tools.explain import register as register_explain
from server.tools.scaffold import register as register_scaffold
from server.tools.license import register as register_license

app = Server("oss-scout")

# Register all tools
register_search(app)
register_explain(app)
register_scaffold(app)
register_license(app)

async def main():
    transport = os.getenv("MCP_TRANSPORT", "stdio")

    if transport == "stdio":
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())
    elif transport == "http":
        port = int(os.getenv("MCP_PORT", "8080"))
        sse = SseServerTransport("/messages/")
        starlette_app = Starlette(
            routes=[
                Route("/sse", endpoint=sse.handle_sse_connection),
                Mount("/messages/", app=sse.handle_post_message),
            ]
        )
        import uvicorn
        uvicorn.run(starlette_app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    asyncio.run(main())
```

### 1.2 HTTP/SSE Transport Mode

- **Default for production** (Cloud Run deployment)
- SSE endpoint: `GET /sse` -- establishes Server-Sent Events connection
- Message endpoint: `POST /messages/` -- receives MCP JSON-RPC messages
- Port: `MCP_PORT` env var (default `8080`)
- Cloud Run URL format: `https://oss-scout-<hash>-<region>.a.run.app`

### 1.3 stdio Transport Mode

- **Default for local development** and `claude mcp add` integration
- Reads JSON-RPC from stdin, writes to stdout
- No network configuration required
- Activated when `MCP_TRANSPORT=stdio`

### 1.4 Environment Variable Loading Strategy

```
Priority order (highest to lowest):
1. OS environment variables (runtime override)
2. GCP Secret Manager (production secrets: GITHUB_TOKEN)
3. .env file (local development only)
```

**Required Variables:**

| Variable | Required | Default | Description |
|---|---|---|---|
| `GITHUB_TOKEN` | Yes | -- | GitHub Personal Access Token |
| `GCP_PROJECT_ID` | Yes* | -- | GCP project ID (*not needed for stdio-only mode) |
| `GCP_REGION` | No | `asia-northeast3` | GCP region |
| `FIRESTORE_COLLECTION` | No | `oss_scout_cache` | Firestore collection name |
| `VERTEX_MODEL` | No | `gemini-2.0-flash` | Vertex AI model ID |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `MCP_TRANSPORT` | No | `stdio` | Transport mode: `http` or `stdio` |
| `MCP_PORT` | No | `8080` | HTTP server port |

---

## 2. MCP Tool Specifications

### 2.1 `search_boilerplate`

**Purpose:** Search GitHub for license-verified, quality-scored open-source boilerplates matching a natural language query.

#### Input Schema

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "description": "Natural language search query (e.g., 'Next.js Supabase dashboard')"
    },
    "language": {
      "type": "string",
      "nullable": true,
      "description": "Programming language filter (e.g., 'TypeScript')"
    },
    "min_stars": {
      "type": "integer",
      "default": 100,
      "minimum": 0,
      "description": "Minimum star count"
    },
    "max_results": {
      "type": "integer",
      "default": 5,
      "minimum": 1,
      "maximum": 20,
      "description": "Maximum number of results to return"
    },
    "allow_copyleft": {
      "type": "boolean",
      "default": false,
      "description": "If true, include GPL/LGPL/AGPL licensed repos with warnings"
    }
  },
  "required": ["query"]
}
```

#### Output Schema

```json
{
  "type": "array",
  "items": {
    "type": "object",
    "properties": {
      "repo": { "type": "string", "description": "owner/name format" },
      "url": { "type": "string", "format": "uri" },
      "stars": { "type": "integer" },
      "last_commit": { "type": "string", "format": "date" },
      "license": { "type": "string" },
      "license_ok": { "type": "boolean" },
      "quality_score": { "type": "number", "minimum": 0, "maximum": 1 },
      "fit_score": { "type": "number", "minimum": 0, "maximum": 1 },
      "summary": { "type": "string" }
    },
    "required": ["repo", "url", "stars", "last_commit", "license", "license_ok", "quality_score", "fit_score", "summary"]
  }
}
```

#### Internal Pipeline (6 Stages)

```
Stage 1: GitHub Search API
├── Build query: q={query} language:{lang} stars:>={min_stars}
├── Sort by stars, descending
└── Collect top 20 candidates

Stage 2: License Filtering
├── For each candidate, call license_check logic
├── Apply license_policy.yaml whitelist/warn/block rules
├── If allow_copyleft=False, exclude warn-category repos
└── Blocked repos always excluded

Stage 3: Quality Scoring
├── For each passing repo, call scoring.calculate()
├── Compute activity_score, popularity_score, maturity_score, documentation_score
├── Apply archive penalty (x0.3) if applicable
└── Produce quality_score (0~1)

Stage 4: Ranking & Pruning
├── Sort by quality_score descending
└── Take top max_results candidates

Stage 5: LLM Summarization (Parallel)
├── For each top candidate, fetch README (truncated to 4000 chars)
├── Send to Vertex AI Gemini Flash with fit_score prompt
├── Parse JSON response: {summary, fit_score, reasoning}
├── On parse failure: 1 retry, then fallback fit_score=0.5
└── Execute via asyncio.gather with semaphore (max 10 concurrent)

Stage 6: Cache & Return
├── Store results in Firestore (key: search:{hash(query+params)}, TTL: 24h)
└── Return sorted result array
```

**Timeout constraint:** Total pipeline must complete within 30 seconds (Claude Code timeout).

---

### 2.2 `explain_repo`

**Purpose:** Generate a structured summary of a repository's architecture, usage, and caveats.

#### Input Schema

```json
{
  "type": "object",
  "properties": {
    "repo_url": {
      "type": "string",
      "format": "uri",
      "description": "Full GitHub repository URL (https://github.com/owner/name)"
    },
    "focus": {
      "type": "string",
      "enum": ["setup", "architecture", "license"],
      "nullable": true,
      "description": "Optional focus area for the explanation"
    }
  },
  "required": ["repo_url"]
}
```

#### Output Schema

```json
{
  "type": "object",
  "properties": {
    "repo": { "type": "string" },
    "description": { "type": "string" },
    "tech_stack": { "type": "array", "items": { "type": "string" } },
    "file_tree_summary": { "type": "string" },
    "how_to_use": { "type": "string" },
    "caveats": { "type": "string" },
    "license": { "type": "string" }
  },
  "required": ["repo", "description", "tech_stack", "file_tree_summary", "how_to_use", "caveats", "license"]
}
```

#### Processing Flow

```
1. Parse owner/name from repo_url
2. Check Firestore cache (key: explain:{owner}:{name}:{focus})
3. If cache miss:
   a. Fetch repo metadata via GitHub API
   b. Fetch README content (truncate to 4000 chars)
   c. Fetch file tree (top-level + 1 level deep)
   d. Fetch license info via check_license logic
   e. Send all context to Vertex AI with explain prompt (focus-aware)
   f. Parse structured response
   g. Cache result (TTL 24h)
4. Return structured explanation
```

---

### 2.3 `scaffold`

**Purpose:** Download a repository (without git history) into a target directory and optionally generate a CLAUDE.md file.

#### Input Schema

```json
{
  "type": "object",
  "properties": {
    "repo_url": {
      "type": "string",
      "format": "uri",
      "description": "Full GitHub repository URL"
    },
    "target_dir": {
      "type": "string",
      "description": "Target directory path (absolute or relative to CWD)"
    },
    "subdir": {
      "type": "string",
      "nullable": true,
      "description": "Subdirectory within the repo to extract (e.g., 'packages/app')"
    },
    "generate_claude_md": {
      "type": "boolean",
      "default": true,
      "description": "Whether to generate a CLAUDE.md file in the target directory"
    }
  },
  "required": ["repo_url", "target_dir"]
}
```

#### Output Schema

```json
{
  "type": "object",
  "properties": {
    "status": { "type": "string", "enum": ["success", "error"] },
    "path": { "type": "string" },
    "files_created": { "type": "integer" },
    "claude_md_path": { "type": "string", "nullable": true },
    "next_steps": { "type": "array", "items": { "type": "string" } }
  },
  "required": ["status", "path", "files_created"]
}
```

#### Tarball Download Process

```
1. Validate target_dir (security checks -- see below)
2. Resolve repo owner/name from URL
3. Determine default branch via GitHub API
4. Download tarball: GET https://api.github.com/repos/{owner}/{name}/tarball/{branch}
   - Follows redirects to codeload.github.com
   - Stream download to temp file
5. Extract tarball to temporary directory
6. If subdir specified, locate subdir within extracted tree
7. Copy files to target_dir
   - Preserve directory structure
   - MUST preserve LICENSE file
8. If generate_claude_md=True:
   a. Fetch README for context
   b. Generate CLAUDE.md via LLM with scaffold prompt
   c. Include source attribution at top: "# Scaffolded from {repo_url}"
9. Detect next_steps from package.json, requirements.txt, .env.example, etc.
10. Return result with file count
```

#### Security Rules

| Rule | Implementation |
|---|---|
| Path traversal prevention | Resolve `target_dir` to absolute path, verify it is under `os.getcwd()`. Reject `..` components. |
| No overwrite | Check `os.listdir(target_dir)` -- if non-empty, return error |
| LICENSE preservation | After extraction, verify LICENSE file exists in target. If missing, fetch from repo and write. |
| Source attribution | CLAUDE.md must contain `Scaffolded from: {repo_url}` on first line |
| Temp file cleanup | Always delete temp tarball and extracted directory in `finally` block |

---

### 2.4 `check_license`

**Purpose:** Standalone license verification utility. Reused internally by other tools.

#### Input Schema

```json
{
  "type": "object",
  "properties": {
    "repo_url": {
      "type": "string",
      "format": "uri",
      "description": "Full GitHub repository URL"
    }
  },
  "required": ["repo_url"]
}
```

#### Output Schema

```json
{
  "type": "object",
  "properties": {
    "license": { "type": "string", "description": "License name (e.g., 'MIT')" },
    "spdx_id": { "type": "string", "description": "SPDX identifier" },
    "category": { "type": "string", "enum": ["permissive", "copyleft", "unknown", "none"] },
    "commercial_use_ok": { "type": "boolean" },
    "recommended": { "type": "boolean" },
    "warnings": { "type": "array", "items": { "type": "string" } }
  },
  "required": ["license", "spdx_id", "category", "commercial_use_ok", "recommended", "warnings"]
}
```

#### Judgment Logic

```
1. Fetch license from GitHub API (repos/{owner}/{name}/license)
   - Extract: license.spdx_id, license.name

2. Cross-validate with actual LICENSE file content
   - Fetch raw LICENSE file from repo
   - If GitHub API spdx_id != detected license from content:
     -> Add warning: "GitHub API license ({api}) differs from LICENSE file content ({detected})"

3. Categorize using license_policy.yaml:
   - If spdx_id in whitelist -> category="permissive", recommended=True
   - If spdx_id in warn -> category="copyleft", recommended=False
     -> Add warning: "Copyleft license may restrict commercial use"
   - If spdx_id in block or null -> category="unknown"/"none", recommended=False
     -> Add warning: "No clear license detected; legal review required"

4. Determine commercial_use_ok:
   - permissive -> True
   - copyleft -> True (with conditions, add warning)
   - unknown/none -> False

5. Return structured result
```

---

## 3. Pydantic Model Design (`server/models.py`)

### 3.1 Common Models

```python
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from enum import Enum
from datetime import date


class LicenseCategory(str, Enum):
    PERMISSIVE = "permissive"
    COPYLEFT = "copyleft"
    UNKNOWN = "unknown"
    NONE = "none"


class RepoInfo(BaseModel):
    """Core repository metadata used across tools."""
    owner: str
    name: str
    full_name: str = Field(description="owner/name format")
    url: str
    stars: int = Field(ge=0)
    forks: int = Field(ge=0)
    last_commit: date
    archived: bool = False
    default_branch: str = "main"
    language: Optional[str] = None
    description: Optional[str] = None

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, v: str) -> str:
        if "/" not in v or len(v.split("/")) != 2:
            raise ValueError("full_name must be in 'owner/name' format")
        return v


class LicenseInfo(BaseModel):
    """License analysis result."""
    license: str
    spdx_id: str
    category: LicenseCategory
    commercial_use_ok: bool
    recommended: bool
    warnings: list[str] = Field(default_factory=list)


class QualityScore(BaseModel):
    """Breakdown of quality scoring components."""
    activity_score: float = Field(ge=0, le=1)
    popularity_score: float = Field(ge=0, le=1)
    maturity_score: float = Field(ge=0, le=1)
    documentation_score: float = Field(ge=0, le=1)
    total: float = Field(ge=0, le=1)
    archived_penalty_applied: bool = False


class LLMSummary(BaseModel):
    """LLM-generated summary and fit score."""
    summary: str
    fit_score: float = Field(ge=0, le=1)
    reasoning: str
```

### 3.2 Tool Request Models

```python
class SearchRequest(BaseModel):
    """Input for search_boilerplate tool."""
    query: str = Field(min_length=1, max_length=200)
    language: Optional[str] = None
    min_stars: int = Field(default=100, ge=0)
    max_results: int = Field(default=5, ge=1, le=20)
    allow_copyleft: bool = False

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        return v.strip()


class ExplainRequest(BaseModel):
    """Input for explain_repo tool."""
    repo_url: str = Field(pattern=r"^https://github\.com/[\w\-\.]+/[\w\-\.]+/?$")
    focus: Optional[str] = Field(default=None, pattern=r"^(setup|architecture|license)$")


class ScaffoldRequest(BaseModel):
    """Input for scaffold tool."""
    repo_url: str = Field(pattern=r"^https://github\.com/[\w\-\.]+/[\w\-\.]+/?$")
    target_dir: str = Field(min_length=1)
    subdir: Optional[str] = None
    generate_claude_md: bool = True

    @field_validator("target_dir")
    @classmethod
    def validate_no_traversal(cls, v: str) -> str:
        if ".." in v:
            raise ValueError("Path traversal is not allowed in target_dir")
        return v


class LicenseRequest(BaseModel):
    """Input for check_license tool."""
    repo_url: str = Field(pattern=r"^https://github\.com/[\w\-\.]+/[\w\-\.]+/?$")
```

### 3.3 Tool Response Models

```python
class SearchResultItem(BaseModel):
    """Single search result entry."""
    repo: str
    url: str
    stars: int
    last_commit: str
    license: str
    license_ok: bool
    quality_score: float = Field(ge=0, le=1)
    fit_score: float = Field(ge=0, le=1)
    summary: str


class SearchResponse(BaseModel):
    """Response from search_boilerplate tool."""
    results: list[SearchResultItem]


class ExplainResponse(BaseModel):
    """Response from explain_repo tool."""
    repo: str
    description: str
    tech_stack: list[str]
    file_tree_summary: str
    how_to_use: str
    caveats: str
    license: str


class ScaffoldResponse(BaseModel):
    """Response from scaffold tool."""
    status: str = Field(pattern=r"^(success|error)$")
    path: str
    files_created: int = Field(ge=0)
    claude_md_path: Optional[str] = None
    next_steps: list[str] = Field(default_factory=list)
    error_message: Optional[str] = None


class LicenseResponse(BaseModel):
    """Response from check_license tool. Alias for LicenseInfo."""
    license: str
    spdx_id: str
    category: LicenseCategory
    commercial_use_ok: bool
    recommended: bool
    warnings: list[str] = Field(default_factory=list)
```

---

## 4. LLM Integration Design (`server/core/llm.py`)

### 4.1 Vertex AI Gemini Flash Integration

```python
# server/core/llm.py
from google.cloud import aiplatform
from vertexai.generative_models import GenerativeModel, GenerationConfig
import json

class LLMClient:
    def __init__(self, project_id: str, region: str, model_id: str = "gemini-2.0-flash"):
        aiplatform.init(project=project_id, location=region)
        self.model = GenerativeModel(model_id)
        self.fallback_model = None  # Claude Haiku, initialized on first fallback

    async def summarize_repo(
        self,
        query: str,
        readme_text: str,
        repo_metadata: dict,
    ) -> dict:
        """Generate summary and fit_score for a repo."""
        prompt = self._load_prompt("summarize", {
            "query": query,
            "readme": readme_text[:4000],
            "metadata": json.dumps(repo_metadata),
        })
        return await self._generate_json(prompt, schema=SUMMARY_SCHEMA)

    async def explain_repo(
        self,
        readme_text: str,
        file_tree: str,
        repo_metadata: dict,
        focus: str | None,
    ) -> dict:
        """Generate detailed repo explanation."""
        prompt = self._load_prompt("explain", {
            "readme": readme_text[:4000],
            "file_tree": file_tree,
            "metadata": json.dumps(repo_metadata),
            "focus": focus or "general",
        })
        return await self._generate_json(prompt, schema=EXPLAIN_SCHEMA)

    async def generate_claude_md(
        self,
        readme_text: str,
        repo_url: str,
        file_tree: str,
    ) -> str:
        """Generate CLAUDE.md content for scaffolded project."""
        prompt = self._load_prompt("claude_md", {
            "readme": readme_text[:4000],
            "repo_url": repo_url,
            "file_tree": file_tree,
        })
        response = await self._generate(prompt)
        return response

    async def _generate_json(self, prompt: str, schema: dict) -> dict:
        """Generate JSON with schema enforcement and retry logic."""
        config = GenerationConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.1,
        )
        try:
            response = await self.model.generate_content_async(
                prompt,
                generation_config=config,
            )
            return json.loads(response.text)
        except (json.JSONDecodeError, Exception) as e:
            # Retry once
            try:
                response = await self.model.generate_content_async(
                    prompt,
                    generation_config=config,
                )
                return json.loads(response.text)
            except Exception:
                # Fallback strategy
                return await self._fallback_generate(prompt, schema)

    async def _fallback_generate(self, prompt: str, schema: dict) -> dict:
        """Fallback to Claude Haiku if Gemini fails."""
        # Implementation: Use Anthropic API with Claude Haiku
        # If Haiku also fails, return default values
        if "fit_score" in str(schema):
            return {"summary": "Summary unavailable", "fit_score": 0.5, "reasoning": "LLM fallback"}
        raise Exception("LLM generation failed after all retries")

    def _load_prompt(self, template_name: str, variables: dict) -> str:
        """Load prompt template from server/core/prompts/ directory."""
        import importlib.resources as resources
        template_path = f"server/core/prompts/{template_name}.txt"
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()
        for key, value in variables.items():
            template = template.replace(f"{{{{{key}}}}}", str(value))
        return template
```

### 4.2 Prompt Template Management (`server/core/prompts/`)

Templates are plain text files with `{{variable}}` placeholders.

**File structure:**

```
server/core/prompts/
  summarize.txt     # search_boilerplate: README -> summary + fit_score
  explain.txt       # explain_repo: full repo analysis
  claude_md.txt     # scaffold: CLAUDE.md generation
```

**`summarize.txt` template:**

```
You are evaluating an open-source repository for a user query.

User query: {{query}}

Repository metadata:
{{metadata}}

README content:
{{readme}}

Respond in JSON with these fields:
- summary: A single sentence (Korean) describing what this repo does and why it fits the query.
- fit_score: A float from 0.0 to 1.0 indicating how well this repo matches the query.
- reasoning: A brief explanation of the score (English, for logging).

Rules:
- fit_score >= 0.8 means strong match
- fit_score 0.5-0.8 means partial match
- fit_score < 0.5 means weak match
- If the README is empty or unrelated, set fit_score to 0.3
```

**`explain.txt` template:**

```
Analyze this GitHub repository and provide a structured explanation.
Focus area: {{focus}}

Repository metadata:
{{metadata}}

File tree:
{{file_tree}}

README:
{{readme}}

Respond in JSON with these fields:
- description: What this project does (Korean, 2-3 sentences)
- tech_stack: Array of technologies used
- file_tree_summary: Key directories and their purposes (Korean)
- how_to_use: Setup and usage instructions (Korean)
- caveats: Important warnings -- license issues, archived status, missing tests, etc. (Korean)
```

### 4.3 JSON Schema Enforcement

Vertex AI Gemini supports `response_mime_type="application/json"` with `response_schema` to enforce structured output. Schemas used:

```python
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "fit_score": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["summary", "fit_score", "reasoning"],
}

EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "tech_stack": {"type": "array", "items": {"type": "string"}},
        "file_tree_summary": {"type": "string"},
        "how_to_use": {"type": "string"},
        "caveats": {"type": "string"},
    },
    "required": ["description", "tech_stack", "file_tree_summary", "how_to_use", "caveats"],
}
```

### 4.4 Fallback Strategy

```
Attempt 1: Vertex AI Gemini Flash (gemini-2.0-flash)
    |
    |- Success -> return parsed JSON
    |- JSON parse failure -> Retry once
        |
        |- Success -> return parsed JSON
        |- Failure -> Attempt 2
            |
            Attempt 2: Claude Haiku (claude-haiku-4-5-20251001)
                |
                |- Success -> return parsed JSON
                |- Failure -> Return default values
                    - fit_score=0.5 for search
                    - raise error for explain/scaffold
```

---

## 5. GitHub Client Design (`server/core/github_client.py`)

### 5.1 PyGithub Wrapper Structure

```python
# server/core/github_client.py
import asyncio
from github import Github, RateLimitExceededException
from server.core.cache import CacheClient

class GitHubClient:
    SEMAPHORE_LIMIT = 10
    RATE_LIMIT_THRESHOLD = 10
    MAX_RETRIES = 3
    RETRY_BACKOFF = [1, 2, 4]  # seconds

    def __init__(self, token: str, cache: CacheClient):
        self._github = Github(token, per_page=30)
        self._cache = cache
        self._semaphore = asyncio.Semaphore(self.SEMAPHORE_LIMIT)
        self._rate_remaining: int | None = None

    async def search_repos(self, query: str, language: str | None, min_stars: int, limit: int = 20) -> list[dict]:
        """Search GitHub repositories with caching."""
        ...

    async def get_repo(self, owner: str, name: str) -> dict:
        """Get single repo metadata with caching."""
        ...

    async def get_readme(self, owner: str, name: str) -> str:
        """Get README content, truncated to 4000 chars."""
        ...

    async def get_license(self, owner: str, name: str) -> dict:
        """Get license info from GitHub API."""
        ...

    async def get_license_file_content(self, owner: str, name: str) -> str | None:
        """Get raw LICENSE file content for cross-validation."""
        ...

    async def get_file_tree(self, owner: str, name: str, depth: int = 2) -> list[str]:
        """Get repository file tree (top-level + 1 level deep)."""
        ...

    async def download_tarball(self, owner: str, name: str, branch: str) -> bytes:
        """Download repository tarball."""
        ...
```

### 5.2 Rate Limit Tracking & Protection

```python
async def _check_rate_limit(self) -> bool:
    """Check if we have sufficient rate limit remaining."""
    rate_limit = self._github.get_rate_limit()
    self._rate_remaining = rate_limit.core.remaining

    if self._rate_remaining < self.RATE_LIMIT_THRESHOLD:
        return False  # Signal to use cache fallback
    return True

async def _execute_with_rate_protection(self, operation, cache_key: str):
    """Execute GitHub API call with rate limit protection."""
    # Check cache first
    cached = await self._cache.get(cache_key)
    if cached is not None:
        return cached

    # Check rate limit
    if not await self._check_rate_limit():
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached
        raise Exception("GitHub API rate limit exhausted and no cache available")

    # Execute with retry
    result = await self._execute_with_retry(operation)

    # Cache result
    await self._cache.set(cache_key, result, ttl_hours=24)
    return result
```

### 5.3 Async Parallel Calls

```python
async def get_repos_parallel(self, repo_ids: list[str]) -> list[dict]:
    """Fetch multiple repos in parallel with semaphore control."""
    async def _fetch_one(repo_id: str) -> dict:
        async with self._semaphore:
            owner, name = repo_id.split("/")
            return await self.get_repo(owner, name)

    results = await asyncio.gather(
        *[_fetch_one(rid) for rid in repo_ids],
        return_exceptions=True,
    )
    return [r for r in results if not isinstance(r, Exception)]
```

### 5.4 Retry Logic

```python
async def _execute_with_retry(self, operation) -> any:
    """Execute with exponential backoff for 429/503 errors."""
    for attempt in range(self.MAX_RETRIES):
        try:
            return await asyncio.to_thread(operation)
        except RateLimitExceededException:
            if attempt < self.MAX_RETRIES - 1:
                await asyncio.sleep(self.RETRY_BACKOFF[attempt])
            else:
                raise
        except Exception as e:
            if "503" in str(e) and attempt < self.MAX_RETRIES - 1:
                await asyncio.sleep(self.RETRY_BACKOFF[attempt])
            else:
                raise
```

### 5.5 Cache Integration

All GitHub API responses route through Firestore cache:

| Cache Key Pattern | TTL | Description |
|---|---|---|
| `repo:{owner}:{name}` | 24h | Single repo metadata |
| `readme:{owner}:{name}` | 24h | README content |
| `license:{owner}:{name}` | 24h | License info |
| `tree:{owner}:{name}` | 24h | File tree |
| `search:{sha256(query+params)}` | 24h | Search results |

---

## 6. Installation & Integration Guide

### 6.1 Claude Code -- `claude mcp add` (stdio mode)

**Local installation with pip:**

```bash
# Clone and install
git clone https://github.com/artience/oss-scout.git
cd oss-scout
pip install -e .

# Set up environment
cp .env.example .env
# Edit .env with your GITHUB_TOKEN and GCP credentials

# Add to Claude Code
claude mcp add oss-scout -- python -m server.main
```

**Using uvx (recommended for Claude Code):**

```bash
claude mcp add oss-scout -- uvx --from git+https://github.com/artience/oss-scout.git python -m server.main
```

**Resulting `.mcp.json` entry:**

```json
{
  "mcpServers": {
    "oss-scout": {
      "command": "python",
      "args": ["-m", "server.main"],
      "cwd": "/path/to/oss-scout",
      "env": {
        "MCP_TRANSPORT": "stdio",
        "GITHUB_TOKEN": "${GITHUB_TOKEN}"
      }
    }
  }
}
```

### 6.2 Codex Integration

Codex uses the same stdio transport. Add to your project's MCP configuration:

```bash
# Option 1: Direct
codex mcp add oss-scout -- python -m server.main

# Option 2: With explicit env vars
codex mcp add oss-scout \
  --env GITHUB_TOKEN=$GITHUB_TOKEN \
  --env GCP_PROJECT_ID=$GCP_PROJECT_ID \
  -- python -m server.main
```

### 6.3 Cloud Run URL Remote Integration (HTTP/SSE mode)

For remote deployment via Cloud Run:

```bash
# Deploy to Cloud Run (requires gcloud CLI)
gcloud run deploy oss-scout \
  --source . \
  --region asia-northeast3 \
  --allow-unauthenticated \
  --set-env-vars "MCP_TRANSPORT=http,MCP_PORT=8080"

# Get the service URL
SERVICE_URL=$(gcloud run services describe oss-scout --region asia-northeast3 --format 'value(status.url)')

# Add remote MCP server to Claude Code
claude mcp add oss-scout --transport sse "${SERVICE_URL}/sse"
```

**Resulting `.mcp.json` for remote:**

```json
{
  "mcpServers": {
    "oss-scout": {
      "transport": "sse",
      "url": "https://oss-scout-<hash>-an.a.run.app/sse"
    }
  }
}
```

### 6.4 Verifying the Connection

After adding the MCP server, verify tools are registered:

```bash
# List available MCP tools
claude mcp list

# Test a tool
claude "oss-scout search_boilerplate로 Next.js dashboard 보일러플레이트 검색해줘"
```

Expected tools visible after connection:

| Tool Name | Description |
|---|---|
| `search_boilerplate` | Search GitHub for quality-verified boilerplate repos |
| `explain_repo` | Explain a repository's structure and usage |
| `scaffold` | Download and scaffold a repo into target directory |
| `check_license` | Check a repository's license status |

---

## Appendix A: MCP JSON-RPC Message Examples

### Tool Discovery (tools/list)

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list"
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "tools": [
      {
        "name": "search_boilerplate",
        "description": "Search GitHub for license-verified, quality-scored open-source boilerplates matching a natural language query.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "query": { "type": "string" },
            "language": { "type": "string" },
            "min_stars": { "type": "integer", "default": 100 },
            "max_results": { "type": "integer", "default": 5 },
            "allow_copyleft": { "type": "boolean", "default": false }
          },
          "required": ["query"]
        }
      },
      {
        "name": "explain_repo",
        "description": "Generate a structured summary of a repository's architecture, usage, and caveats.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "repo_url": { "type": "string" },
            "focus": { "type": "string", "enum": ["setup", "architecture", "license"] }
          },
          "required": ["repo_url"]
        }
      },
      {
        "name": "scaffold",
        "description": "Download a repository into a target directory without git history and optionally generate CLAUDE.md.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "repo_url": { "type": "string" },
            "target_dir": { "type": "string" },
            "subdir": { "type": "string" },
            "generate_claude_md": { "type": "boolean", "default": true }
          },
          "required": ["repo_url", "target_dir"]
        }
      },
      {
        "name": "check_license",
        "description": "Check a repository's license status and provide categorization with warnings.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "repo_url": { "type": "string" }
          },
          "required": ["repo_url"]
        }
      }
    ]
  }
}
```

### Tool Call Example (tools/call)

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "search_boilerplate",
    "arguments": {
      "query": "Next.js Supabase dashboard",
      "language": "TypeScript",
      "min_stars": 100,
      "max_results": 3
    }
  }
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "[{\"repo\":\"example/nextjs-supabase-starter\",\"url\":\"https://github.com/example/nextjs-supabase-starter\",\"stars\":2340,\"last_commit\":\"2026-03-15\",\"license\":\"MIT\",\"license_ok\":true,\"quality_score\":0.87,\"fit_score\":0.92,\"summary\":\"Next.js 14 + Supabase Auth + Dashboard UI. TypeScript 기반 풀스택 스타터.\"}]"
      }
    ]
  }
}
```

---

## Appendix B: Error Handling

MCP tools return errors using the standard MCP error format:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "error": {
    "code": -32000,
    "message": "GitHub API rate limit exhausted. Try again in 45 minutes.",
    "data": {
      "rate_limit_reset": "2026-04-13T15:30:00Z"
    }
  }
}
```

**Error codes used:**

| Code | Meaning | When |
|---|---|---|
| `-32602` | Invalid params | Input validation failure (Pydantic) |
| `-32000` | Rate limit | GitHub API rate limit exhausted, no cache |
| `-32001` | Not found | Repository does not exist or is private |
| `-32002` | Path security | scaffold target_dir fails security check |
| `-32003` | LLM failure | All LLM attempts (Gemini + Haiku) failed |
| `-32004` | Timeout | Pipeline exceeded 30s |

**User-facing error messages are in Korean; debug logs in English.**

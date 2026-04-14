"""check_env MCP tool.

Analyzes a repository to identify required API keys, environment variables,
and external service configurations before scaffolding.

Pipeline:
  1. README keyword detection (API_KEY, SECRET, TOKEN, etc.)
  2. File tree scan for .env.example / .env.sample / .env.template
  3. .env.example content parsing (if found)
  4. package.json / pyproject.toml dependency-based service detection
  5. Known service-key mapping (rule-based)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from mcp.types import TextContent, Tool

from server.core.github_client import GitHubClient, parse_repo_url

logger = logging.getLogger("oss-scout")

_REPO_URL_PATTERN = re.compile(
    r"^https://github\.com/[\w.\-]+/[\w.\-]+/?$"
)

# ---------------------------------------------------------------------------
# Known service -> env var mapping (rule-based, no LLM)
# ---------------------------------------------------------------------------

SERVICE_KEYS: dict[str, dict[str, Any]] = {
    "stripe": {
        "keys": ["STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_WEBHOOK_SECRET"],
        "signup_url": "https://dashboard.stripe.com/register",
        "description": "결제 처리",
        "sdk_patterns": ["stripe", "@stripe/stripe-js"],
    },
    "supabase": {
        "keys": ["SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY"],
        "signup_url": "https://supabase.com/dashboard",
        "description": "백엔드/인증/DB",
        "sdk_patterns": ["@supabase/supabase-js", "supabase-py", "supabase"],
    },
    "firebase": {
        "keys": ["FIREBASE_API_KEY", "FIREBASE_AUTH_DOMAIN", "FIREBASE_PROJECT_ID"],
        "signup_url": "https://console.firebase.google.com",
        "description": "백엔드/인증",
        "sdk_patterns": ["firebase", "firebase-admin", "@firebase/app"],
    },
    "openai": {
        "keys": ["OPENAI_API_KEY"],
        "signup_url": "https://platform.openai.com/api-keys",
        "description": "AI/LLM",
        "sdk_patterns": ["openai"],
    },
    "anthropic": {
        "keys": ["ANTHROPIC_API_KEY"],
        "signup_url": "https://console.anthropic.com",
        "description": "AI/LLM",
        "sdk_patterns": ["@anthropic-ai/sdk", "anthropic"],
    },
    "aws": {
        "keys": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"],
        "signup_url": "https://aws.amazon.com",
        "description": "클라우드 인프라",
        "sdk_patterns": ["aws-sdk", "@aws-sdk/client-s3", "boto3"],
    },
    "sendgrid": {
        "keys": ["SENDGRID_API_KEY"],
        "signup_url": "https://sendgrid.com",
        "description": "이메일 발송",
        "sdk_patterns": ["@sendgrid/mail", "sendgrid"],
    },
    "twilio": {
        "keys": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"],
        "signup_url": "https://twilio.com",
        "description": "SMS/통화",
        "sdk_patterns": ["twilio"],
    },
    "google": {
        "keys": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"],
        "signup_url": "https://console.cloud.google.com",
        "description": "OAuth/API",
        "sdk_patterns": ["googleapis", "google-auth", "@google-cloud/"],
    },
    "github_oauth": {
        "keys": ["GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"],
        "signup_url": "https://github.com/settings/developers",
        "description": "GitHub OAuth",
        "sdk_patterns": [],
    },
    "database": {
        "keys": ["DATABASE_URL", "DB_HOST", "DB_USER", "DB_PASSWORD"],
        "signup_url": None,
        "description": "데이터베이스",
        "sdk_patterns": ["prisma", "@prisma/client", "pg", "mysql2", "mongoose", "sqlalchemy"],
    },
    "redis": {
        "keys": ["REDIS_URL", "REDIS_HOST"],
        "signup_url": None,
        "description": "캐시/세션",
        "sdk_patterns": ["redis", "ioredis", "bull", "bullmq"],
    },
    "cloudinary": {
        "keys": ["CLOUDINARY_URL", "CLOUDINARY_API_KEY"],
        "signup_url": "https://cloudinary.com",
        "description": "이미지 호스팅",
        "sdk_patterns": ["cloudinary"],
    },
    "resend": {
        "keys": ["RESEND_API_KEY"],
        "signup_url": "https://resend.com",
        "description": "이메일 발송",
        "sdk_patterns": ["resend"],
    },
    "vercel": {
        "keys": ["VERCEL_TOKEN"],
        "signup_url": "https://vercel.com",
        "description": "배포",
        "sdk_patterns": ["@vercel/analytics", "@vercel/og"],
    },
    "clerk": {
        "keys": [
            "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
            "CLERK_SECRET_KEY",
        ],
        "signup_url": "https://clerk.com",
        "description": "인증/사용자 관리",
        "sdk_patterns": ["@clerk/nextjs", "@clerk/clerk-sdk-node"],
    },
    "nextauth": {
        "keys": ["NEXTAUTH_SECRET", "NEXTAUTH_URL"],
        "signup_url": None,
        "description": "인증 (NextAuth.js)",
        "sdk_patterns": ["next-auth"],
    },
    "sentry": {
        "keys": ["SENTRY_DSN"],
        "signup_url": "https://sentry.io",
        "description": "에러 모니터링",
        "sdk_patterns": ["@sentry/nextjs", "@sentry/node", "sentry-sdk"],
    },
    "uploadthing": {
        "keys": ["UPLOADTHING_SECRET", "UPLOADTHING_APP_ID"],
        "signup_url": "https://uploadthing.com",
        "description": "파일 업로드",
        "sdk_patterns": ["uploadthing", "@uploadthing/react"],
    },
}

# Env var patterns to detect in README text
_ENV_VAR_PATTERN = re.compile(
    r"\b([A-Z][A-Z0-9_]{1,}(?:_KEY|_SECRET|_TOKEN|_URL|_ID|_DSN|_PASSWORD"
    r"|_HOST|_PORT|_DOMAIN|_REGION|_ENDPOINT))\b"
)

# Generic env var pattern (any ALL_CAPS_WITH_UNDERSCORES in code-like context)
_GENERIC_ENV_PATTERN = re.compile(
    r"^([A-Z][A-Z0-9_]{2,})=",
    re.MULTILINE,
)

# Env-related file names
_ENV_FILE_NAMES = {
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.local.example",
    ".env.development.example",
    ".env.production.example",
}

# README sections that mention env setup
_ENV_SECTION_KEYWORDS = [
    "environment variable",
    "env variable",
    ".env",
    "api key",
    "api_key",
    "secret key",
    "configuration",
    "setup",
    "getting started",
    "환경 변수",
    "환경변수",
]


def _log(level: str, event: str, **kwargs: Any) -> None:
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(json.dumps(entry, ensure_ascii=False))


def _validate_args(arguments: dict[str, Any]) -> str:
    """Validate check_env arguments. Returns repo_url."""
    repo_url = arguments.get("repo_url", "")
    if not isinstance(repo_url, str) or not repo_url.strip():
        raise ValueError("repo_url을 입력해주세요.")
    repo_url = repo_url.strip().rstrip("/")
    if not _REPO_URL_PATTERN.match(repo_url):
        raise ValueError(
            "repo_url은 https://github.com/{owner}/{repo} 형식이어야 합니다."
        )
    return repo_url


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

ENVCHECK_TOOL = Tool(
    name="check_env",
    description=(
        "프로젝트에 필요한 API 키, 환경변수, 외부 서비스 설정을 사전에 파악합니다. "
        "scaffold 전에 어떤 서비스 가입과 키 준비가 필요한지 체크리스트를 제공합니다."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo_url": {
                "type": "string",
                "description": "GitHub 레포 URL (https://github.com/owner/name)",
            },
        },
        "required": ["repo_url"],
    },
)


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def extract_env_vars_from_text(text: str) -> set[str]:
    """Extract environment variable names from text using pattern matching."""
    if not text:
        return set()
    return set(_ENV_VAR_PATTERN.findall(text))


def extract_env_vars_from_dotenv(content: str) -> list[dict[str, str]]:
    """Parse .env.example content into structured var entries.

    Returns list of dicts with 'name' and 'comment' keys.
    """
    if not content:
        return []

    results: list[dict[str, str]] = []
    lines = content.strip().splitlines()
    last_comment = ""

    for line in lines:
        stripped = line.strip()
        if not stripped:
            last_comment = ""
            continue
        if stripped.startswith("#"):
            last_comment = stripped.lstrip("# ").strip()
            continue

        match = re.match(r"^([A-Z][A-Z0-9_]*)=", stripped)
        if match:
            results.append({
                "name": match.group(1),
                "comment": last_comment,
            })
            last_comment = ""

    return results


def detect_services_from_readme(readme: str) -> set[str]:
    """Detect known services mentioned in README text."""
    if not readme:
        return set()

    readme_lower = readme.lower()
    detected: set[str] = set()

    for service_name, info in SERVICE_KEYS.items():
        # Check service name in text
        check_name = service_name.replace("_", " ")
        if check_name in readme_lower:
            detected.add(service_name)
            continue

        # Check if any known key names appear in README
        for key in info["keys"]:
            if key.lower() in readme_lower or key in readme:
                detected.add(service_name)
                break

    return detected


def detect_services_from_dependencies(dependencies: dict[str, Any]) -> set[str]:
    """Detect services from package.json dependencies or similar."""
    if not dependencies:
        return set()

    dep_names = set(dependencies.keys())
    detected: set[str] = set()

    for service_name, info in SERVICE_KEYS.items():
        sdk_patterns: list[str] = info.get("sdk_patterns", [])
        for pattern in sdk_patterns:
            # Exact match or prefix match for scoped packages
            for dep in dep_names:
                if dep == pattern or dep.startswith(pattern):
                    detected.add(service_name)
                    break

    return detected


def find_env_files(file_tree: list[str]) -> list[str]:
    """Find .env-related files in the file tree."""
    found: list[str] = []
    for path in file_tree:
        filename = path.rsplit("/", 1)[-1] if "/" in path else path
        if filename in _ENV_FILE_NAMES:
            found.append(path)
    return found


def classify_env_var(
    var_name: str,
    detected_services: set[str],
) -> dict[str, Any]:
    """Classify a single env var: which service, required/optional, signup URL."""
    var_upper = var_name.upper()

    # Check against known service keys
    for service_name, info in SERVICE_KEYS.items():
        if service_name not in detected_services:
            continue
        for key in info["keys"]:
            if var_upper == key:
                return {
                    "name": var_name,
                    "service": service_name.replace("_", " ").title(),
                    "description": info["description"],
                    "signup_url": info.get("signup_url"),
                    "required": True,
                }

    # Check all services even if not detected (for vars found in .env.example)
    for service_name, info in SERVICE_KEYS.items():
        for key in info["keys"]:
            if var_upper == key:
                return {
                    "name": var_name,
                    "service": service_name.replace("_", " ").title(),
                    "description": info["description"],
                    "signup_url": info.get("signup_url"),
                    "required": True,
                }

    # Unknown var -- classify by naming convention
    is_required = any(
        kw in var_upper
        for kw in ("SECRET", "KEY", "TOKEN", "PASSWORD", "URL", "DSN")
    )

    return {
        "name": var_name,
        "service": "Unknown",
        "description": "",
        "signup_url": None,
        "required": is_required,
    }


def build_preparation_checklist(
    required_vars: list[dict[str, Any]],
    env_files: list[str],
) -> list[str]:
    """Build a human-readable preparation checklist."""
    checklist: list[str] = []
    seen_services: set[str] = set()
    step = 1

    for var in required_vars:
        service = var.get("service", "Unknown")
        signup_url = var.get("signup_url")
        if service == "Unknown" or service in seen_services:
            continue
        seen_services.add(service)

        if signup_url:
            checklist.append(f"{step}. {service} 계정 생성: {signup_url}")
        else:
            checklist.append(f"{step}. {service} 설정 준비")
        step += 1

    if env_files:
        env_file = env_files[0]
        checklist.append(
            f"{step}. {env_file}을 .env로 복사하고 값 입력"
        )
    else:
        checklist.append(f"{step}. .env 파일 생성 후 환경변수 값 입력")

    return checklist


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

async def handle_envcheck(
    arguments: dict[str, Any],
    github: GitHubClient,
) -> list[TextContent]:
    """Execute check_env: analyze repo for required env vars and API keys."""
    repo_url = _validate_args(arguments)
    owner, name = parse_repo_url(repo_url)

    _log("info", "envcheck_start", repo=f"{owner}/{name}")

    # 1. Fetch README
    readme = await github.get_readme(owner, name)

    # 2. Fetch file tree
    file_tree = await github.get_file_tree(owner, name)

    # 3. Find .env files
    env_files = find_env_files(file_tree)

    # 4. Parse .env.example content if found
    dotenv_vars: list[dict[str, str]] = []
    for env_file in env_files:
        try:
            content = await github.get_file_content(owner, name, env_file)
            dotenv_vars.extend(extract_env_vars_from_dotenv(content))
        except Exception:
            _log("warning", "env_file_read_failed", file=env_file)

    # 5. Detect services from README
    readme_services = detect_services_from_readme(readme)

    # 6. Detect services from dependencies (package.json)
    dep_services: set[str] = set()
    if "package.json" in file_tree:
        try:
            pkg_content = await github.get_file_content(
                owner, name, "package.json",
            )
            pkg_data = json.loads(pkg_content)
            all_deps: dict[str, Any] = {}
            all_deps.update(pkg_data.get("dependencies", {}))
            all_deps.update(pkg_data.get("devDependencies", {}))
            dep_services = detect_services_from_dependencies(all_deps)
        except Exception:
            _log("warning", "package_json_parse_failed")

    # Also check pyproject.toml dependencies if present
    if any(f == "pyproject.toml" or f.endswith("/pyproject.toml") for f in file_tree):
        try:
            pyproject = await github.get_file_content(
                owner, name, "pyproject.toml",
            )
            # Simple pattern match for dependency names
            dep_names_in_pyproject = re.findall(
                r'"([a-zA-Z][a-zA-Z0-9_-]*)"', pyproject,
            )
            pyproject_deps = {d: "*" for d in dep_names_in_pyproject}
            dep_services |= detect_services_from_dependencies(pyproject_deps)
        except Exception:
            _log("warning", "pyproject_parse_failed")

    # 7. Extract env vars from README
    readme_env_vars = extract_env_vars_from_text(readme)

    # 8. Collect all detected services
    all_services = readme_services | dep_services

    # 9. Collect all env var names (from .env files + README)
    all_var_names: set[str] = set()
    for dv in dotenv_vars:
        all_var_names.add(dv["name"])
    all_var_names |= readme_env_vars

    # Add expected keys from detected services
    for service_name in all_services:
        info = SERVICE_KEYS.get(service_name, {})
        for key in info.get("keys", []):
            all_var_names.add(key)

    # 10. Classify all vars
    classified: list[dict[str, Any]] = []
    for var_name in sorted(all_var_names):
        classified.append(classify_env_var(var_name, all_services))

    required_vars = [v for v in classified if v["required"]]
    optional_vars = [v for v in classified if not v["required"]]

    # 11. Build checklist
    checklist = build_preparation_checklist(required_vars, env_files)

    result = {
        "repo": f"{owner}/{name}",
        "required_env_vars": required_vars,
        "optional_env_vars": optional_vars,
        "env_files_found": env_files,
        "detected_services": sorted(all_services),
        "total_required": len(required_vars),
        "total_optional": len(optional_vars),
        "preparation_checklist": checklist,
    }

    _log(
        "info", "envcheck_complete",
        repo=f"{owner}/{name}",
        required=len(required_vars),
        optional=len(optional_vars),
        services=len(all_services),
    )

    return [TextContent(
        type="text",
        text=json.dumps(result, ensure_ascii=False, indent=2),
    )]

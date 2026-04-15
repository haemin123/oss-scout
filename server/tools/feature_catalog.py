"""Feature catalog for search_feature tool.

Maps natural-language feature keywords to structured GitHub Code Search
queries, required dependencies, stack-specific filters, and expected
file structures. All data is rule-based with no LLM dependency.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Stack-specific search filters
# ---------------------------------------------------------------------------

STACK_SEARCH_FILTERS: dict[str, dict[str, Any]] = {
    "nextjs": {
        "language": "typescript",
        "path_hints": ["app/api", "pages/api", "components"],
    },
    "react": {
        "language": "typescript",
        "path_hints": ["src/components", "src/hooks"],
    },
    "express": {
        "language": "typescript",
        "path_hints": ["routes", "middleware", "controllers"],
    },
    "fastapi": {
        "language": "python",
        "path_hints": ["routers", "middleware", "api"],
    },
    "vue": {
        "language": "typescript",
        "path_hints": ["src/components", "src/composables"],
    },
    "django": {
        "language": "python",
        "path_hints": ["views", "middleware", "api"],
    },
    "flask": {
        "language": "python",
        "path_hints": ["routes", "middleware", "api"],
    },
    "fastify": {
        "language": "typescript",
        "path_hints": ["routes", "plugins", "middleware"],
    },
}


# ---------------------------------------------------------------------------
# Feature definitions
# ---------------------------------------------------------------------------

FeatureEntry = dict[str, Any]

FEATURE_CATALOG: dict[str, FeatureEntry] = {
    "stripe-payment": {
        "aliases": [
            "stripe", "payment", "checkout", "billing",
            "결제", "스트라이프",
        ],
        "search_queries": [
            "stripe checkout session",
            "createPaymentIntent",
            "stripe webhook",
        ],
        "required_deps": {
            "typescript": ["stripe", "@stripe/stripe-js"],
            "javascript": ["stripe", "@stripe/stripe-js"],
            "python": ["stripe"],
        },
        "stack_filters": {
            "nextjs": {"path": "app/api"},
            "express": {"path": "routes"},
            "fastapi": {"path": "routers"},
        },
        "typical_file_structure": [
            "api/checkout/route.ts",
            "lib/stripe.ts",
            "components/CheckoutForm.tsx",
        ],
    },
    "auth-middleware": {
        "aliases": [
            "auth", "authentication", "login", "signup",
            "session", "jwt", "인증", "로그인", "미들웨어",
        ],
        "search_queries": [
            "auth middleware jwt",
            "session verify token",
            "authentication guard",
        ],
        "required_deps": {
            "typescript": ["jsonwebtoken", "bcrypt"],
            "javascript": ["jsonwebtoken", "bcrypt"],
            "python": ["pyjwt", "passlib"],
        },
        "stack_filters": {
            "nextjs": {"path": "middleware"},
            "express": {"path": "middleware"},
            "fastapi": {"path": "middleware"},
        },
        "typical_file_structure": [
            "middleware/auth.ts",
            "lib/auth.ts",
            "api/auth/login/route.ts",
        ],
    },
    "dark-mode": {
        "aliases": [
            "dark mode", "darkmode", "theme", "theme toggle",
            "다크모드", "테마", "다크 모드",
        ],
        "search_queries": [
            "dark mode toggle",
            "useTheme provider",
            "theme context dark",
        ],
        "required_deps": {
            "typescript": ["next-themes"],
            "javascript": ["next-themes"],
            "python": [],
        },
        "stack_filters": {
            "nextjs": {"path": "components"},
            "react": {"path": "src/components"},
        },
        "typical_file_structure": [
            "components/ThemeToggle.tsx",
            "providers/ThemeProvider.tsx",
            "hooks/useTheme.ts",
        ],
    },
    "file-upload": {
        "aliases": [
            "file upload", "upload", "file", "s3", "storage",
            "dropzone", "파일 업로드", "파일", "업로드",
        ],
        "search_queries": [
            "upload file s3",
            "multer upload middleware",
            "file upload handler",
        ],
        "required_deps": {
            "typescript": ["multer", "@aws-sdk/client-s3"],
            "javascript": ["multer", "@aws-sdk/client-s3"],
            "python": ["boto3", "python-multipart"],
        },
        "stack_filters": {
            "nextjs": {"path": "app/api"},
            "express": {"path": "routes"},
            "fastapi": {"path": "routers"},
        },
        "typical_file_structure": [
            "api/upload/route.ts",
            "lib/storage.ts",
            "components/FileUpload.tsx",
        ],
    },
    "i18n": {
        "aliases": [
            "i18n", "internationalization", "locale", "translation",
            "다국어", "번역", "국제화",
        ],
        "search_queries": [
            "i18n locale translation",
            "useTranslation hook",
            "next-intl messages",
        ],
        "required_deps": {
            "typescript": ["next-intl"],
            "javascript": ["i18next", "react-i18next"],
            "python": ["babel"],
        },
        "stack_filters": {
            "nextjs": {"path": "messages"},
            "react": {"path": "src/locales"},
        },
        "typical_file_structure": [
            "messages/en.json",
            "messages/ko.json",
            "middleware.ts",
            "lib/i18n.ts",
        ],
    },
    "rate-limiting": {
        "aliases": [
            "rate limit", "rate-limit", "rate limiting", "throttle",
            "속도 제한", "레이트 리밋",
        ],
        "search_queries": [
            "rate limit middleware",
            "rateLimit express",
            "sliding window rate",
        ],
        "required_deps": {
            "typescript": ["express-rate-limit"],
            "javascript": ["express-rate-limit"],
            "python": ["slowapi"],
        },
        "stack_filters": {
            "express": {"path": "middleware"},
            "fastapi": {"path": "middleware"},
        },
        "typical_file_structure": [
            "middleware/rateLimit.ts",
            "lib/rateLimiter.ts",
        ],
    },
    "websocket-chat": {
        "aliases": [
            "websocket", "ws", "socket", "realtime", "chat",
            "실시간 채팅", "웹소켓", "소켓",
        ],
        "search_queries": [
            "websocket connection handler",
            "socket.io chat",
            "ws message broadcast",
        ],
        "required_deps": {
            "typescript": ["socket.io", "socket.io-client"],
            "javascript": ["socket.io", "socket.io-client"],
            "python": ["websockets", "python-socketio"],
        },
        "stack_filters": {
            "nextjs": {"path": "app/api"},
            "express": {"path": "routes"},
            "fastapi": {"path": "routers"},
        },
        "typical_file_structure": [
            "api/socket/route.ts",
            "lib/socket.ts",
            "components/Chat.tsx",
        ],
    },
    "email-send": {
        "aliases": [
            "email", "mail", "sendgrid", "ses", "newsletter",
            "nodemailer", "이메일", "메일", "이메일 발송",
        ],
        "search_queries": [
            "sendEmail transactional",
            "nodemailer transport",
            "email template send",
        ],
        "required_deps": {
            "typescript": ["nodemailer", "@sendgrid/mail"],
            "javascript": ["nodemailer", "@sendgrid/mail"],
            "python": ["sendgrid"],
        },
        "stack_filters": {
            "nextjs": {"path": "app/api"},
            "express": {"path": "routes"},
            "fastapi": {"path": "routers"},
        },
        "typical_file_structure": [
            "api/email/route.ts",
            "lib/email.ts",
            "templates/email/welcome.html",
        ],
    },
    "pagination": {
        "aliases": [
            "pagination", "infinite scroll", "paging", "cursor",
            "페이지네이션", "무한스크롤", "페이징",
        ],
        "search_queries": [
            "pagination cursor offset",
            "useInfiniteQuery pagination",
            "paginate results limit",
        ],
        "required_deps": {
            "typescript": ["@tanstack/react-query"],
            "javascript": ["@tanstack/react-query"],
            "python": [],
        },
        "stack_filters": {
            "nextjs": {"path": "components"},
            "react": {"path": "src/hooks"},
            "fastapi": {"path": "routers"},
        },
        "typical_file_structure": [
            "components/Pagination.tsx",
            "hooks/usePagination.ts",
            "lib/paginate.ts",
        ],
    },
    "search-filter": {
        "aliases": [
            "search filter", "filter", "search", "filtering",
            "검색 필터", "필터링", "검색",
        ],
        "search_queries": [
            "search filter query params",
            "debounce search input",
            "filter sort paginate",
        ],
        "required_deps": {
            "typescript": ["use-debounce"],
            "javascript": ["lodash.debounce"],
            "python": [],
        },
        "stack_filters": {
            "nextjs": {"path": "components"},
            "react": {"path": "src/components"},
        },
        "typical_file_structure": [
            "components/SearchFilter.tsx",
            "hooks/useSearch.ts",
            "lib/search.ts",
        ],
    },
}


def match_feature(feature: str) -> FeatureEntry | None:
    """Match a natural-language feature string to a catalog entry.

    Performs case-insensitive matching against feature IDs and aliases.
    Returns the matching FeatureEntry or None if no match found.
    """
    normalized = feature.strip().lower()

    # Direct ID match
    if normalized in FEATURE_CATALOG:
        return FEATURE_CATALOG[normalized]

    # Alias match
    for _feature_id, entry in FEATURE_CATALOG.items():
        aliases: list[str] = entry.get("aliases", [])
        for alias in aliases:
            if alias.lower() == normalized or alias.lower() in normalized:
                return entry

    return None


def get_search_queries(
    entry: FeatureEntry,
    framework: str | None = None,
) -> list[str]:
    """Build search queries from a feature entry, optionally filtered by stack.

    Returns base queries enriched with stack-specific qualifiers.
    """
    base_queries: list[str] = list(entry.get("search_queries", []))

    if framework and framework in entry.get("stack_filters", {}):
        stack_filter = entry["stack_filters"][framework]
        enriched: list[str] = []
        for q in base_queries:
            qualifiers = " ".join(f"{k}:{v}" for k, v in stack_filter.items())
            enriched.append(f"{q} {qualifiers}")
        return enriched

    return base_queries


def get_required_deps(
    entry: FeatureEntry,
    language: str,
) -> list[str]:
    """Get required dependencies for a feature and language.

    Returns a list of package names.
    """
    deps_map: dict[str, list[str]] = entry.get("required_deps", {})
    return list(deps_map.get(language, []))


def get_install_command(deps: list[str], language: str) -> str:
    """Build an install command for the given dependencies and language.

    Returns empty string if no dependencies.
    """
    if not deps:
        return ""
    if language == "python":
        return f"pip install {' '.join(deps)}"
    return f"npm install {' '.join(deps)}"

"""Quality scoring engine for OSS Scout.

Implements the 6-axis scoring formula.
All sub-scores are normalized to 0-1 range.
"""

from __future__ import annotations

import math
import re
from datetime import date

from server.models import QualityScore, RepoInfo

# --- Common tech stack keywords for detection ---

_STACK_KEYWORDS: dict[str, list[str]] = {
    "next.js": ["next.config", "pages/", "app/", "nextjs"],
    "nextjs": ["next.config", "pages/", "app/", "next.js"],
    "react": ["react", "jsx", "tsx", "component"],
    "vue": ["vue", "nuxt", ".vue"],
    "angular": ["angular", "ng-", ".component.ts"],
    "svelte": ["svelte", "svelte.config"],
    "express": ["express", "app.listen", "router"],
    "fastapi": ["fastapi", "uvicorn", "main.py"],
    "django": ["django", "manage.py", "settings.py", "wsgi"],
    "flask": ["flask", "app.py"],
    "stripe": ["stripe", "payment", "checkout"],
    "auth": ["auth", "login", "signup", "session", "jwt", "oauth"],
    "database": ["prisma", "drizzle", "sequelize", "typeorm", "sqlalchemy", "db/", "database"],
    "tailwind": ["tailwind", "tailwind.config"],
    "typescript": ["tsconfig", ".ts", "typescript"],
    "python": [".py", "pyproject", "requirements.txt", "setup.py"],
    "docker": ["dockerfile", "docker-compose", ".dockerignore"],
    "graphql": ["graphql", ".gql", "apollo", "schema.graphql"],
    "redis": ["redis", "ioredis", "bull"],
    "mongodb": ["mongodb", "mongoose", "mongo"],
    "postgresql": ["postgres", "pg", "postgresql"],
    "supabase": ["supabase", "@supabase"],
    "firebase": ["firebase", "firestore"],
    "aws": ["aws", "lambda", "s3", "dynamodb", "cdk"],
    "vercel": ["vercel", "vercel.json"],
    "kubernetes": ["k8s", "kubernetes", "helm", "kustomize"],
}

_FUNCTIONAL_KEYWORDS: dict[str, list[str]] = {
    "auth": ["auth/", "login", "signup", "session", "middleware/auth", "lib/auth", "src/auth"],
    "payment": ["payment/", "stripe", "checkout", "billing", "subscription"],
    "email": ["email/", "mailer", "sendgrid", "ses", "newsletter"],
    "api": ["api/", "routes/", "endpoints/", "controllers/"],
    "dashboard": ["dashboard", "admin", "panel"],
    "blog": ["blog/", "posts/", "articles/", "content/"],
    "ecommerce": ["cart", "product", "shop", "store", "order"],
    "chat": ["chat/", "message", "realtime", "socket", "websocket"],
    "upload": ["upload/", "storage", "s3", "media"],
    "notification": ["notification", "push", "alert"],
    "search": ["search/", "elasticsearch", "algolia", "meilisearch"],
    "i18n": ["i18n/", "locale", "translation", "intl"],
    "testing": ["test/", "tests/", "__tests__/", "spec/", "cypress", "playwright"],
    "ci": [".github/workflows", ".circleci", "jenkins", ".gitlab-ci"],
    "monitoring": ["monitoring", "sentry", "datadog", "prometheus"],
    "landing": ["landing", "hero", "pricing", "features"],
    "saas": ["saas", "tenant", "subscription", "billing", "pricing"],
    "cms": ["cms", "content", "editor", "wysiwyg"],
    "analytics": ["analytics", "tracking", "metrics", "posthog"],
    "deploy": ["deploy", "vercel", "docker", "kubernetes", "terraform"],
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Clamp a value between low and high."""
    return max(low, min(high, value))


def calculate_activity_score(
    commits_last_6mo: int,
    days_since_last_commit: int,
) -> float:
    """Activity score based on recent commits and recency.

    Formula: min(commits_last_6mo / 50, 1.0) * 0.7
             + max(0, 1 - days_since_last_commit / 180) * 0.3
    """
    commit_factor = min(commits_last_6mo / 50.0, 1.0)
    recency_factor = max(0.0, 1.0 - days_since_last_commit / 180.0)
    return _clamp(commit_factor * 0.7 + recency_factor * 0.3)


def calculate_popularity_score(stars: int, forks: int = 0) -> float:
    """Popularity score based on star count and fork count.

    Formula:
        star_score = clamp(log10(stars + 1) / 5, 0, 1)
        fork_score = clamp(log10(forks + 1) / 4, 0, 1)
        popularity = star_score * 0.6 + fork_score * 0.4
    """
    star_score = _clamp(math.log10(stars + 1) / 5.0)
    fork_score = _clamp(math.log10(forks + 1) / 4.0)
    return _clamp(star_score * 0.6 + fork_score * 0.4)


def calculate_maturity_score(
    has_tests: bool,
    has_ci: bool,
    has_releases: bool,
) -> float:
    """Maturity score based on project infrastructure.

    Formula: has_tests * 0.4 + has_ci * 0.3 + has_releases * 0.3
    """
    return (
        (0.4 if has_tests else 0.0)
        + (0.3 if has_ci else 0.0)
        + (0.3 if has_releases else 0.0)
    )


def calculate_documentation_score(
    readme_length: int,
    has_examples: bool,
    has_license: bool,
) -> float:
    """Documentation score based on README and project docs.

    Formula: clamp(readme_length / 5000, 0, 1) * 0.5
             + has_examples * 0.3
             + has_license * 0.2
    """
    readme_score = _clamp(readme_length / 5000.0)
    return (
        readme_score * 0.5
        + (0.3 if has_examples else 0.0)
        + (0.2 if has_license else 0.0)
    )


def _normalize_text(text: str) -> str:
    """Lowercase and strip punctuation for keyword matching."""
    return re.sub(r"[^\w\s/.\-]", "", text.lower())


def _extract_keywords(query: str) -> list[str]:
    """Extract meaningful keywords from a search query."""
    stop_words = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "can", "shall",
        "for", "and", "nor", "but", "or", "yet", "so", "at", "by",
        "in", "of", "on", "to", "up", "with", "as", "from", "into",
        "like", "boilerplate", "template", "starter", "kit", "project",
        "app", "application", "web", "open", "source",
    }
    words = _normalize_text(query).split()
    return [w for w in words if w and w not in stop_words]


def calculate_stack_score(
    repo_data: dict[str, object],
    query_keywords: list[str],
) -> float:
    """Stack compatibility score based on tech stack detection vs query keywords.

    Detects tech stack from repo language, file tree, and description,
    then matches against query keywords.

    Returns 0-1 score.
    """
    if not query_keywords:
        return 0.0

    # Collect repo signals
    language = str(repo_data.get("language", "") or "").lower()
    description = _normalize_text(str(repo_data.get("description", "") or ""))
    file_tree = repo_data.get("file_tree", [])
    if not isinstance(file_tree, list):
        file_tree = []
    tree_text = _normalize_text(" ".join(str(f) for f in file_tree))

    repo_text = f"{language} {description} {tree_text}"

    matched = 0
    total = 0

    for kw in query_keywords:
        kw_lower = kw.lower()
        # Direct match in repo text
        if kw_lower in repo_text:
            matched += 1
            total += 1
            continue

        # Check via stack keyword mapping
        stack_indicators = _STACK_KEYWORDS.get(kw_lower, [])
        if stack_indicators:
            total += 1
            for indicator in stack_indicators:
                if indicator.lower() in repo_text:
                    matched += 1
                    break
        else:
            # Keyword not in our mapping, still count it
            total += 1

    if total == 0:
        return 0.0

    return _clamp(matched / total)


def calculate_functional_fit(
    repo_data: dict[str, object],
    query_keywords: list[str],
) -> float:
    """Functional fit score based on keyword matching in description,
    README, and file tree.

    Uses rule-based matching to determine if the repo's functionality
    aligns with what the user is looking for.

    Returns 0-1 score.
    """
    if not query_keywords:
        return 0.0

    description = _normalize_text(str(repo_data.get("description", "") or ""))
    readme_content = _normalize_text(str(repo_data.get("readme_content", "") or ""))
    file_tree = repo_data.get("file_tree", [])
    if not isinstance(file_tree, list):
        file_tree = []
    tree_text = _normalize_text(" ".join(str(f) for f in file_tree))

    total_score = 0.0
    max_score = 0.0

    for kw in query_keywords:
        kw_lower = kw.lower()
        kw_score = 0.0

        # 1. Description match (weight: 0.4)
        if kw_lower in description:
            kw_score += 0.4

        # 2. README match (weight: 0.3)
        if readme_content and kw_lower in readme_content:
            kw_score += 0.3

        # 3. File tree match (weight: 0.3)
        # Check direct keyword in tree
        if kw_lower in tree_text:
            kw_score += 0.3
        else:
            # Check via functional keyword mapping
            functional_indicators = _FUNCTIONAL_KEYWORDS.get(kw_lower, [])
            for indicator in functional_indicators:
                if indicator.lower() in tree_text:
                    kw_score += 0.3
                    break

        total_score += kw_score
        max_score += 1.0  # Max possible per keyword

    if max_score == 0.0:
        return 0.0

    return _clamp(total_score / max_score)


def _determine_confidence(repo: RepoInfo) -> str:
    """Determine data confidence level for a repo.

    Returns "high", "medium", "low", or "insufficient_data".
    """
    signals = 0
    total = 5

    if repo.readme_length > 0:
        signals += 1
    if repo.commits_last_6mo > 0 or repo.has_releases:
        signals += 1
    if repo.description:
        signals += 1
    if repo.has_tests or repo.has_ci:
        signals += 1
    if len(repo.file_tree) > 0:
        signals += 1

    ratio = signals / total

    if ratio >= 0.8:
        return "high"
    if ratio >= 0.6:
        return "medium"
    if ratio >= 0.4:
        return "low"
    return "insufficient_data"


def calculate(
    repo: RepoInfo,
    reference_date: date | None = None,
    query_keywords: list[str] | None = None,
) -> QualityScore:
    """Calculate the full quality score for a repository.

    6-axis formula:
        quality_score = 0.25 * functional_fit
                      + 0.20 * activity
                      + 0.20 * popularity (with forks)
                      + 0.15 * maturity
                      + 0.10 * documentation
                      + 0.10 * stack_score

    Archived repos receive a 0.3 penalty multiplier.
    Low-confidence repos receive a 0.7 penalty multiplier.

    Args:
        repo: Repository metadata.
        reference_date: Date to calculate recency from. Defaults to today.
        query_keywords: Keywords from user query for functional fit and
            stack scoring. If None, functional_fit and stack_score are 0.

    Returns:
        QualityScore with all sub-scores and total.
    """
    if reference_date is None:
        reference_date = date.today()

    if query_keywords is None:
        query_keywords = []

    days_since = (reference_date - repo.last_commit).days
    days_since = max(0, days_since)

    activity = calculate_activity_score(repo.commits_last_6mo, days_since)
    popularity = calculate_popularity_score(repo.stars, repo.forks)
    maturity = calculate_maturity_score(repo.has_tests, repo.has_ci, repo.has_releases)
    documentation = calculate_documentation_score(
        repo.readme_length, repo.has_examples, repo.has_license
    )

    # Build repo_data dict for stack and functional fit scoring
    repo_data: dict[str, object] = {
        "language": repo.language,
        "description": repo.description or "",
        "file_tree": repo.file_tree,
        "readme_content": repo.readme_content,
    }

    stack = calculate_stack_score(repo_data, query_keywords)
    functional_fit = calculate_functional_fit(repo_data, query_keywords)

    confidence = _determine_confidence(repo)

    total = (
        0.25 * functional_fit
        + 0.20 * activity
        + 0.20 * popularity
        + 0.15 * maturity
        + 0.10 * documentation
        + 0.10 * stack
    )

    if repo.archived:
        total *= 0.3

    # Confidence penalty
    if confidence == "low":
        total *= 0.7
    elif confidence == "insufficient_data":
        total *= 0.5

    total = _clamp(total)

    return QualityScore(
        quality_score=round(total, 4),
        activity_score=round(activity, 4),
        popularity_score=round(popularity, 4),
        maturity_score=round(maturity, 4),
        documentation_score=round(documentation, 4),
        stack_score=round(stack, 4),
        functional_fit_score=round(functional_fit, 4),
        confidence=confidence,
    )

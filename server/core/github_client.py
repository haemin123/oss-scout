"""GitHub API client wrapper with rate limit protection, retry, cache, and async support.

PyGithub is synchronous, so all calls are wrapped with asyncio.to_thread().
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
from typing import TYPE_CHECKING, Any

import httpx
from github import Github, GithubException, RateLimitExceededException

if TYPE_CHECKING:
    from server.core.local_cache import LocalCache

logger = logging.getLogger("oss-scout")

_REPO_URL_PATTERN = re.compile(
    r"^https://github\.com/([\w.\-]+)/([\w.\-]+)/?$"
)

_TEST_DIRS = {"tests", "test", "__tests__", "spec"}
_CI_INDICATORS = {
    ".github/workflows", ".circleci", ".travis.yml",
    "Jenkinsfile", ".gitlab-ci.yml",
}
_EXAMPLE_DIRS = {"examples", "example", "demo", "demos"}


def _log(level: str, event: str, **kwargs: Any) -> None:
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(
        json.dumps(entry, ensure_ascii=False)
    )


def parse_repo_url(url: str) -> tuple[str, str]:
    """Extract (owner, name) from a GitHub URL.

    Raises ValueError if invalid.
    """
    m = _REPO_URL_PATTERN.match(url.rstrip("/"))
    if not m:
        raise ValueError(f"Invalid GitHub repo URL: {url}")
    return m.group(1), m.group(2)


def _cache_key(prefix: str, *parts: str) -> str:
    """Build a cache key. Hash long keys to stay under SQLite limits."""
    raw = ":".join(parts)
    if len(raw) > 100:
        raw = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"{prefix}:{raw}"


class GitHubClient:
    """Async wrapper around PyGithub with rate limit protection,
    retry, and cache."""

    SEMAPHORE_LIMIT = 10
    RATE_LIMIT_THRESHOLD = 10
    MAX_RETRIES = 3
    RETRY_BACKOFF = [1.0, 2.0, 4.0]
    JITTER_MAX = 0.5

    def __init__(
        self,
        token: str | None = None,
        cache: LocalCache | None = None,
    ) -> None:
        self._token = token or os.getenv("GITHUB_TOKEN", "")
        if not self._token:
            _log(
                "warning", "github_token_missing",
                msg="No GITHUB_TOKEN set; API rate limits will be "
                    "severely restricted",
            )
        self._github = Github(self._token, per_page=30)
        self._semaphore = asyncio.Semaphore(self.SEMAPHORE_LIMIT)
        self._rate_remaining: int | None = None
        self._cache = cache

    # --- Public API -------------------------------------------------------

    async def search_repos(
        self,
        query: str,
        language: str | None = None,
        min_stars: int = 100,
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        """Search GitHub repositories."""
        q_parts = [query, f"stars:>={min_stars}"]
        if language:
            q_parts.append(f"language:{language}")
        q = " ".join(q_parts)

        cache_key = _cache_key("search", q, str(max_results))

        def _search() -> list[dict[str, Any]]:
            results: list[dict[str, Any]] = []
            repos = self._github.search_repositories(
                q, sort="stars", order="desc",
            )
            for repo in repos[:max_results]:
                results.append({
                    "full_name": repo.full_name,
                    "url": repo.html_url,
                    "stars": repo.stargazers_count,
                    "forks": repo.forks_count,
                    "description": repo.description or "",
                    "language": repo.language,
                    "archived": repo.archived,
                    "default_branch": repo.default_branch,
                })
            self._update_rate_limit()
            return results

        return await self._execute(
            "search_repos", _search, cache_key=cache_key,
        )

    async def get_repo(self, owner: str, name: str) -> dict[str, Any]:
        """Get detailed repo metadata including quality indicators."""
        cache_key = _cache_key("repo", owner, name)

        def _get() -> dict[str, Any]:
            repo = self._github.get_repo(f"{owner}/{name}")

            try:
                contents = repo.get_contents("")
                top_level_names = (
                    {c.name for c in contents} if contents else set()
                )
            except GithubException:
                top_level_names = set()

            has_tests = bool(top_level_names & _TEST_DIRS)
            has_ci = bool(
                top_level_names & {".github", ".circleci"}
            ) or bool(
                top_level_names
                & {".travis.yml", "Jenkinsfile", ".gitlab-ci.yml"}
            )
            has_examples = bool(top_level_names & _EXAMPLE_DIRS)

            if ".github" in top_level_names and not has_ci:
                try:
                    gh_contents = repo.get_contents(".github")
                    gh_names = (
                        {c.name for c in gh_contents}
                        if gh_contents else set()
                    )
                    if "workflows" in gh_names:
                        has_ci = True
                except GithubException:
                    pass

            try:
                releases = repo.get_releases()
                has_releases = releases.totalCount > 0
            except GithubException:
                has_releases = False

            try:
                readme = repo.get_readme()
                readme_length = len(
                    readme.decoded_content.decode(
                        "utf-8", errors="replace",
                    )
                )
            except GithubException:
                readme_length = 0

            try:
                commits = repo.get_commits()
                last_commit_date = (
                    commits[0].commit.committer.date.strftime("%Y-%m-%d")
                )
            except (GithubException, IndexError):
                last_commit_date = "unknown"

            self._update_rate_limit()

            return {
                "full_name": repo.full_name,
                "url": repo.html_url,
                "stars": repo.stargazers_count,
                "forks": repo.forks_count,
                "open_issues": repo.open_issues_count,
                "last_commit": last_commit_date,
                "archived": repo.archived,
                "default_branch": repo.default_branch,
                "language": repo.language,
                "description": repo.description or "",
                "has_tests": has_tests,
                "has_ci": has_ci,
                "has_releases": has_releases,
                "has_examples": has_examples,
                "readme_length": readme_length,
            }

        return await self._execute(
            "get_repo", _get, cache_key=cache_key,
        )

    async def get_readme(self, owner: str, name: str) -> str:
        """Get README content, truncated to 4000 chars."""
        cache_key = _cache_key("readme", owner, name)

        def _get() -> str:
            repo = self._github.get_repo(f"{owner}/{name}")
            try:
                readme = repo.get_readme()
                content = readme.decoded_content.decode(
                    "utf-8", errors="replace",
                )
                self._update_rate_limit()
                return content[:4000]
            except GithubException:
                self._update_rate_limit()
                return ""

        result = await self._execute(
            "get_readme", _get, cache_key=cache_key,
        )
        if isinstance(result, dict) and "_text" in result:
            return result["_text"]
        if isinstance(result, str):
            return result
        return ""

    async def get_license(self, owner: str, name: str) -> dict[str, Any]:
        """Get license info from GitHub API."""
        cache_key = _cache_key("license", owner, name)

        def _get() -> dict[str, Any]:
            repo = self._github.get_repo(f"{owner}/{name}")
            license_info = repo.get_license()
            self._update_rate_limit()

            lic = license_info.license
            return {
                "name": lic.name if lic else "Unknown",
                "spdx_id": lic.spdx_id if lic else "NOASSERTION",
                "url": lic.url if lic else None,
                "body": (
                    license_info.decoded_content.decode(
                        "utf-8", errors="replace",
                    )
                    if license_info.content
                    else ""
                ),
            }

        try:
            return await self._execute(
                "get_license", _get,
                cache_key=cache_key, ttl_hours=72,
            )
        except GithubException:
            return {
                "name": "Unknown",
                "spdx_id": "NOASSERTION",
                "url": None,
                "body": "",
            }

    async def get_file_tree(
        self, owner: str, name: str, depth: int = 2,
    ) -> list[str]:
        """Get repository file tree."""
        cache_key = _cache_key("tree", owner, name)

        def _get() -> list[str]:
            repo = self._github.get_repo(f"{owner}/{name}")
            tree: list[str] = []

            try:
                git_tree = repo.get_git_tree(
                    repo.default_branch, recursive=True,
                )
                for item in git_tree.tree:
                    item_depth = item.path.count("/") + 1
                    if item_depth <= depth:
                        tree.append(item.path)
            except GithubException:
                pass

            self._update_rate_limit()
            return tree

        return await self._execute(
            "get_file_tree", _get, cache_key=cache_key,
        )

    async def download_tarball(
        self,
        owner: str,
        name: str,
        branch: str | None = None,
    ) -> bytes:
        """Download repository tarball using httpx (not cached)."""
        if branch is None:
            def _get_branch() -> str:
                repo = self._github.get_repo(f"{owner}/{name}")
                self._update_rate_limit()
                return repo.default_branch

            branch = await self._execute(
                "get_default_branch", _get_branch,
            )

        url = (
            f"https://api.github.com/repos/{owner}/{name}"
            f"/tarball/{branch}"
        )
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        async with self._semaphore:
            _log(
                "info", "tarball_download_start",
                owner=owner, name=name, branch=branch,
            )
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=30.0,
            ) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                _log(
                    "info", "tarball_download_complete",
                    owner=owner, name=name,
                    size_bytes=len(response.content),
                )
                return response.content

    async def get_repos_parallel(
        self, repo_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Fetch multiple repos in parallel with semaphore control."""
        tasks = []
        for repo_id in repo_ids:
            owner, name = repo_id.split("/", 1)
            tasks.append(self.get_repo(owner, name))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, dict)]

    # --- Rate Limit -------------------------------------------------------

    def _update_rate_limit(self) -> None:
        try:
            rate = self._github.get_rate_limit()
            self._rate_remaining = rate.core.remaining
            if self._rate_remaining < 50:
                _log(
                    "warning", "rate_limit_low",
                    remaining=self._rate_remaining,
                    reset=rate.core.reset.isoformat(),
                )
        except Exception:
            pass

    def _is_rate_limited(self) -> bool:
        if (
            self._rate_remaining is not None
            and self._rate_remaining < self.RATE_LIMIT_THRESHOLD
        ):
            _log(
                "warning", "rate_limit_exhausted",
                remaining=self._rate_remaining,
            )
            return True
        return False

    # --- Retry & Execution with Cache ------------------------------------

    async def _execute(
        self,
        operation_name: str,
        func: Any,
        cache_key: str | None = None,
        ttl_hours: int | None = None,
    ) -> Any:
        """Execute with semaphore, cache check, rate limit check,
        and retry."""
        async with self._semaphore:
            # 1. Check cache first
            if cache_key and self._cache:
                cached = await self._cache.get(cache_key)
                if cached is not None:
                    _log(
                        "debug", "cache_hit",
                        operation=operation_name, key=cache_key,
                    )
                    return cached

            # 2. Check rate limit
            if self._is_rate_limited():
                if cache_key and self._cache:
                    stale = await self._cache.get_stale(cache_key)
                    if stale is not None:
                        _log(
                            "warning", "rate_limit_cache_fallback",
                            operation=operation_name, key=cache_key,
                        )
                        return stale
                _log(
                    "warning", "rate_limit_skip",
                    operation=operation_name,
                    msg="Rate limit too low, no cache available",
                )
                return self._empty_result(func)

            # 3. Execute API call with retry
            result = await self._retry_with_backoff(
                operation_name, func,
            )

            # 4. Store in cache
            if cache_key and self._cache and result:
                cache_data = result
                if isinstance(result, str):
                    cache_data = {"_text": result}
                elif isinstance(result, list):
                    cache_data = {"_list": result}
                await self._cache.set(
                    cache_key, cache_data, ttl_hours=ttl_hours,
                )

            return result

    async def _retry_with_backoff(
        self, operation_name: str, func: Any,
    ) -> Any:
        """Execute with exponential backoff for 429/503 errors."""
        last_exception: Exception | None = None

        for attempt in range(self.MAX_RETRIES):
            try:
                return await asyncio.to_thread(func)
            except RateLimitExceededException as e:
                last_exception = e
                if attempt < self.MAX_RETRIES - 1:
                    delay = (
                        self.RETRY_BACKOFF[attempt]
                        + random.uniform(0, self.JITTER_MAX)
                    )
                    _log(
                        "warning", "retry_rate_limit",
                        operation=operation_name,
                        attempt=attempt + 1,
                        delay=f"{delay:.2f}s",
                    )
                    await asyncio.sleep(delay)
                else:
                    _log(
                        "error", "retry_exhausted",
                        operation=operation_name,
                        attempts=self.MAX_RETRIES,
                        error="rate_limit",
                    )
            except GithubException as e:
                last_exception = e
                status = getattr(e, "status", 0)
                if status in (429, 503) and attempt < self.MAX_RETRIES - 1:
                    delay = (
                        self.RETRY_BACKOFF[attempt]
                        + random.uniform(0, self.JITTER_MAX)
                    )
                    _log(
                        "warning", "retry_server_error",
                        operation=operation_name,
                        attempt=attempt + 1,
                        status=status,
                        delay=f"{delay:.2f}s",
                    )
                    await asyncio.sleep(delay)
                else:
                    _log(
                        "error", "github_api_error",
                        operation=operation_name,
                        status=status,
                        error=str(e)[:200],
                    )
                    raise
            except Exception as e:
                _log(
                    "error", "unexpected_error",
                    operation=operation_name,
                    error=str(e)[:200],
                )
                raise

        if last_exception:
            raise last_exception
        raise RuntimeError(
            f"Retry loop exited unexpectedly for {operation_name}"
        )

    @staticmethod
    def _empty_result(func: Any) -> Any:
        name = getattr(func, "__name__", "")
        if "search" in name:
            return []
        if "tree" in name:
            return []
        if "readme" in name:
            return ""
        return {}

"""Unit tests for server/tools/batch.py — batch parallel execution tools."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.tools.batch import (
    handle_batch_scaffold,
    handle_batch_search,
    handle_batch_validate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_github_mock() -> MagicMock:
    """Create a minimal GitHubClient mock."""
    return MagicMock()


def _parse_result(result: list) -> list | dict:
    """Parse the TextContent list returned by handlers."""
    assert len(result) == 1
    return json.loads(result[0].text)


# ---------------------------------------------------------------------------
# batch_search
# ---------------------------------------------------------------------------


class TestBatchSearch:
    """Tests for handle_batch_search."""

    @pytest.mark.asyncio
    async def test_empty_queries_returns_error(self) -> None:
        """빈 쿼리 목록은 에러를 반환해야 한다."""
        github = _make_github_mock()
        result = await handle_batch_search({"queries": []}, github)
        parsed = _parse_result(result)
        assert "error" in parsed
        assert "비어 있습니다" in parsed["error"]

    @pytest.mark.asyncio
    async def test_missing_queries_key_returns_error(self) -> None:
        """queries 키가 없으면 에러를 반환해야 한다."""
        github = _make_github_mock()
        result = await handle_batch_search({}, github)
        parsed = _parse_result(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    @patch("server.tools.batch.handle_search")
    async def test_parallel_search_multiple_queries(
        self, mock_search: AsyncMock
    ) -> None:
        """여러 쿼리가 병렬로 실행되고 라벨별로 결과가 매핑되어야 한다."""
        from mcp.types import TextContent

        mock_search.return_value = [
            TextContent(type="text", text=json.dumps([{"repo": "test/repo", "stars": 100}]))
        ]

        github = _make_github_mock()
        queries = [
            {"label": "인증", "query": "auth library", "language": "Python"},
            {"label": "결제", "query": "payment sdk", "min_stars": 200},
        ]
        result = await handle_batch_search({"queries": queries}, github)
        parsed = _parse_result(result)

        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert parsed[0]["label"] == "인증"
        assert parsed[1]["label"] == "결제"
        assert mock_search.call_count == 2

    @pytest.mark.asyncio
    @patch("server.tools.batch.handle_search")
    async def test_partial_failure_does_not_block_others(
        self, mock_search: AsyncMock
    ) -> None:
        """하나의 검색이 실패해도 나머지 결과는 정상 반환되어야 한다."""
        from mcp.types import TextContent

        async def side_effect(args: dict, github: MagicMock) -> list:
            if args.get("query") == "fail":
                raise ValueError("테스트 에러")
            return [TextContent(type="text", text=json.dumps([{"repo": "ok/repo"}]))]

        mock_search.side_effect = side_effect

        github = _make_github_mock()
        queries = [
            {"label": "성공", "query": "success"},
            {"label": "실패", "query": "fail"},
        ]
        result = await handle_batch_search({"queries": queries}, github)
        parsed = _parse_result(result)

        assert len(parsed) == 2
        # 성공한 항목
        assert parsed[0]["label"] == "성공"
        assert "error" not in parsed[0]
        # 실패한 항목
        assert parsed[1]["label"] == "실패"
        assert "error" in parsed[1]
        assert "검색 실패" in parsed[1]["error"]


# ---------------------------------------------------------------------------
# batch_validate
# ---------------------------------------------------------------------------


class TestBatchValidate:
    """Tests for handle_batch_validate."""

    @pytest.mark.asyncio
    async def test_empty_urls_returns_error(self) -> None:
        """빈 URL 목록은 에러를 반환해야 한다."""
        github = _make_github_mock()
        result = await handle_batch_validate({"repo_urls": []}, github)
        parsed = _parse_result(result)
        assert "error" in parsed
        assert "비어 있습니다" in parsed["error"]

    @pytest.mark.asyncio
    @patch("server.tools.batch.handle_validate")
    async def test_parallel_validate_multiple_repos(
        self, mock_validate: AsyncMock
    ) -> None:
        """여러 레포가 병렬로 검증되어야 한다."""
        from mcp.types import TextContent

        mock_validate.return_value = [
            TextContent(type="text", text=json.dumps({
                "repo": "test/repo",
                "overall_passed": True,
                "aggregate_score": 0.85,
            }))
        ]

        github = _make_github_mock()
        urls = [
            "https://github.com/owner/repo1",
            "https://github.com/owner/repo2",
        ]
        result = await handle_batch_validate({"repo_urls": urls}, github)
        parsed = _parse_result(result)

        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert parsed[0]["repo_url"] == urls[0]
        assert parsed[1]["repo_url"] == urls[1]
        assert mock_validate.call_count == 2

    @pytest.mark.asyncio
    @patch("server.tools.batch.handle_validate")
    async def test_invalid_url_error_captured(
        self, mock_validate: AsyncMock
    ) -> None:
        """잘못된 URL은 에러로 캡처되고 나머지는 계속 실행되어야 한다."""
        from mcp.types import TextContent

        async def side_effect(args: dict, github: MagicMock) -> list:
            if "invalid" in args.get("repo_url", ""):
                raise ValueError("잘못된 URL")
            return [TextContent(type="text", text=json.dumps({
                "repo": "ok/repo",
                "overall_passed": True,
            }))]

        mock_validate.side_effect = side_effect

        github = _make_github_mock()
        urls = [
            "https://github.com/owner/valid",
            "https://github.com/invalid/repo",
        ]
        result = await handle_batch_validate({"repo_urls": urls}, github)
        parsed = _parse_result(result)

        assert len(parsed) == 2
        assert "error" not in parsed[0]
        assert "error" in parsed[1]
        assert "검증 실패" in parsed[1]["error"]


# ---------------------------------------------------------------------------
# batch_scaffold
# ---------------------------------------------------------------------------


class TestBatchScaffold:
    """Tests for handle_batch_scaffold."""

    @pytest.mark.asyncio
    async def test_empty_repos_returns_error(self) -> None:
        """빈 레포 목록은 에러를 반환해야 한다."""
        github = _make_github_mock()
        result = await handle_batch_scaffold({"repos": []}, github)
        parsed = _parse_result(result)
        assert "error" in parsed
        assert "비어 있습니다" in parsed["error"]

    @pytest.mark.asyncio
    @patch("server.tools.batch.handle_scaffold")
    async def test_parallel_scaffold_multiple_repos(
        self, mock_scaffold: AsyncMock
    ) -> None:
        """여러 레포가 병렬로 scaffold되어야 한다."""
        from mcp.types import TextContent

        mock_scaffold.return_value = [
            TextContent(type="text", text=json.dumps({
                "status": "success",
                "files_created": 42,
            }))
        ]

        github = _make_github_mock()
        repos = [
            {"repo_url": "https://github.com/a/b", "target_dir": "./dir1"},
            {"repo_url": "https://github.com/c/d", "target_dir": "./dir2", "subdir": "src"},
        ]
        result = await handle_batch_scaffold({"repos": repos}, github)
        parsed = _parse_result(result)

        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert parsed[0]["repo_url"] == "https://github.com/a/b"
        assert parsed[1]["repo_url"] == "https://github.com/c/d"
        assert mock_scaffold.call_count == 2

    @pytest.mark.asyncio
    @patch("server.tools.batch.handle_scaffold")
    async def test_scaffold_partial_failure(
        self, mock_scaffold: AsyncMock
    ) -> None:
        """하나의 scaffold가 실패해도 나머지는 정상 실행되어야 한다."""
        from mcp.types import TextContent

        call_count = 0

        async def side_effect(args: dict, github: MagicMock) -> list:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("디스크 공간 부족")
            return [TextContent(type="text", text=json.dumps({
                "status": "success",
                "files_created": 10,
            }))]

        mock_scaffold.side_effect = side_effect

        github = _make_github_mock()
        repos = [
            {"repo_url": "https://github.com/a/b", "target_dir": "./d1"},
            {"repo_url": "https://github.com/c/d", "target_dir": "./d2"},
        ]
        result = await handle_batch_scaffold({"repos": repos}, github)
        parsed = _parse_result(result)

        assert len(parsed) == 2
        # One should succeed, one should fail (order may vary due to parallelism,
        # but gather preserves input order)
        success = [r for r in parsed if "error" not in r]
        failures = [r for r in parsed if "error" in r]
        assert len(success) == 1
        assert len(failures) == 1
        assert "스캐폴딩 실패" in failures[0]["error"]

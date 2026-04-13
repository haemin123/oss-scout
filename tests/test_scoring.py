"""Unit tests for server/core/scoring.py."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from server.core.scoring import (
    calculate,
    calculate_activity_score,
    calculate_documentation_score,
    calculate_maturity_score,
    calculate_popularity_score,
)
from server.models import RepoInfo


# --- Activity score ---


def test_activity_zero_commits_old_repo() -> None:
    score = calculate_activity_score(commits_last_6mo=0, days_since_last_commit=365)
    assert score == 0.0


def test_activity_many_commits_recent() -> None:
    score = calculate_activity_score(commits_last_6mo=100, days_since_last_commit=0)
    # min(100/50, 1.0)*0.7 + max(0, 1-0/180)*0.3 = 1.0*0.7 + 1.0*0.3 = 1.0
    assert score == pytest.approx(1.0)


def test_activity_boundary_50_commits() -> None:
    score = calculate_activity_score(commits_last_6mo=50, days_since_last_commit=0)
    # min(50/50, 1.0)*0.7 + 1.0*0.3 = 0.7 + 0.3 = 1.0
    assert score == pytest.approx(1.0)


def test_activity_25_commits_90_days() -> None:
    score = calculate_activity_score(commits_last_6mo=25, days_since_last_commit=90)
    # min(25/50, 1.0)*0.7 + max(0, 1-90/180)*0.3 = 0.5*0.7 + 0.5*0.3 = 0.35 + 0.15 = 0.5
    assert score == pytest.approx(0.5)


def test_activity_at_180_days() -> None:
    score = calculate_activity_score(commits_last_6mo=0, days_since_last_commit=180)
    # 0*0.7 + max(0, 1-180/180)*0.3 = 0 + 0 = 0
    assert score == pytest.approx(0.0)


def test_activity_recency_clamps_at_zero() -> None:
    score = calculate_activity_score(commits_last_6mo=0, days_since_last_commit=999)
    assert score == 0.0


# --- Popularity score ---


def test_popularity_zero_stars() -> None:
    score = calculate_popularity_score(0)
    # log10(1)/5 = 0/5 = 0
    assert score == pytest.approx(0.0)


def test_popularity_10_stars() -> None:
    score = calculate_popularity_score(10)
    # log10(11)/5 ≈ 1.0414/5 ≈ 0.2083
    assert score == pytest.approx(math.log10(11) / 5)


def test_popularity_100k_stars() -> None:
    score = calculate_popularity_score(100_000)
    # log10(100001)/5 ≈ 5.0/5 = 1.0
    assert score == pytest.approx(1.0, abs=0.01)


def test_popularity_1000_stars() -> None:
    score = calculate_popularity_score(1000)
    # log10(1001)/5 ≈ 3.0004/5 ≈ 0.6001
    assert score == pytest.approx(math.log10(1001) / 5)


def test_popularity_clamps_at_1() -> None:
    score = calculate_popularity_score(10_000_000)
    assert score == 1.0


# --- Maturity score ---


def test_maturity_all_present() -> None:
    score = calculate_maturity_score(has_tests=True, has_ci=True, has_releases=True)
    assert score == pytest.approx(1.0)


def test_maturity_none_present() -> None:
    score = calculate_maturity_score(has_tests=False, has_ci=False, has_releases=False)
    assert score == pytest.approx(0.0)


def test_maturity_tests_only() -> None:
    score = calculate_maturity_score(has_tests=True, has_ci=False, has_releases=False)
    assert score == pytest.approx(0.4)


def test_maturity_ci_only() -> None:
    score = calculate_maturity_score(has_tests=False, has_ci=True, has_releases=False)
    assert score == pytest.approx(0.3)


def test_maturity_releases_only() -> None:
    score = calculate_maturity_score(has_tests=False, has_ci=False, has_releases=True)
    assert score == pytest.approx(0.3)


# --- Documentation score ---


def test_documentation_all_present_long_readme() -> None:
    score = calculate_documentation_score(
        readme_length=10000, has_examples=True, has_license=True
    )
    # clamp(10000/5000, 0, 1)*0.5 + 0.3 + 0.2 = 1.0*0.5 + 0.5 = 1.0
    assert score == pytest.approx(1.0)


def test_documentation_nothing() -> None:
    score = calculate_documentation_score(
        readme_length=0, has_examples=False, has_license=False
    )
    assert score == pytest.approx(0.0)


def test_documentation_short_readme() -> None:
    score = calculate_documentation_score(
        readme_length=2500, has_examples=False, has_license=False
    )
    # clamp(2500/5000)*0.5 = 0.5*0.5 = 0.25
    assert score == pytest.approx(0.25)


def test_documentation_readme_clamps() -> None:
    score = calculate_documentation_score(
        readme_length=99999, has_examples=False, has_license=False
    )
    # clamp at 1.0 * 0.5 = 0.5
    assert score == pytest.approx(0.5)


# --- Full calculate() ---


def _make_repo(**kwargs: object) -> RepoInfo:
    """Create a RepoInfo with sensible defaults."""
    defaults: dict[str, object] = {
        "full_name": "test/repo",
        "url": "https://github.com/test/repo",
        "stars": 1000,
        "forks": 100,
        "last_commit": date(2026, 4, 1),
        "archived": False,
        "commits_last_6mo": 30,
        "has_tests": True,
        "has_ci": True,
        "has_releases": True,
        "has_examples": True,
        "has_license": True,
        "readme_length": 5000,
    }
    defaults.update(kwargs)
    return RepoInfo(**defaults)  # type: ignore[arg-type]


def test_calculate_perfect_repo() -> None:
    repo = _make_repo(
        stars=100_000,
        commits_last_6mo=100,
        last_commit=date(2026, 4, 13),
    )
    result = calculate(repo, reference_date=date(2026, 4, 13))
    # All sub-scores should be ~1.0
    assert result.activity_score == pytest.approx(1.0, abs=0.01)
    assert result.popularity_score == pytest.approx(1.0, abs=0.01)
    assert result.maturity_score == pytest.approx(1.0)
    assert result.documentation_score == pytest.approx(1.0)
    assert result.quality_score == pytest.approx(1.0, abs=0.01)


def test_calculate_archived_penalty() -> None:
    repo = _make_repo(archived=True)
    ref = date(2026, 4, 13)
    result_archived = calculate(repo, reference_date=ref)

    repo_normal = _make_repo(archived=False)
    result_normal = calculate(repo_normal, reference_date=ref)

    assert result_archived.quality_score == pytest.approx(
        result_normal.quality_score * 0.3, abs=0.001
    )


def test_calculate_zero_repo() -> None:
    repo = _make_repo(
        stars=0,
        commits_last_6mo=0,
        last_commit=date(2025, 1, 1),
        has_tests=False,
        has_ci=False,
        has_releases=False,
        has_examples=False,
        has_license=False,
        readme_length=0,
    )
    result = calculate(repo, reference_date=date(2026, 4, 13))
    assert result.quality_score == pytest.approx(0.0, abs=0.01)
    assert result.activity_score == pytest.approx(0.0, abs=0.01)
    assert result.maturity_score == pytest.approx(0.0)
    assert result.documentation_score == pytest.approx(0.0)


def test_calculate_weights_sum() -> None:
    """Verify the weight formula: 0.40 + 0.25 + 0.20 + 0.15 = 1.0."""
    repo = _make_repo(
        stars=100_000,
        commits_last_6mo=100,
        last_commit=date(2026, 4, 13),
    )
    result = calculate(repo, reference_date=date(2026, 4, 13))
    # With all sub-scores at 1.0, total should be 1.0
    expected = 0.40 + 0.25 + 0.20 + 0.15
    assert expected == pytest.approx(1.0)
    assert result.quality_score == pytest.approx(1.0, abs=0.02)


def test_calculate_uses_reference_date() -> None:
    repo = _make_repo(last_commit=date(2026, 1, 1), commits_last_6mo=0)
    # 102 days ago
    result = calculate(repo, reference_date=date(2026, 4, 13))
    # recency: max(0, 1 - 102/180) = 0.433
    expected_recency = max(0, 1 - 102 / 180.0)
    expected_activity = 0.0 * 0.7 + expected_recency * 0.3
    assert result.activity_score == pytest.approx(expected_activity, abs=0.001)


def test_calculate_future_commit_clamps_days() -> None:
    """If last_commit is in the future, days_since should be 0."""
    repo = _make_repo(last_commit=date(2026, 5, 1), commits_last_6mo=50)
    result = calculate(repo, reference_date=date(2026, 4, 13))
    # days_since = max(0, -18) = 0
    # activity = min(50/50,1)*0.7 + max(0,1-0/180)*0.3 = 0.7+0.3 = 1.0
    assert result.activity_score == pytest.approx(1.0)


def test_calculate_returns_rounded_values() -> None:
    repo = _make_repo(stars=123, commits_last_6mo=17)
    result = calculate(repo, reference_date=date(2026, 4, 13))
    # All values should be rounded to 4 decimal places
    for field in ["quality_score", "activity_score", "popularity_score", "maturity_score", "documentation_score"]:
        value = getattr(result, field)
        assert value == round(value, 4)

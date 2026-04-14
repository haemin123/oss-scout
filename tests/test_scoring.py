"""Unit tests for server/core/scoring.py."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from server.core.scoring import (
    _clamp,
    _determine_confidence,
    _extract_keywords,
    calculate,
    calculate_activity_score,
    calculate_documentation_score,
    calculate_functional_fit,
    calculate_maturity_score,
    calculate_popularity_score,
    calculate_stack_score,
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


# --- Popularity score (now with forks) ---


def test_popularity_zero_stars_zero_forks() -> None:
    score = calculate_popularity_score(0, 0)
    # star: log10(1)/5 = 0, fork: log10(1)/4 = 0
    assert score == pytest.approx(0.0)


def test_popularity_backward_compat_no_forks() -> None:
    """Calling with only stars (forks defaults to 0) should work."""
    score = calculate_popularity_score(10)
    star_part = math.log10(11) / 5 * 0.6
    fork_part = 0.0  # log10(1)/4 = 0
    assert score == pytest.approx(star_part + fork_part)


def test_popularity_10_stars_no_forks() -> None:
    score = calculate_popularity_score(10, 0)
    star_part = math.log10(11) / 5 * 0.6
    fork_part = 0.0
    assert score == pytest.approx(star_part + fork_part)


def test_popularity_100k_stars_10k_forks() -> None:
    score = calculate_popularity_score(100_000, 10_000)
    # star: log10(100001)/5 ~ 1.0, fork: log10(10001)/4 = 4.0/4 = 1.0
    # 1.0 * 0.6 + 1.0 * 0.4 = 1.0
    assert score == pytest.approx(1.0, abs=0.01)


def test_popularity_1000_stars_100_forks() -> None:
    score = calculate_popularity_score(1000, 100)
    star_part = math.log10(1001) / 5 * 0.6
    fork_part = math.log10(101) / 4 * 0.4
    assert score == pytest.approx(star_part + fork_part, abs=0.001)


def test_popularity_clamps_at_1() -> None:
    score = calculate_popularity_score(10_000_000, 1_000_000)
    assert score == 1.0


def test_popularity_forks_only() -> None:
    """High forks with zero stars should still give partial score."""
    score = calculate_popularity_score(0, 10_000)
    # star: 0, fork: log10(10001)/4 ~ 1.0
    # 0 * 0.6 + 1.0 * 0.4 = 0.4
    assert score == pytest.approx(0.4, abs=0.01)


def test_popularity_stars_only() -> None:
    """High stars with zero forks should give star-weighted score."""
    score = calculate_popularity_score(100_000, 0)
    # star: ~1.0, fork: 0
    # 1.0 * 0.6 + 0 * 0.4 = 0.6
    assert score == pytest.approx(0.6, abs=0.01)


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


# --- Stack score ---


def test_stack_score_empty_keywords() -> None:
    score = calculate_stack_score({"language": "Python"}, [])
    assert score == 0.0


def test_stack_score_direct_language_match() -> None:
    score = calculate_stack_score(
        {"language": "TypeScript", "description": "A Next.js starter", "file_tree": []},
        ["typescript"],
    )
    assert score > 0.0


def test_stack_score_file_tree_match() -> None:
    score = calculate_stack_score(
        {
            "language": "JavaScript",
            "description": "",
            "file_tree": ["next.config.js", "pages/index.tsx", "stripe.ts"],
        },
        ["next.js", "stripe"],
    )
    assert score == pytest.approx(1.0)


def test_stack_score_no_match() -> None:
    score = calculate_stack_score(
        {"language": "Python", "description": "A Flask app", "file_tree": ["app.py"]},
        ["rust", "wasm"],
    )
    assert score == pytest.approx(0.0)


def test_stack_score_partial_match() -> None:
    score = calculate_stack_score(
        {
            "language": "TypeScript",
            "description": "React dashboard",
            "file_tree": ["src/components/", "tailwind.config.js"],
        },
        ["react", "vue", "tailwind"],
    )
    # react: match, vue: no match, tailwind: match -> 2/3
    assert score == pytest.approx(2.0 / 3.0, abs=0.01)


def test_stack_score_description_match() -> None:
    score = calculate_stack_score(
        {"language": "Python", "description": "FastAPI with PostgreSQL", "file_tree": []},
        ["fastapi", "postgresql"],
    )
    assert score == pytest.approx(1.0)


# --- Functional fit score ---


def test_functional_fit_empty_keywords() -> None:
    score = calculate_functional_fit({"description": "something"}, [])
    assert score == 0.0


def test_functional_fit_description_match() -> None:
    score = calculate_functional_fit(
        {"description": "SaaS boilerplate with auth and payment", "file_tree": [], "readme_content": ""},
        ["auth", "payment"],
    )
    # Each keyword matches description (0.4) only -> 0.4 per keyword -> 0.4
    assert score == pytest.approx(0.4)


def test_functional_fit_full_match() -> None:
    score = calculate_functional_fit(
        {
            "description": "Next.js SaaS with auth",
            "readme_content": "Includes auth module and payment integration",
            "file_tree": ["src/auth/", "src/payment/"],
        },
        ["auth", "payment"],
    )
    # auth: desc(0.4) + readme(0.3) + tree(0.3) = 1.0
    # payment: desc(no match, "payment" in desc) + readme(0.3) + tree(0.3)
    # payment in description? "with auth" -> no. But "payment" is in readme.
    # Actually desc = "next.js saas with auth" -> no "payment" in description
    # So payment: 0 + 0.3 + 0.3 = 0.6
    # auth: 0.4 + 0.3 + 0.3 = 1.0
    # total: (1.0 + 0.6) / 2 = 0.8
    assert score >= 0.7


def test_functional_fit_tree_only_match() -> None:
    score = calculate_functional_fit(
        {
            "description": "",
            "readme_content": "",
            "file_tree": ["src/auth/login.ts", "src/auth/middleware.ts"],
        },
        ["auth"],
    )
    # auth: desc(0) + readme(0) + tree(0.3) = 0.3
    assert score == pytest.approx(0.3)


def test_functional_fit_no_match() -> None:
    score = calculate_functional_fit(
        {"description": "A blog engine", "readme_content": "Simple blog", "file_tree": ["posts/"]},
        ["payment", "stripe"],
    )
    assert score == pytest.approx(0.0)


def test_functional_fit_functional_keyword_mapping() -> None:
    """Test that functional keyword mapping works for file tree detection."""
    score = calculate_functional_fit(
        {
            "description": "",
            "readme_content": "",
            "file_tree": ["src/auth/login.ts", "lib/auth/session.ts", "middleware/auth.ts"],
        },
        ["auth"],
    )
    # "auth" directly in tree text -> 0.3
    assert score >= 0.3


# --- Confidence ---


def test_confidence_high() -> None:
    repo = _make_repo(
        readme_length=5000,
        commits_last_6mo=30,
        description="A great project",
        has_tests=True,
        file_tree=["src/", "tests/"],
    )
    assert _determine_confidence(repo) == "high"


def test_confidence_medium() -> None:
    repo = _make_repo(
        readme_length=5000,
        commits_last_6mo=30,
        description="A project",
        has_tests=False,
        has_ci=False,
        file_tree=[],
    )
    assert _determine_confidence(repo) == "medium"


def test_confidence_low() -> None:
    repo = _make_repo(
        readme_length=0,
        commits_last_6mo=30,
        description="",
        has_tests=False,
        has_ci=False,
        file_tree=["src/"],
    )
    assert _determine_confidence(repo) == "low"


def test_confidence_insufficient_data() -> None:
    repo = _make_repo(
        readme_length=0,
        commits_last_6mo=0,
        has_releases=False,
        description="",
        has_tests=False,
        has_ci=False,
        file_tree=[],
    )
    assert _determine_confidence(repo) == "insufficient_data"


# --- Extract keywords ---


def test_extract_keywords_basic() -> None:
    keywords = _extract_keywords("Next.js Stripe SaaS boilerplate")
    assert "next.js" in keywords
    assert "stripe" in keywords
    assert "saas" in keywords
    assert "boilerplate" not in keywords  # stop word


def test_extract_keywords_removes_stop_words() -> None:
    keywords = _extract_keywords("a template for web application with auth")
    assert "a" not in keywords
    assert "template" not in keywords
    assert "for" not in keywords
    assert "auth" in keywords


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
        "description": "A great project",
        "file_tree": ["src/", "tests/", "README.md"],
        "readme_content": "",
    }
    defaults.update(kwargs)
    return RepoInfo(**defaults)  # type: ignore[arg-type]


def test_calculate_high_quality_repo() -> None:
    """A repo with high scores across all axes should score high."""
    repo = _make_repo(
        stars=100_000,
        forks=10_000,
        commits_last_6mo=100,
        last_commit=date(2026, 4, 13),
        description="Next.js SaaS boilerplate with auth",
        file_tree=["src/", "tests/", "src/auth/", "next.config.js"],
        readme_content="Complete SaaS starter with authentication",
    )
    result = calculate(
        repo,
        reference_date=date(2026, 4, 13),
        query_keywords=["next.js", "auth", "saas"],
    )
    assert result.activity_score == pytest.approx(1.0, abs=0.01)
    assert result.popularity_score == pytest.approx(1.0, abs=0.01)
    assert result.maturity_score == pytest.approx(1.0)
    assert result.documentation_score == pytest.approx(1.0)
    assert result.quality_score > 0.8


def test_calculate_no_query_keywords() -> None:
    """Without query keywords, functional_fit and stack_score should be 0."""
    repo = _make_repo(
        stars=100_000,
        forks=10_000,
        commits_last_6mo=100,
        last_commit=date(2026, 4, 13),
    )
    result = calculate(repo, reference_date=date(2026, 4, 13))
    assert result.functional_fit_score == 0.0
    assert result.stack_score == 0.0
    # Without functional_fit and stack, max is 0.20+0.20+0.15+0.10 = 0.65
    assert result.quality_score <= 0.66


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
        forks=0,
        commits_last_6mo=0,
        last_commit=date(2025, 1, 1),
        has_tests=False,
        has_ci=False,
        has_releases=False,
        has_examples=False,
        has_license=False,
        readme_length=0,
        description="",
        file_tree=[],
        readme_content="",
    )
    result = calculate(repo, reference_date=date(2026, 4, 13))
    assert result.quality_score == pytest.approx(0.0, abs=0.01)
    assert result.activity_score == pytest.approx(0.0, abs=0.01)
    assert result.maturity_score == pytest.approx(0.0)
    assert result.documentation_score == pytest.approx(0.0)


def test_calculate_weights_sum() -> None:
    """Verify the weight formula: 0.25 + 0.20 + 0.20 + 0.15 + 0.10 + 0.10 = 1.0."""
    expected = 0.25 + 0.20 + 0.20 + 0.15 + 0.10 + 0.10
    assert expected == pytest.approx(1.0)


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
    for field in [
        "quality_score", "activity_score", "popularity_score",
        "maturity_score", "documentation_score", "stack_score",
        "functional_fit_score",
    ]:
        value = getattr(result, field)
        assert value == round(value, 4)


def test_calculate_returns_confidence() -> None:
    repo = _make_repo()
    result = calculate(repo, reference_date=date(2026, 4, 13))
    assert result.confidence in ("high", "medium", "low", "insufficient_data")


def test_calculate_confidence_penalty_low() -> None:
    """Low confidence should apply 0.7 penalty."""
    repo = _make_repo(
        readme_length=0,
        commits_last_6mo=30,
        description="",
        has_tests=False,
        has_ci=False,
        file_tree=["src/"],
    )
    ref = date(2026, 4, 13)
    result = calculate(repo, reference_date=ref)
    assert result.confidence == "low"
    # Score should be lower due to penalty
    repo_high = _make_repo(
        readme_length=5000,
        commits_last_6mo=30,
        description="Good project",
        has_tests=True,
        has_ci=True,
        file_tree=["src/", "tests/"],
    )
    result_high = calculate(repo_high, reference_date=ref)
    assert result_high.confidence == "high"
    # The low-confidence repo should have penalty applied
    assert result.quality_score < result_high.quality_score


def test_calculate_confidence_penalty_insufficient() -> None:
    """Insufficient data should apply 0.5 penalty."""
    repo = _make_repo(
        readme_length=0,
        commits_last_6mo=0,
        has_releases=False,
        description="",
        has_tests=False,
        has_ci=False,
        file_tree=[],
    )
    result = calculate(repo, reference_date=date(2026, 4, 13))
    assert result.confidence == "insufficient_data"


def test_calculate_with_query_keywords() -> None:
    """Query keywords should produce non-zero functional_fit and stack_score."""
    repo = _make_repo(
        description="Next.js SaaS with authentication",
        language="TypeScript",
        file_tree=["next.config.js", "src/auth/", "src/components/"],
        readme_content="A complete SaaS starter with auth and billing",
    )
    result = calculate(
        repo,
        reference_date=date(2026, 4, 13),
        query_keywords=["next.js", "auth"],
    )
    assert result.functional_fit_score > 0.0
    assert result.stack_score > 0.0


def test_calculate_popularity_includes_forks() -> None:
    """Repos with many forks should score higher on popularity."""
    repo_no_forks = _make_repo(stars=1000, forks=0)
    repo_with_forks = _make_repo(stars=1000, forks=5000)
    ref = date(2026, 4, 13)
    result_no = calculate(repo_no_forks, reference_date=ref)
    result_with = calculate(repo_with_forks, reference_date=ref)
    assert result_with.popularity_score > result_no.popularity_score


# --- Clamp helper ---


def test_clamp_within_range() -> None:
    assert _clamp(0.5) == 0.5


def test_clamp_below() -> None:
    assert _clamp(-1.0) == 0.0


def test_clamp_above() -> None:
    assert _clamp(2.0) == 1.0

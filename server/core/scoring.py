"""Quality scoring engine for OSS Scout.

Implements the scoring formula from ossmaker.md §5.
All sub-scores are normalized to 0-1 range.
"""

from __future__ import annotations

import math
from datetime import date

from server.models import QualityScore, RepoInfo


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


def calculate_popularity_score(stars: int) -> float:
    """Popularity score based on star count.

    Formula: clamp(log10(stars + 1) / 5, 0, 1)
    """
    return _clamp(math.log10(stars + 1) / 5.0)


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


def calculate(repo: RepoInfo, reference_date: date | None = None) -> QualityScore:
    """Calculate the full quality score for a repository.

    Formula:
        quality_score = 0.40 * activity + 0.25 * popularity
                      + 0.20 * maturity + 0.15 * documentation

    Archived repos receive a 0.3 penalty multiplier.

    Args:
        repo: Repository metadata.
        reference_date: Date to calculate recency from. Defaults to today.

    Returns:
        QualityScore with all sub-scores and total.
    """
    if reference_date is None:
        reference_date = date.today()

    days_since = (reference_date - repo.last_commit).days
    days_since = max(0, days_since)

    activity = calculate_activity_score(repo.commits_last_6mo, days_since)
    popularity = calculate_popularity_score(repo.stars)
    maturity = calculate_maturity_score(repo.has_tests, repo.has_ci, repo.has_releases)
    documentation = calculate_documentation_score(
        repo.readme_length, repo.has_examples, repo.has_license
    )

    total = (
        0.40 * activity
        + 0.25 * popularity
        + 0.20 * maturity
        + 0.15 * documentation
    )

    if repo.archived:
        total *= 0.3

    total = _clamp(total)

    return QualityScore(
        quality_score=round(total, 4),
        activity_score=round(activity, 4),
        popularity_score=round(popularity, 4),
        maturity_score=round(maturity, 4),
        documentation_score=round(documentation, 4),
    )

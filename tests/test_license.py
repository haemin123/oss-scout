"""Unit tests for server/core/license_check.py."""

from __future__ import annotations

import pytest

from server.core.license_check import (
    check_license,
    is_license_acceptable,
    reset_policy_cache,
)
from server.models import LicenseCategory


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Reset policy cache before each test."""
    reset_policy_cache()


# --- Whitelist (permissive) licenses ---


@pytest.mark.parametrize(
    "spdx",
    ["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Unlicense", "0BSD"],
)
def test_whitelist_licenses_are_permissive(spdx: str) -> None:
    result = check_license(spdx)
    assert result.category == LicenseCategory.PERMISSIVE
    assert result.recommended is True
    assert result.commercial_use_ok is True
    assert result.warnings == []
    assert result.spdx_id == spdx


# --- Warn (copyleft) licenses ---


@pytest.mark.parametrize(
    "spdx",
    ["GPL-3.0", "GPL-2.0", "LGPL-3.0", "LGPL-2.1", "AGPL-3.0", "MPL-2.0"],
)
def test_warn_licenses_are_copyleft(spdx: str) -> None:
    result = check_license(spdx)
    assert result.category == LicenseCategory.COPYLEFT
    assert result.recommended is False
    assert result.commercial_use_ok is True
    assert len(result.warnings) == 1
    assert spdx in result.warnings[0]


# --- Block licenses ---


def test_noassertion_is_unknown() -> None:
    result = check_license("NOASSERTION")
    assert result.category == LicenseCategory.UNKNOWN
    assert result.recommended is False
    assert result.commercial_use_ok is False
    assert len(result.warnings) == 1


def test_other_is_unknown() -> None:
    result = check_license("other")
    assert result.category == LicenseCategory.UNKNOWN
    assert result.recommended is False
    assert result.commercial_use_ok is False


# --- None / empty ---


def test_none_license() -> None:
    result = check_license(None)
    assert result.category == LicenseCategory.NONE
    assert result.recommended is False
    assert result.commercial_use_ok is False
    assert result.spdx_id == ""
    assert result.license == "none"
    assert len(result.warnings) == 1


def test_empty_string_license() -> None:
    result = check_license("")
    assert result.category == LicenseCategory.NONE
    assert result.recommended is False


def test_whitespace_only_license() -> None:
    result = check_license("   ")
    assert result.category == LicenseCategory.NONE


# --- Unknown license not in any list ---


def test_unknown_license_not_in_policy() -> None:
    result = check_license("WTFPL")
    assert result.category == LicenseCategory.UNKNOWN
    assert result.recommended is False
    assert result.commercial_use_ok is False
    assert "정책에 정의되지 않았습니다" in result.warnings[0]


# --- Whitespace handling ---


def test_license_with_whitespace_is_trimmed() -> None:
    result = check_license("  MIT  ")
    assert result.category == LicenseCategory.PERMISSIVE
    assert result.spdx_id == "MIT"


# --- is_license_acceptable ---


def test_acceptable_permissive() -> None:
    assert is_license_acceptable("MIT") is True


def test_acceptable_copyleft_denied() -> None:
    assert is_license_acceptable("GPL-3.0") is False


def test_acceptable_copyleft_allowed() -> None:
    assert is_license_acceptable("GPL-3.0", allow_copyleft=True) is True


def test_acceptable_none() -> None:
    assert is_license_acceptable(None) is False


def test_acceptable_unknown() -> None:
    assert is_license_acceptable("NOASSERTION") is False


def test_acceptable_unknown_with_copyleft() -> None:
    # Unknown licenses are never acceptable, even with allow_copyleft
    assert is_license_acceptable("NOASSERTION", allow_copyleft=True) is False

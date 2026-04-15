"""License policy enforcement for OSS Scout.

Loads license_policy.yaml and classifies licenses into
permissive, copyleft, unknown, or none categories.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from server.models import LicenseCategory, LicenseResult

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_POLICY_PATH = _PROJECT_ROOT / "config" / "license_policy.yaml"

_policy_cache: dict[str, list[str]] | None = None


def _load_policy() -> dict[str, list[str]]:
    """Load and cache the license policy YAML."""
    global _policy_cache  # noqa: PLW0603
    if _policy_cache is not None:
        return _policy_cache

    with open(_POLICY_PATH, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    _policy_cache = {
        "whitelist": [str(v) for v in raw.get("whitelist", [])],
        "warn": [str(v) for v in raw.get("warn", [])],
        "block": [str(v) for v in (raw.get("block", []) or []) if v is not None],
    }
    return _policy_cache


def check_license(
    license_spdx: str | None,
    allow_copyleft: bool = False,
) -> LicenseResult:
    """Classify a license SPDX ID against the policy.

    Args:
        license_spdx: SPDX license identifier (e.g. "MIT"), or None if unknown.
        allow_copyleft: If True, copyleft licenses are included with warnings.

    Returns:
        LicenseResult with category, recommendation, and warnings.
    """
    policy = _load_policy()

    if license_spdx is None or license_spdx.strip() == "":
        return LicenseResult(
            license="none",
            spdx_id="",
            category=LicenseCategory.NONE,
            commercial_use_ok=False,
            recommended=False,
            warnings=["라이선스가 감지되지 않았습니다. 법률 검토가 필요합니다."],
        )

    spdx = license_spdx.strip()

    # Check whitelist (permissive)
    if spdx in policy["whitelist"]:
        return LicenseResult(
            license=spdx,
            spdx_id=spdx,
            category=LicenseCategory.PERMISSIVE,
            commercial_use_ok=True,
            recommended=True,
            warnings=[],
        )

    # Check warn list (copyleft)
    if spdx in policy["warn"]:
        warnings = [
            f"이 레포는 {spdx} 라이선스입니다. "
            "상업적 사용 시 파생 코드 공개 의무가 있습니다. "
            "법률 검토 없이 프로덕션에 사용하지 마세요.",
        ]
        return LicenseResult(
            license=spdx,
            spdx_id=spdx,
            category=LicenseCategory.COPYLEFT,
            commercial_use_ok=True,
            recommended=False,
            warnings=warnings,
        )

    # Check block list or unknown
    if spdx in policy["block"] or spdx in ("NOASSERTION", "other"):
        return LicenseResult(
            license=spdx,
            spdx_id=spdx,
            category=LicenseCategory.UNKNOWN,
            commercial_use_ok=False,
            recommended=False,
            warnings=[
                f"라이선스 '{spdx}'은(는) 불명확합니다. 법률 검토가 필요합니다.",
            ],
        )

    # Not in any list — treat as unknown
    return LicenseResult(
        license=spdx,
        spdx_id=spdx,
        category=LicenseCategory.UNKNOWN,
        commercial_use_ok=False,
        recommended=False,
        warnings=[
            f"라이선스 '{spdx}'은(는) 정책에 정의되지 않았습니다. 법률 검토가 필요합니다.",
        ],
    )


def is_license_acceptable(
    license_spdx: str | None,
    allow_copyleft: bool = False,
) -> bool:
    """Check if a license passes the filter for search results.

    Returns True if the license should be included in results.
    """
    result = check_license(license_spdx, allow_copyleft)

    if result.category == LicenseCategory.PERMISSIVE:
        return True
    if result.category == LicenseCategory.COPYLEFT:
        return allow_copyleft
    return False


def reset_policy_cache() -> None:
    """Reset the cached policy. Used in tests."""
    global _policy_cache  # noqa: PLW0603
    _policy_cache = None

"""merge_repos MCP tool.

Merges code from an external GitHub repository into the current project.
Handles dependency integration and file copying with conflict detection.

SECURITY-CRITICAL: This tool writes to the filesystem.
All inputs are validated against path traversal.
"""

from __future__ import annotations

import fnmatch
import io
import json
import logging
import re
import shutil
import tarfile
from pathlib import Path
from typing import Any

from mcp.types import TextContent, Tool

from server.core.github_client import GitHubClient, parse_repo_url

logger = logging.getLogger("oss-scout")

# Security limits (same as scaffold)
MAX_FILES = 10_000
MAX_TOTAL_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_TARBALL_SIZE = 100 * 1024 * 1024  # 100 MB

_REPO_URL_PATTERN = re.compile(
    r"^https://github\.com/[\w.\-]+/[\w.\-]+/?$"
)


class MergeSecurityError(Exception):
    """Raised when a security check fails during merge."""


def _log(level: str, event: str, **kwargs: Any) -> None:
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(json.dumps(entry, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

MERGE_REPOS_TOOL = Tool(
    name="merge_repos",
    description=(
        "외부 레포의 코드를 현재 프로젝트에 머지합니다. "
        "의존성 통합과 import 경로 수정을 포함합니다."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": "베이스 프로젝트 디렉토리",
            },
            "source_repo": {
                "type": "string",
                "description": "머지할 GitHub 레포 URL",
            },
            "source_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "가져올 파일/디렉토리 glob 패턴",
            },
            "target_path": {
                "type": "string",
                "description": "프로젝트 내 대상 경로 (예: src/components/chat/)",
            },
        },
        "required": ["project_dir", "source_repo", "target_path"],
    },
)


# ---------------------------------------------------------------------------
# Core logic (pure functions for testability)
# ---------------------------------------------------------------------------

def _validate_path_no_traversal(path_str: str, label: str) -> None:
    """Validate that a path does not contain traversal sequences."""
    if ".." in Path(path_str).parts:
        raise MergeSecurityError(
            f"보안 검증 실패: {label}에 '..' 경로를 사용할 수 없습니다."
        )


def merge_package_json(
    base_data: dict[str, Any],
    source_data: dict[str, Any],
) -> dict[str, Any]:
    """Merge source package.json dependencies into base.

    Returns a dict with:
      - added_dependencies: new deps added to base
      - version_conflicts: deps with differing versions
      - merged_data: the updated base package.json data
    """
    added: dict[str, str] = {}
    conflicts: list[dict[str, str]] = []

    base_deps = base_data.get("dependencies", {})
    source_deps = source_data.get("dependencies", {})

    for pkg, version in source_deps.items():
        if pkg in base_deps:
            if base_deps[pkg] != version:
                conflicts.append({
                    "package": pkg,
                    "base_version": base_deps[pkg],
                    "source_version": version,
                })
        else:
            added[pkg] = version
            base_deps[pkg] = version

    # Also check devDependencies
    base_dev = base_data.get("devDependencies", {})
    source_dev = source_data.get("devDependencies", {})

    for pkg, version in source_dev.items():
        if pkg in base_dev:
            if base_dev[pkg] != version:
                conflicts.append({
                    "package": pkg,
                    "base_version": base_dev[pkg],
                    "source_version": version,
                })
        elif pkg not in base_deps:  # Don't add to devDeps if already in deps
            added[pkg] = version
            base_dev[pkg] = version

    merged = {**base_data}
    if base_deps:
        merged["dependencies"] = dict(sorted(base_deps.items()))
    if base_dev:
        merged["devDependencies"] = dict(sorted(base_dev.items()))

    return {
        "added_dependencies": added,
        "version_conflicts": conflicts,
        "merged_data": merged,
    }


def _filter_tarball_members(
    members: list[tarfile.TarInfo],
    source_paths: list[str] | None,
    prefix: str,
) -> list[tarfile.TarInfo]:
    """Filter tarball members by source_paths glob patterns.

    If source_paths is None or empty, all files are included.
    """
    if not source_paths:
        return members

    filtered: list[tarfile.TarInfo] = []
    for member in members:
        # Get path relative to the GitHub prefix
        if not member.name.startswith(prefix):
            continue
        rel_path = member.name[len(prefix):]
        if not rel_path:
            continue

        for pattern in source_paths:
            if fnmatch.fnmatch(rel_path, pattern):
                filtered.append(member)
                break
            # Directory prefix match
            if pattern.endswith("/**") and rel_path.startswith(pattern[:-3]):
                filtered.append(member)
                break
            # Exact prefix match for directories
            if rel_path.startswith(pattern.rstrip("/") + "/"):
                filtered.append(member)
                break

    return filtered


def _safe_extract_to_target(
    tarball_bytes: bytes,
    target_dir: Path,
    source_paths: list[str] | None = None,
) -> list[str]:
    """Safely extract tarball files to target directory.

    Returns list of extracted file paths (relative to target_dir).

    SECURITY: Validates all paths, rejects symlinks and traversals.
    """
    if len(tarball_bytes) > MAX_TARBALL_SIZE:
        raise MergeSecurityError(
            f"보안 검증 실패: tarball 크기가 {MAX_TARBALL_SIZE // (1024 * 1024)}MB를 초과합니다."
        )

    target_resolved = target_dir.resolve()
    extracted: list[str] = []
    file_count = 0
    total_size = 0

    with tarfile.open(fileobj=io.BytesIO(tarball_bytes), mode="r:gz") as tar:
        all_members = tar.getmembers()

        # Validate all members first
        for member in all_members:
            if member.issym() or member.islnk():
                raise MergeSecurityError(
                    f"보안 검증 실패: symlink가 포함되어 있습니다 ({member.name})"
                )
            if member.name.startswith("/") or member.name.startswith("\\"):
                raise MergeSecurityError(
                    f"보안 검증 실패: 절대 경로가 포함되어 있습니다 ({member.name})"
                )
            if ".." in Path(member.name).parts:
                raise MergeSecurityError(
                    f"보안 검증 실패: 경로 탈출 시도가 감지되었습니다 ({member.name})"
                )

        # Find GitHub prefix
        top_dirs = {m.name.split("/")[0] for m in all_members if "/" in m.name}
        prefix = ""
        if len(top_dirs) == 1:
            prefix = top_dirs.pop() + "/"

        # Filter by source_paths
        members_to_extract = _filter_tarball_members(all_members, source_paths, prefix)

        for member in members_to_extract:
            if not member.name.startswith(prefix):
                continue
            rel_path = member.name[len(prefix):]
            if not rel_path:
                continue

            target_path = (target_resolved / rel_path).resolve()
            if not target_path.is_relative_to(target_resolved):
                raise MergeSecurityError(
                    f"보안 검증 실패: 경로 탈출 시도가 감지되었습니다 ({rel_path})"
                )

            if member.isfile():
                file_count += 1
                total_size += member.size
                if file_count > MAX_FILES:
                    raise MergeSecurityError(
                        f"보안 검증 실패: 파일 수가 {MAX_FILES:,}개를 초과합니다."
                    )
                if total_size > MAX_TOTAL_SIZE:
                    raise MergeSecurityError(
                        f"보안 검증 실패: 총 크기가 "
                        f"{MAX_TOTAL_SIZE // (1024 * 1024)}MB를 초과합니다."
                    )

                target_path.parent.mkdir(parents=True, exist_ok=True)
                fileobj = tar.extractfile(member)
                if fileobj:
                    with open(target_path, "wb") as f:
                        shutil.copyfileobj(fileobj, f)
                    extracted.append(rel_path)

            elif member.isdir():
                target_path.mkdir(parents=True, exist_ok=True)

    return sorted(extracted)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

async def handle_merge_repos(
    arguments: dict[str, Any],
    github: GitHubClient,
) -> list[TextContent]:
    """Execute the merge_repos pipeline.

    1. Validate inputs
    2. Download source repo tarball
    3. Extract matching files to target path
    4. Merge package.json dependencies
    5. Return result
    """
    # Validate inputs
    project_dir_str = arguments.get("project_dir", "")
    if not isinstance(project_dir_str, str) or not project_dir_str.strip():
        raise ValueError("project_dir를 입력해주세요.")

    source_repo = arguments.get("source_repo", "")
    if not isinstance(source_repo, str) or not source_repo.strip():
        raise ValueError("source_repo를 입력해주세요.")
    source_repo = source_repo.strip().rstrip("/")
    if not _REPO_URL_PATTERN.match(source_repo):
        raise ValueError(
            "source_repo는 https://github.com/{owner}/{repo} 형식이어야 합니다."
        )

    target_path_str = arguments.get("target_path", "")
    if not isinstance(target_path_str, str) or not target_path_str.strip():
        raise ValueError("target_path를 입력해주세요.")
    _validate_path_no_traversal(target_path_str, "target_path")

    source_paths = arguments.get("source_paths")
    if source_paths is not None:
        if not isinstance(source_paths, list):
            raise ValueError("source_paths는 배열이어야 합니다.")
        for sp in source_paths:
            if not isinstance(sp, str):
                raise ValueError("source_paths 항목은 문자열이어야 합니다.")
            _validate_path_no_traversal(sp, "source_paths")

    owner, name = parse_repo_url(source_repo)
    project_dir = Path(project_dir_str).resolve()
    if not project_dir.exists():
        raise ValueError(f"프로젝트 디렉토리가 존재하지 않습니다: {project_dir_str}")

    target_dir = (project_dir / target_path_str).resolve()
    if not target_dir.is_relative_to(project_dir):
        raise MergeSecurityError(
            "보안 검증 실패: target_path가 프로젝트 디렉토리 외부를 가리킵니다."
        )

    _log("info", "merge_repos_start",
         project=project_dir_str,
         source=f"{owner}/{name}",
         target=target_path_str)

    # Download tarball
    tarball_bytes = await github.download_tarball(owner, name)

    # Extract files
    target_dir.mkdir(parents=True, exist_ok=True)
    extracted_files = _safe_extract_to_target(tarball_bytes, target_dir, source_paths)

    # Prefix extracted files with target_path for display
    display_files = [
        f"{target_path_str.rstrip('/')}/{f}" for f in extracted_files
    ]

    # Merge package.json if source has one
    added_deps: dict[str, str] = {}
    version_conflicts: list[dict[str, str]] = []
    package_json_updated = False

    source_pkg_path = target_dir / "package.json"
    base_pkg_path = project_dir / "package.json"

    if source_pkg_path.exists() and base_pkg_path.exists():
        try:
            source_pkg = json.loads(source_pkg_path.read_text(encoding="utf-8"))
            base_pkg = json.loads(base_pkg_path.read_text(encoding="utf-8"))

            merge_result = merge_package_json(base_pkg, source_pkg)
            added_deps = merge_result["added_dependencies"]
            version_conflicts = merge_result["version_conflicts"]

            if added_deps:
                base_pkg_path.write_text(
                    json.dumps(merge_result["merged_data"], indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                package_json_updated = True

        except (json.JSONDecodeError, OSError) as e:
            _log("warning", "package_json_merge_failed", error=str(e)[:200])

    _log("info", "merge_repos_complete",
         source=f"{owner}/{name}",
         files=len(extracted_files),
         deps_added=len(added_deps))

    result = {
        "merged_files": display_files,
        "added_dependencies": added_deps,
        "version_conflicts": version_conflicts,
        "package_json_updated": package_json_updated,
        "total_files": len(extracted_files),
    }

    return [TextContent(
        type="text",
        text=json.dumps(result, ensure_ascii=False, indent=2),
    )]

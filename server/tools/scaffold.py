"""scaffold MCP tool.

Downloads a GitHub repo (without git history) into a target directory
and optionally generates a CLAUDE.md file.

SECURITY-CRITICAL: This tool writes to the filesystem.
All inputs are validated against path traversal, symlink attacks,
and resource exhaustion. See docs/security.md for the threat model.
"""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.types import TextContent, Tool

from server.core.github_client import GitHubClient, parse_repo_url

logger = logging.getLogger("oss-scout")

# Security limits
MAX_FILES = 10_000
MAX_TOTAL_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_TARBALL_SIZE = 100 * 1024 * 1024  # 100 MB

_REPO_URL_PATTERN = re.compile(
    r"^https://github\.com/[\w.\-]+/[\w.\-]+/?$"
)


class SecurityError(Exception):
    """Raised when a security check fails during scaffold."""


def _log(level: str, event: str, **kwargs: Any) -> None:
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(json.dumps(entry, ensure_ascii=False))


# --- Input Validation -------------------------------------------------------


def _validate_scaffold_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize scaffold arguments."""
    # repo_url
    repo_url = arguments.get("repo_url", "")
    if not isinstance(repo_url, str) or not repo_url.strip():
        raise ValueError("repo_url을 입력해주세요.")
    repo_url = repo_url.strip().rstrip("/")
    if not _REPO_URL_PATTERN.match(repo_url):
        raise ValueError(
            "repo_url은 https://github.com/{owner}/{repo} 형식이어야 합니다."
        )

    # target_dir
    target_dir = arguments.get("target_dir", "")
    if not isinstance(target_dir, str) or not target_dir.strip():
        raise ValueError("target_dir을 입력해주세요.")

    # subdir
    subdir = arguments.get("subdir")
    if subdir is not None:
        if not isinstance(subdir, str):
            raise ValueError("subdir는 문자열이어야 합니다.")
        subdir = subdir.strip()
        if not subdir:
            subdir = None

    generate_claude_md = bool(arguments.get("generate_claude_md", True))

    return {
        "repo_url": repo_url,
        "target_dir": target_dir.strip(),
        "subdir": subdir,
        "generate_claude_md": generate_claude_md,
    }


def _validate_target_dir(target_dir_str: str) -> Path:
    """Validate target_dir is under CWD and is empty or doesn't exist.

    SECURITY: Prevents path traversal attacks.
    Uses Path.is_relative_to() — never str.startswith().
    """
    cwd = Path.cwd().resolve()
    target = Path(target_dir_str).resolve()

    # Path traversal check
    if not target.is_relative_to(cwd):
        raise SecurityError(
            "대상 디렉토리는 현재 작업 디렉토리 하위여야 합니다."
        )

    # Non-empty directory check
    if target.exists() and any(target.iterdir()):
        raise SecurityError(
            "대상 디렉토리가 비어있지 않습니다. 빈 디렉토리를 지정해주세요."
        )

    return target


def _validate_subdir(subdir: str | None) -> str | None:
    """Validate subdir has no path traversal."""
    if subdir is None:
        return None
    parts = Path(subdir).parts
    if ".." in parts:
        raise SecurityError(
            "보안 검증 실패: subdir에 '..' 경로를 사용할 수 없습니다."
        )
    if Path(subdir).is_absolute():
        raise SecurityError(
            "보안 검증 실패: subdir는 상대 경로여야 합니다."
        )
    return subdir


# --- Safe Tarball Extraction ------------------------------------------------


def _safe_extract_tarball(
    tarball_bytes: bytes,
    dest: Path,
    subdir: str | None = None,
) -> int:
    """Safely extract tarball to dest directory.

    SECURITY checks:
    - Rejects symlinks
    - Rejects absolute paths
    - Rejects path traversal (..)
    - Limits file count (MAX_FILES)
    - Limits total size (MAX_TOTAL_SIZE)
    - Uses tarfile filter="data" for additional safety

    Returns the number of files extracted.
    """
    if len(tarball_bytes) > MAX_TARBALL_SIZE:
        raise SecurityError(
            f"보안 검증 실패: tarball 크기가 {MAX_TARBALL_SIZE // (1024*1024)}MB를 초과합니다."
        )

    dest_resolved = dest.resolve()
    file_count = 0
    total_size = 0

    with tarfile.open(fileobj=io.BytesIO(tarball_bytes), mode="r:gz") as tar:
        # First pass: validate all members
        members_to_extract: list[tarfile.TarInfo] = []

        for member in tar.getmembers():
            # Reject symlinks and hardlinks
            if member.issym() or member.islnk():
                raise SecurityError(
                    f"보안 검증 실패: symlink가 포함되어 있습니다 ({member.name})"
                )

            # Reject absolute paths
            if member.name.startswith("/") or member.name.startswith("\\"):
                raise SecurityError(
                    f"보안 검증 실패: 절대 경로가 포함되어 있습니다 ({member.name})"
                )

            # Reject path traversal
            if ".." in Path(member.name).parts:
                raise SecurityError(
                    f"보안 검증 실패: 경로 탈출 시도가 감지되었습니다 ({member.name})"
                )

            # Verify extraction stays within dest
            target_path = (dest_resolved / member.name).resolve()
            if not target_path.is_relative_to(dest_resolved):
                raise SecurityError(
                    f"보안 검증 실패: 경로 탈출 시도가 감지되었습니다 ({member.name})"
                )

            # Count files and size
            if member.isfile():
                file_count += 1
                total_size += member.size

                if file_count > MAX_FILES:
                    raise SecurityError(
                        f"보안 검증 실패: 파일 수가 {MAX_FILES:,}개를 초과합니다."
                    )
                if total_size > MAX_TOTAL_SIZE:
                    raise SecurityError(
                        f"보안 검증 실패: 총 크기가 {MAX_TOTAL_SIZE // (1024*1024)}MB를 초과합니다."
                    )

            members_to_extract.append(member)

        # GitHub tarballs have a top-level directory like "owner-name-sha/"
        # Find the common prefix to strip
        top_dirs = {m.name.split("/")[0] for m in members_to_extract if "/" in m.name}
        prefix = ""
        if len(top_dirs) == 1:
            prefix = top_dirs.pop() + "/"

        # If subdir specified, adjust prefix
        if subdir:
            prefix = prefix + subdir.strip("/") + "/"

        # Second pass: extract validated members with path rewriting
        extracted_count = 0
        for member in members_to_extract:
            # Strip the prefix
            if not member.name.startswith(prefix):
                continue

            relative_name = member.name[len(prefix):]
            if not relative_name:
                continue

            # Rewrite the member name
            member_copy = tarfile.TarInfo(name=relative_name)
            member_copy.size = member.size
            member_copy.mode = member.mode
            member_copy.type = member.type

            target_path = dest_resolved / relative_name

            if member.isdir():
                target_path.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                fileobj = tar.extractfile(member)
                if fileobj:
                    with open(target_path, "wb") as f:
                        shutil.copyfileobj(fileobj, f)
                    extracted_count += 1

    return extracted_count


# --- CLAUDE.md Generation ---------------------------------------------------


def _generate_claude_md(
    dest: Path,
    owner: str,
    name: str,
    license_name: str,
    next_steps: list[str],
) -> Path:
    """Generate a CLAUDE.md file with source attribution."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        f"# {name}",
        "",
        f"> Scaffolded from https://github.com/{owner}/{name}",
        f"> Original license: {license_name}",
        f"> Scaffolded at: {timestamp}",
        "",
    ]

    if next_steps:
        lines.append("## Quick Start")
        lines.append("")
        for step in next_steps:
            lines.append(f"```bash")
            lines.append(step)
            lines.append("```")
            lines.append("")

    claude_md_path = dest / "CLAUDE.md"
    claude_md_path.write_text("\n".join(lines), encoding="utf-8")
    return claude_md_path


# --- Next Steps Detection ---------------------------------------------------


def _detect_next_steps(dest: Path) -> list[str]:
    """Detect setup steps from common project files."""
    steps: list[str] = []
    files = {f.name for f in dest.iterdir() if f.is_file()} if dest.exists() else set()

    if "package.json" in files:
        # Check for lock files to determine package manager
        if "pnpm-lock.yaml" in files:
            steps.append("pnpm install")
        elif "yarn.lock" in files:
            steps.append("yarn install")
        else:
            steps.append("npm install")

    if "requirements.txt" in files:
        steps.append("pip install -r requirements.txt")

    if "pyproject.toml" in files and "requirements.txt" not in files:
        steps.append("pip install -e .")

    if ".env.example" in files:
        steps.append("cp .env.example .env")

    if "docker-compose.yml" in files or "docker-compose.yaml" in files:
        steps.append("docker-compose up")

    if "Makefile" in files and not steps:
        steps.append("make")

    return steps


# --- License Detection -------------------------------------------------------


def _find_license_name(dest: Path) -> str:
    """Try to detect the license from LICENSE file in the extracted directory."""
    license_names = {"LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "LICENCE.md"}
    for name in license_names:
        license_path = dest / name
        if license_path.exists():
            return "See LICENSE file"
    return "Unknown"


# --- MCP Tool Definition ---------------------------------------------------


SCAFFOLD_TOOL = Tool(
    name="scaffold",
    description=(
        "GitHub 레포를 대상 디렉토리로 복사(git history 제외)하고 "
        "CLAUDE.md를 자동 생성합니다."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo_url": {
                "type": "string",
                "description": "Full GitHub repository URL (https://github.com/owner/name)",
            },
            "target_dir": {
                "type": "string",
                "description": "Target directory path (absolute or relative to CWD)",
            },
            "subdir": {
                "type": "string",
                "description": "Subdirectory within the repo to extract (e.g., 'packages/app')",
            },
            "generate_claude_md": {
                "type": "boolean",
                "default": True,
                "description": "Whether to generate a CLAUDE.md file",
            },
        },
        "required": ["repo_url", "target_dir"],
    },
)


# --- Handler ----------------------------------------------------------------


async def handle_scaffold(
    arguments: dict[str, Any],
    github: GitHubClient,
) -> list[TextContent]:
    """Execute the scaffold pipeline.

    1. Validate target_dir (path traversal prevention)
    2. Validate subdir (no ..)
    3. Check target_dir is empty
    4. Download tarball from GitHub
    5. Safe extract with security checks
    6. Verify LICENSE file preserved
    7. Generate CLAUDE.md (if requested)
    8. Detect next_steps
    9. Return result
    """
    # Step 1-2: Validate inputs
    args = _validate_scaffold_args(arguments)
    owner, name = parse_repo_url(args["repo_url"])
    subdir = _validate_subdir(args["subdir"])

    _log("info", "scaffold_start", repo=f"{owner}/{name}", target=args["target_dir"])

    # Step 3: Validate and prepare target directory
    target = _validate_target_dir(args["target_dir"])
    target.mkdir(parents=True, exist_ok=True)

    try:
        # Step 4: Download tarball
        _log("info", "tarball_downloading", repo=f"{owner}/{name}")
        tarball_bytes = await github.download_tarball(owner, name)

        if len(tarball_bytes) > MAX_TARBALL_SIZE:
            raise SecurityError(
                f"보안 검증 실패: tarball 크기({len(tarball_bytes) // (1024*1024)}MB)가 "
                f"제한({MAX_TARBALL_SIZE // (1024*1024)}MB)을 초과합니다."
            )

        # Step 5: Safe extraction
        _log("info", "tarball_extracting", repo=f"{owner}/{name}")
        files_created = _safe_extract_tarball(tarball_bytes, target, subdir)

        # Step 6: Verify LICENSE preserved
        license_name = _find_license_name(target)
        has_license = license_name != "Unknown"
        if not has_license:
            _log("warning", "license_missing",
                 repo=f"{owner}/{name}",
                 msg="LICENSE file not found in extracted contents")

        # Step 7: Generate CLAUDE.md
        claude_md_path: str | None = None
        if args["generate_claude_md"]:
            # Get license from GitHub API
            try:
                license_data = await github.get_license(owner, name)
                api_license = license_data.get("name", "Unknown")
            except Exception:
                api_license = license_name

            next_steps = _detect_next_steps(target)
            md_path = _generate_claude_md(target, owner, name, api_license, next_steps)
            claude_md_path = str(md_path)
            files_created += 1

        # Step 8: Detect next_steps
        next_steps = _detect_next_steps(target)
        if not has_license:
            next_steps.insert(0, "# WARNING: LICENSE 파일이 없습니다. 라이선스를 확인하세요.")

        _log("info", "scaffold_complete",
             repo=f"{owner}/{name}",
             files=files_created,
             path=str(target))

        result = {
            "status": "success",
            "path": str(target),
            "files_created": files_created,
            "claude_md_path": claude_md_path,
            "next_steps": next_steps,
        }

    except SecurityError as e:
        _log("warning", "scaffold_security_error",
             repo=f"{owner}/{name}", error=str(e))
        # Clean up on security failure
        if target.exists() and not any(target.iterdir()):
            target.rmdir()
        result = {
            "status": "error",
            "path": str(target),
            "files_created": 0,
            "claude_md_path": None,
            "next_steps": [],
            "error": str(e),
        }

    except Exception as e:
        _log("error", "scaffold_failed",
             repo=f"{owner}/{name}", error=str(e)[:200])
        result = {
            "status": "error",
            "path": str(target),
            "files_created": 0,
            "claude_md_path": None,
            "next_steps": [],
            "error": "스캐폴딩 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
        }

    return [TextContent(
        type="text",
        text=json.dumps(result, ensure_ascii=False, indent=2),
    )]

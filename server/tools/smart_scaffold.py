"""smart_scaffold MCP tool.

Downloads a GitHub repo and applies smart customizations:
- Partial extraction (keep_only / remove_patterns)
- Project name substitution
- Environment variable file generation
- Unused dependency detection

SECURITY-CRITICAL: Reuses scaffold.py security primitives.
All glob patterns are validated against path traversal.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
from pathlib import Path, PurePosixPath
from typing import Any

from mcp.types import TextContent, Tool

from server.core.github_client import GitHubClient, parse_repo_url
from server.tools.scaffold import (
    SecurityError,
    _detect_next_steps,
    _find_license_name,
    _generate_claude_md,
    _safe_extract_tarball,
    _validate_subdir,
    _validate_target_dir,
)

logger = logging.getLogger("oss-scout")

_REPO_URL_PATTERN = re.compile(
    r"^https://github\.com/[\w.\-]+/[\w.\-]+/?$"
)


def _log(level: str, event: str, **kwargs: Any) -> None:
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(json.dumps(entry, ensure_ascii=False))


# --- Glob Pattern Security ---------------------------------------------------


def _validate_glob_patterns(patterns: list[str], param_name: str) -> list[str]:
    """Validate glob patterns have no path traversal."""
    validated: list[str] = []
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        pattern = pattern.strip()
        # Reject path traversal
        if ".." in PurePosixPath(pattern).parts:
            raise SecurityError(
                f"보안 검증 실패: {param_name}에 '..' 경로를 사용할 수 없습니다."
            )
        # Reject absolute paths
        if pattern.startswith("/") or pattern.startswith("\\"):
            raise SecurityError(
                f"보안 검증 실패: {param_name}는 상대 경로만 허용됩니다."
            )
        validated.append(pattern)
    return validated


# --- Input Validation ---------------------------------------------------------


def _validate_smart_scaffold_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize smart_scaffold arguments."""
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

    # project_name
    project_name = arguments.get("project_name")
    if project_name is not None:
        if not isinstance(project_name, str) or not project_name.strip():
            project_name = None
        else:
            project_name = project_name.strip()

    # keep_only
    keep_only = arguments.get("keep_only")
    if keep_only is not None:
        if not isinstance(keep_only, list):
            raise ValueError("keep_only는 문자열 배열이어야 합니다.")
        keep_only = _validate_glob_patterns(keep_only, "keep_only")
        if not keep_only:
            keep_only = None

    # remove_patterns
    remove_patterns = arguments.get("remove_patterns")
    if remove_patterns is not None:
        if not isinstance(remove_patterns, list):
            raise ValueError("remove_patterns는 문자열 배열이어야 합니다.")
        remove_patterns = _validate_glob_patterns(remove_patterns, "remove_patterns")
        if not remove_patterns:
            remove_patterns = None

    # env_vars
    env_vars = arguments.get("env_vars")
    if env_vars is not None and not isinstance(env_vars, dict):
        raise ValueError("env_vars는 객체(키-값)여야 합니다.")

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
        "project_name": project_name,
        "keep_only": keep_only,
        "remove_patterns": remove_patterns,
        "env_vars": env_vars,
        "subdir": subdir,
        "generate_claude_md": generate_claude_md,
    }


# --- File Filtering -----------------------------------------------------------


def _collect_all_files(directory: Path) -> list[Path]:
    """Recursively collect all file paths relative to directory."""
    files: list[Path] = []
    for item in directory.rglob("*"):
        if item.is_file():
            files.append(item.relative_to(directory))
    return files


def _matches_any_pattern(file_path: Path, patterns: list[str]) -> bool:
    """Check if a file path matches any of the glob patterns."""
    posix_path = file_path.as_posix()
    for pattern in patterns:
        if fnmatch.fnmatch(posix_path, pattern):
            return True
        # Also check if any parent directory matches
        for parent in file_path.parents:
            if parent != Path(".") and fnmatch.fnmatch(parent.as_posix() + "/", pattern):
                return True
    return False


def _apply_keep_only(directory: Path, keep_patterns: list[str]) -> int:
    """Remove files that don't match keep_only patterns. Returns count removed."""
    all_files = _collect_all_files(directory)
    removed = 0

    # Always keep essential files
    essential = {"LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "LICENCE.md"}

    for rel_path in all_files:
        if rel_path.name in essential:
            continue
        if not _matches_any_pattern(rel_path, keep_patterns):
            full_path = directory / rel_path
            full_path.unlink()
            removed += 1

    # Clean up empty directories
    _remove_empty_dirs(directory)
    return removed


def _apply_remove_patterns(directory: Path, remove_patterns: list[str]) -> int:
    """Remove files matching remove_patterns. Returns count removed."""
    all_files = _collect_all_files(directory)
    removed = 0

    for rel_path in all_files:
        if _matches_any_pattern(rel_path, remove_patterns):
            full_path = directory / rel_path
            full_path.unlink()
            removed += 1

    _remove_empty_dirs(directory)
    return removed


def _remove_empty_dirs(directory: Path) -> None:
    """Remove empty directories bottom-up."""
    for dirpath in sorted(directory.rglob("*"), reverse=True):
        if dirpath.is_dir() and not any(dirpath.iterdir()):
            dirpath.rmdir()


# --- Project Name Substitution ------------------------------------------------


def _apply_project_name(directory: Path, project_name: str, original_name: str) -> None:
    """Replace original repo name with new project name in key files."""
    # package.json: update "name" field
    pkg_json = directory / "package.json"
    if pkg_json.exists():
        try:
            content = pkg_json.read_text(encoding="utf-8")
            data = json.loads(content)
            if "name" in data:
                data["name"] = project_name
                pkg_json.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
        except (json.JSONDecodeError, OSError):
            pass

    # README.md: replace first heading with project name
    readme = directory / "README.md"
    if readme.exists():
        try:
            content = readme.read_text(encoding="utf-8")
            # Replace the first H1 heading
            content = re.sub(
                r"^#\s+.+$",
                f"# {project_name}",
                content,
                count=1,
                flags=re.MULTILINE,
            )
            readme.write_text(content, encoding="utf-8")
        except OSError:
            pass


# --- Environment Variable File Generation ------------------------------------


def _apply_env_vars(directory: Path, env_vars: dict[str, str]) -> bool:
    """Generate .env file from env_vars, using .env.example as template if available.

    Returns True if .env was created.
    """
    env_example = directory / ".env.example"
    env_file = directory / ".env"

    if env_example.exists():
        try:
            lines = env_example.read_text(encoding="utf-8").splitlines()
            new_lines: list[str] = []
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.split("=", 1)[0].strip()
                    if key in env_vars:
                        new_lines.append(f"{key}={env_vars[key]}")
                    else:
                        new_lines.append(line)
                else:
                    new_lines.append(line)
            # Add any env_vars not in the template
            existing_keys = {
                line.split("=", 1)[0].strip()
                for line in new_lines
                if line.strip() and not line.strip().startswith("#") and "=" in line
            }
            for key, value in env_vars.items():
                if key not in existing_keys:
                    new_lines.append(f"{key}={value}")
            env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            return True
        except OSError:
            pass

    # No .env.example, generate from scratch
    if env_vars:
        lines_out = [f"{key}={value}" for key, value in env_vars.items()]
        env_file.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
        return True

    return False


# --- Unused Dependency Detection ----------------------------------------------


def _detect_unused_deps(directory: Path) -> list[str]:
    """Detect potentially unused dependencies after file removal.

    Scans remaining source files for import/require statements and
    compares against package.json dependencies.
    Returns list of dependency names that are NOT referenced in any remaining file.
    """
    pkg_json = directory / "package.json"
    if not pkg_json.exists():
        return []

    try:
        data = json.loads(pkg_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    all_deps: dict[str, str] = {}
    all_deps.update(data.get("dependencies", {}))
    # devDependencies are less relevant but still check
    # all_deps.update(data.get("devDependencies", {}))

    if not all_deps:
        return []

    # Collect all text content from remaining source files
    source_extensions = {
        ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
        ".vue", ".svelte", ".astro",
    }
    all_source_text = ""
    for file_path in directory.rglob("*"):
        if file_path.is_file() and file_path.suffix in source_extensions:
            try:
                all_source_text += file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

    # Check which deps are referenced
    unused: list[str] = []
    for dep_name in sorted(all_deps.keys()):
        # Check for import/require patterns
        # Handles: import ... from 'dep', require('dep'), import('dep')
        # Also handles scoped packages like @vercel/analytics
        if dep_name not in all_source_text:
            unused.append(dep_name)

    return unused


# --- MCP Tool Definition -----------------------------------------------------


SMART_SCAFFOLD_TOOL = Tool(
    name="smart_scaffold",
    description=(
        "GitHub 레포를 스마트하게 스캐폴딩합니다. "
        "부분 추출, 불필요 코드 제거, 커스터마이징을 지원합니다."
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
            "project_name": {
                "type": "string",
                "description": "새 프로젝트명 (원본 레포명 치환)",
            },
            "keep_only": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "유지할 glob 패턴 (예: ['src/components/**', 'src/hooks/**'])"
                ),
            },
            "remove_patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "제거할 glob 패턴 (예: ['src/pages/analytics/**', 'tests/**'])"
                ),
            },
            "env_vars": {
                "type": "object",
                "description": (
                    "환경변수 키-값 (예: {DATABASE_URL: 'firestore'})"
                ),
            },
            "subdir": {
                "type": "string",
                "description": "레포 내 특정 디렉토리만 추출 (예: 'packages/app')",
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


# --- Handler ------------------------------------------------------------------


async def handle_smart_scaffold(
    arguments: dict[str, Any],
    github: GitHubClient,
) -> list[TextContent]:
    """Execute the smart scaffold pipeline.

    1. Validate all inputs (security-first)
    2. Download + safe extract tarball
    3. Apply keep_only filter
    4. Apply remove_patterns
    5. Apply project_name substitution
    6. Generate .env from env_vars
    7. Detect unused dependencies
    8. Generate CLAUDE.md
    9. Return structured result
    """
    # Step 1: Validate inputs
    args = _validate_smart_scaffold_args(arguments)
    owner, name = parse_repo_url(args["repo_url"])
    subdir = _validate_subdir(args["subdir"])

    _log("info", "smart_scaffold_start", repo=f"{owner}/{name}", target=args["target_dir"])

    # Validate target directory
    target = _validate_target_dir(args["target_dir"])
    target.mkdir(parents=True, exist_ok=True)

    try:
        # Step 2: Download and extract
        _log("info", "tarball_downloading", repo=f"{owner}/{name}")
        tarball_bytes = await github.download_tarball(owner, name)
        files_created = _safe_extract_tarball(tarball_bytes, target, subdir)
        files_removed = 0

        # Step 3: Apply keep_only
        if args["keep_only"]:
            _log("info", "applying_keep_only", patterns=args["keep_only"])
            removed = _apply_keep_only(target, args["keep_only"])
            files_removed += removed

        # Step 4: Apply remove_patterns
        if args["remove_patterns"]:
            _log("info", "applying_remove_patterns", patterns=args["remove_patterns"])
            removed = _apply_remove_patterns(target, args["remove_patterns"])
            files_removed += removed

        # Step 5: Apply project_name
        project_name = args["project_name"] or name
        if args["project_name"]:
            _log("info", "applying_project_name", name=project_name)
            _apply_project_name(target, project_name, name)

        # Step 6: Generate .env
        env_file_created = False
        if args["env_vars"]:
            _log("info", "applying_env_vars")
            env_file_created = _apply_env_vars(target, args["env_vars"])
            if env_file_created:
                files_created += 1

        # Step 7: Detect unused dependencies
        unused_deps = _detect_unused_deps(target)

        # Step 8: License + CLAUDE.md
        license_name = _find_license_name(target)
        has_license = license_name != "Unknown"
        if not has_license:
            _log("warning", "license_missing", repo=f"{owner}/{name}")

        if args["generate_claude_md"]:
            try:
                license_data = await github.get_license(owner, name)
                api_license = license_data.get("name", "Unknown")
            except Exception:
                api_license = license_name

            next_steps_for_md = _detect_next_steps(target)
            _generate_claude_md(target, owner, project_name, api_license, next_steps_for_md)
            files_created += 1

        # Step 9: Build result
        next_steps = _detect_next_steps(target)
        if not has_license:
            next_steps.insert(0, "# WARNING: LICENSE 파일이 없습니다. 라이선스를 확인하세요.")

        remaining_files = len(_collect_all_files(target))

        _log("info", "smart_scaffold_complete",
             repo=f"{owner}/{name}",
             files_created=remaining_files,
             files_removed=files_removed)

        result = {
            "status": "success",
            "path": str(target),
            "files_created": remaining_files,
            "files_removed": files_removed,
            "project_name": project_name,
            "env_file_created": env_file_created,
            "unused_deps_detected": unused_deps,
            "next_steps": next_steps,
        }

    except SecurityError as e:
        _log("warning", "smart_scaffold_security_error",
             repo=f"{owner}/{name}", error=str(e))
        if target.exists() and not any(target.iterdir()):
            target.rmdir()
        result = {
            "status": "error",
            "path": str(target),
            "files_created": 0,
            "files_removed": 0,
            "project_name": args.get("project_name"),
            "env_file_created": False,
            "unused_deps_detected": [],
            "next_steps": [],
            "error": str(e),
        }

    except Exception as e:
        _log("error", "smart_scaffold_failed",
             repo=f"{owner}/{name}", error=str(e)[:200])
        result = {
            "status": "error",
            "path": str(target),
            "files_created": 0,
            "files_removed": 0,
            "project_name": args.get("project_name"),
            "env_file_created": False,
            "unused_deps_detected": [],
            "next_steps": [],
            "error": "스마트 스캐폴딩 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
        }

    return [TextContent(
        type="text",
        text=json.dumps(result, ensure_ascii=False, indent=2),
    )]

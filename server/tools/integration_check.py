"""validate_integration MCP tool.

Scans a local project directory to detect integration issues BEFORE build:
  1. Missing dependencies (import vs package.json / requirements.txt)
  2. Missing environment variables (code refs vs .env)
  3. Broken relative imports (./xxx pointing to non-existent files)
  4. Empty / stub files (size 0, comments-only, TODO-heavy)

This is a purely local, rule-based check -- no GitHub API needed.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from mcp.types import TextContent, Tool

logger = logging.getLogger("oss-scout")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_JS_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
_PY_EXTENSIONS = {".py"}
_ALL_CODE_EXTENSIONS = _JS_EXTENSIONS | _PY_EXTENSIONS

# Directories to skip when scanning
_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".next", ".nuxt",
    "dist", "build", ".cache", ".venv", "venv", "env",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "coverage",
    ".tox", "egg-info",
}

# Max file size to read (256 KB) -- skip very large generated files
_MAX_FILE_SIZE = 256 * 1024

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# JS/TS import patterns
_JS_IMPORT_RE = re.compile(
    r"""(?:^|\n)\s*import\s+"""
    r"""(?:(?:[\w*{}\s,]+)\s+from\s+)?"""
    r"""['"]([^'"]+)['"]""",
)
_JS_REQUIRE_RE = re.compile(
    r"""require\(\s*['"]([^'"]+)['"]\s*\)""",
)

# Python import patterns
_PY_IMPORT_RE = re.compile(
    r"""^(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))""",
    re.MULTILINE,
)

# Environment variable patterns
_JS_ENV_RE = re.compile(r"""process\.env\.([A-Z][A-Z0-9_]+)""")
_JS_ENV_BRACKET_RE = re.compile(r"""process\.env\[['"]([A-Z][A-Z0-9_]+)['"]\]""")
_PY_ENV_RE = re.compile(r"""os\.(?:getenv|environ(?:\.get)?)\(\s*['"]([A-Z][A-Z0-9_]+)['"]""")
_DOTENV_RE = re.compile(r"""^([A-Z][A-Z0-9_]*)=""", re.MULTILINE)

# TODO/FIXME/HACK patterns
_TODO_RE = re.compile(r"""\b(TODO|FIXME|HACK|XXX)\b""", re.IGNORECASE)

# Node built-in modules (should not be flagged as missing deps)
_NODE_BUILTINS = {
    "assert", "buffer", "child_process", "cluster", "console", "constants",
    "crypto", "dgram", "dns", "domain", "events", "fs", "http", "https",
    "module", "net", "os", "path", "perf_hooks", "process", "punycode",
    "querystring", "readline", "repl", "stream", "string_decoder", "sys",
    "timers", "tls", "tty", "url", "util", "v8", "vm", "worker_threads",
    "zlib", "node:assert", "node:buffer", "node:child_process", "node:crypto",
    "node:dns", "node:events", "node:fs", "node:http", "node:https",
    "node:module", "node:net", "node:os", "node:path", "node:perf_hooks",
    "node:process", "node:querystring", "node:readline", "node:stream",
    "node:string_decoder", "node:timers", "node:tls", "node:tty", "node:url",
    "node:util", "node:v8", "node:vm", "node:worker_threads", "node:zlib",
}

# Python standard library modules (subset of commonly used ones)
_PY_STDLIB = {
    "abc", "argparse", "ast", "asyncio", "base64", "bisect", "calendar",
    "cgi", "cmd", "codecs", "collections", "configparser", "contextlib",
    "copy", "csv", "ctypes", "dataclasses", "datetime", "decimal",
    "difflib", "dis", "email", "enum", "errno", "faulthandler", "fcntl",
    "filecmp", "fileinput", "fnmatch", "fractions", "ftplib", "functools",
    "gc", "getpass", "gettext", "glob", "gzip", "hashlib", "heapq",
    "hmac", "html", "http", "imaplib", "importlib", "inspect", "io",
    "ipaddress", "itertools", "json", "keyword", "linecache", "locale",
    "logging", "lzma", "math", "mimetypes", "multiprocessing", "operator",
    "os", "pathlib", "pickle", "platform", "plistlib", "pprint",
    "profile", "pstats", "queue", "random", "re", "readline",
    "reprlib", "resource", "secrets", "select", "shelve", "shlex",
    "shutil", "signal", "site", "smtplib", "socket", "sqlite3", "ssl",
    "stat", "statistics", "string", "struct", "subprocess", "sys",
    "syslog", "tarfile", "tempfile", "textwrap", "threading", "time",
    "timeit", "token", "tokenize", "tomllib", "traceback", "types",
    "typing", "unittest", "urllib", "uuid", "venv", "warnings",
    "weakref", "webbrowser", "xml", "xmlrpc", "zipfile", "zipimport",
    "zlib", "_thread", "__future__",
}


def _log(level: str, event: str, **kwargs: Any) -> None:
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(json.dumps(entry, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

VALIDATE_INTEGRATION_TOOL = Tool(
    name="validate_integration",
    description=(
        "프로젝트의 의존성, import, 환경변수, 타입 등 통합 정합성을 사전 검증합니다. "
        "빌드 전에 누락된 패키지, 깨진 import, 환경변수 미설정 등을 감지합니다."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": "검증할 프로젝트 디렉토리 (절대 경로)",
            },
        },
        "required": ["project_dir"],
    },
)


# ---------------------------------------------------------------------------
# File scanning helpers
# ---------------------------------------------------------------------------

def _iter_code_files(
    project_dir: str,
    extensions: set[str] | None = None,
) -> list[tuple[str, str]]:
    """Walk project directory and yield (relative_path, absolute_path) pairs.

    Skips node_modules, .git, and other non-source directories.
    """
    if extensions is None:
        extensions = _ALL_CODE_EXTENSIONS

    results: list[tuple[str, str]] = []
    root = Path(project_dir)

    if not root.is_dir():
        return results

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip dirs in-place
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS and not d.startswith(".")
        ]

        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in extensions:
                continue

            abs_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(abs_path, project_dir).replace("\\", "/")
            results.append((rel_path, abs_path))

    return results


def _safe_read(filepath: str) -> str:
    """Read file content safely, returning empty string on failure."""
    try:
        size = os.path.getsize(filepath)
        if size > _MAX_FILE_SIZE:
            return ""
        with open(filepath, encoding="utf-8", errors="replace") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return ""


# ---------------------------------------------------------------------------
# 1. Import scanning
# ---------------------------------------------------------------------------

def scan_imports(project_dir: str) -> list[dict[str, Any]]:
    """Scan all JS/TS/Python files and extract import statements.

    Returns a list of dicts: {file, line, module, kind, is_relative}
    """
    results: list[dict[str, Any]] = []
    code_files = _iter_code_files(project_dir)

    for rel_path, abs_path in code_files:
        content = _safe_read(abs_path)
        if not content:
            continue

        ext = os.path.splitext(rel_path)[1].lower()

        if ext in _JS_EXTENSIONS:
            results.extend(_scan_js_imports(rel_path, content))
        elif ext in _PY_EXTENSIONS:
            results.extend(_scan_py_imports(rel_path, content))

    return results


def _scan_js_imports(rel_path: str, content: str) -> list[dict[str, Any]]:
    """Extract imports from a JS/TS file."""
    imports: list[dict[str, Any]] = []
    lines = content.splitlines()

    for i, line in enumerate(lines, start=1):
        for match in _JS_IMPORT_RE.finditer(line):
            module = match.group(1)
            imports.append({
                "file": rel_path,
                "line": i,
                "module": module,
                "kind": "js",
                "is_relative": module.startswith("."),
            })
        for match in _JS_REQUIRE_RE.finditer(line):
            module = match.group(1)
            imports.append({
                "file": rel_path,
                "line": i,
                "module": module,
                "kind": "js",
                "is_relative": module.startswith("."),
            })

    return imports


def _scan_py_imports(rel_path: str, content: str) -> list[dict[str, Any]]:
    """Extract imports from a Python file."""
    imports: list[dict[str, Any]] = []
    lines = content.splitlines()

    for i, line in enumerate(lines, start=1):
        m = _PY_IMPORT_RE.match(line.strip())
        if m:
            module = m.group(1) or m.group(2)
            if module:
                top_level = module.split(".")[0]
                imports.append({
                    "file": rel_path,
                    "line": i,
                    "module": module,
                    "kind": "py",
                    "is_relative": False,  # Python relative imports use "from . import"
                    "top_level": top_level,
                })

    return imports


# ---------------------------------------------------------------------------
# 2. Dependency checking
# ---------------------------------------------------------------------------

def _get_package_name(module: str) -> str:
    """Extract npm package name from import specifier.

    Examples:
      'react' -> 'react'
      'react/jsx-runtime' -> 'react'
      '@mui/material' -> '@mui/material'
      '@mui/material/Button' -> '@mui/material'
    """
    if module.startswith("@"):
        parts = module.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return module
    return module.split("/")[0]


def check_dependencies(project_dir: str, imports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check if imported packages exist in package.json / requirements.txt.

    Returns a list of issue dicts.
    """
    issues: list[dict[str, Any]] = []

    # Separate JS and Python imports
    js_imports = [imp for imp in imports if imp["kind"] == "js" and not imp["is_relative"]]
    py_imports = [imp for imp in imports if imp["kind"] == "py"]

    # --- JS dependency check ---
    if js_imports:
        pkg_deps = _load_package_json_deps(project_dir)
        node_modules_path = os.path.join(project_dir, "node_modules")
        has_node_modules = os.path.isdir(node_modules_path)

        seen_packages: set[str] = set()

        for imp in js_imports:
            pkg_name = _get_package_name(imp["module"])

            if pkg_name in seen_packages:
                continue
            if pkg_name in _NODE_BUILTINS:
                continue
            # Skip Node builtins prefixed with "node:"
            if pkg_name.startswith("node:"):
                continue

            seen_packages.add(pkg_name)

            in_pkg_json = pkg_name in pkg_deps
            installed = has_node_modules and os.path.isdir(
                os.path.join(node_modules_path, pkg_name),
            )

            if not in_pkg_json:
                issues.append({
                    "type": "missing_dependency",
                    "severity": "error",
                    "file": imp["file"],
                    "line": imp["line"],
                    "detail": f"패키지 '{pkg_name}'이(가) package.json에 없습니다",
                    "fix": f"npm install {pkg_name}",
                    "auto_fixable": True,
                })
            elif not installed and has_node_modules:
                issues.append({
                    "type": "missing_dependency",
                    "severity": "warning",
                    "file": imp["file"],
                    "line": imp["line"],
                    "detail": (
                        f"패키지 '{pkg_name}'이(가) package.json에 있지만 "
                        "node_modules에 설치되지 않았습니다"
                    ),
                    "fix": "npm install",
                    "auto_fixable": True,
                })

    # --- Python dependency check ---
    if py_imports:
        py_deps = _load_python_deps(project_dir)
        # Also consider local packages (directories with __init__.py)
        local_packages = _find_local_python_packages(project_dir)

        seen_modules: set[str] = set()

        for imp in py_imports:
            top_level = imp.get("top_level", imp["module"].split(".")[0])

            if top_level in seen_modules:
                continue
            if top_level in _PY_STDLIB:
                continue
            if top_level in local_packages:
                continue

            seen_modules.add(top_level)

            # Normalize: underscores vs hyphens
            normalized = top_level.replace("_", "-").lower()
            if normalized not in py_deps and top_level.lower() not in py_deps:
                issues.append({
                    "type": "missing_dependency",
                    "severity": "error",
                    "file": imp["file"],
                    "line": imp["line"],
                    "detail": (
                        f"패키지 '{top_level}'이(가)"
                        " requirements.txt/pyproject.toml에 없습니다"
                    ),
                    "fix": f"pip install {top_level}",
                    "auto_fixable": True,
                })

    return issues


def _load_package_json_deps(project_dir: str) -> set[str]:
    """Load all dependency names from package.json."""
    pkg_path = os.path.join(project_dir, "package.json")
    if not os.path.isfile(pkg_path):
        return set()

    try:
        with open(pkg_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return set()

    deps: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        deps.update(data.get(key, {}).keys())
    return deps


def _load_python_deps(project_dir: str) -> set[str]:
    """Load dependency names from requirements.txt and pyproject.toml."""
    deps: set[str] = set()

    # requirements.txt
    req_path = os.path.join(project_dir, "requirements.txt")
    if os.path.isfile(req_path):
        try:
            with open(req_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("-"):
                        continue
                    # Extract package name (before any version specifier)
                    pkg = re.split(r"[><=!~;\[\s]", line)[0].strip().lower()
                    if pkg:
                        deps.add(pkg)
        except OSError:
            pass

    # pyproject.toml (simplified parsing)
    pyproject_path = os.path.join(project_dir, "pyproject.toml")
    if os.path.isfile(pyproject_path):
        try:
            with open(pyproject_path, encoding="utf-8") as f:
                content = f.read()
            # Match dependency names in common patterns
            for match in re.finditer(r'"([a-zA-Z][a-zA-Z0-9_-]*)"', content):
                deps.add(match.group(1).lower())
        except OSError:
            pass

    return deps


def _find_local_python_packages(project_dir: str) -> set[str]:
    """Find local Python packages (directories with __init__.py)."""
    packages: set[str] = set()
    root = Path(project_dir)

    if not root.is_dir():
        return packages

    for item in root.iterdir():
        if item.is_dir() and item.name not in _SKIP_DIRS:
            init_file = item / "__init__.py"
            if init_file.exists():
                packages.add(item.name)

    return packages


# ---------------------------------------------------------------------------
# 3. Environment variable checking
# ---------------------------------------------------------------------------

def check_env_vars(project_dir: str) -> list[dict[str, Any]]:
    """Check that env vars referenced in code are defined in .env files.

    Returns a list of issue dicts.
    """
    issues: list[dict[str, Any]] = []

    # Collect all env var references from code
    code_refs: list[dict[str, Any]] = []
    code_files = _iter_code_files(project_dir)

    for rel_path, abs_path in code_files:
        content = _safe_read(abs_path)
        if not content:
            continue

        ext = os.path.splitext(rel_path)[1].lower()
        lines = content.splitlines()

        for i, line in enumerate(lines, start=1):
            found_vars: list[str] = []

            if ext in _JS_EXTENSIONS:
                found_vars.extend(_JS_ENV_RE.findall(line))
                found_vars.extend(_JS_ENV_BRACKET_RE.findall(line))
            elif ext in _PY_EXTENSIONS:
                found_vars.extend(_PY_ENV_RE.findall(line))

            for var_name in found_vars:
                code_refs.append({
                    "var": var_name,
                    "file": rel_path,
                    "line": i,
                })

    if not code_refs:
        return issues

    # Load .env file vars
    env_vars = _load_dotenv_vars(project_dir)

    # Check each referenced var
    seen_vars: set[str] = set()
    for ref in code_refs:
        var_name = ref["var"]
        if var_name in seen_vars:
            continue
        seen_vars.add(var_name)

        if var_name not in env_vars:
            issues.append({
                "type": "env_missing",
                "severity": "warning",
                "file": ref["file"],
                "line": ref["line"],
                "detail": (
                    f"{var_name}이(가) {ref['file']}:{ref['line']}에서 "
                    "참조되지만 .env 파일에 없습니다"
                ),
                "fix": f".env 파일에 {var_name}을(를) 추가하세요",
                "auto_fixable": False,
            })
        elif env_vars[var_name] == "" or env_vars[var_name] in (
            "your-key-here", "xxx", "placeholder", "CHANGEME", "TODO",
        ):
            issues.append({
                "type": "env_placeholder",
                "severity": "warning",
                "file": ref["file"],
                "line": ref["line"],
                "detail": (
                    f"{var_name}이(가) .env 파일에 있지만 "
                    "값이 비어있거나 플레이스홀더입니다"
                ),
                "fix": f".env 파일에서 {var_name}에 실제 값을 설정하세요",
                "auto_fixable": False,
            })

    return issues


def _load_dotenv_vars(project_dir: str) -> dict[str, str]:
    """Load environment variable names and values from .env files."""
    env_files = [".env", ".env.local", ".env.development", ".env.development.local"]
    vars_: dict[str, str] = {}

    for env_file in env_files:
        env_path = os.path.join(project_dir, env_file)
        if not os.path.isfile(env_path):
            continue

        try:
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)", line)
                    if match:
                        vars_[match.group(1)] = match.group(2).strip().strip("'\"")
        except OSError:
            pass

    return vars_


# ---------------------------------------------------------------------------
# 4. Broken relative import checking
# ---------------------------------------------------------------------------

def check_relative_imports(project_dir: str) -> list[dict[str, Any]]:
    """Check that relative imports (./xxx, ../xxx) point to existing files.

    Returns a list of issue dicts.
    """
    issues: list[dict[str, Any]] = []
    all_imports = scan_imports(project_dir)

    for imp in all_imports:
        if not imp["is_relative"]:
            continue

        if imp["kind"] != "js":
            continue

        source_file = os.path.join(project_dir, imp["file"])
        source_dir = os.path.dirname(source_file)
        target_module = imp["module"]

        # Resolve the relative path
        resolved = os.path.normpath(os.path.join(source_dir, target_module))

        # Check with various extensions
        found = _resolve_js_module(resolved)

        if not found:
            issues.append({
                "type": "broken_import",
                "severity": "error",
                "file": imp["file"],
                "line": imp["line"],
                "detail": (
                    f"import '{imp['module']}'이(가) 존재하지 않는 파일을 "
                    "참조합니다"
                ),
                "fix": (
                    f"{imp['module']}에 해당하는 파일을 생성하거나 "
                    "import 경로를 수정하세요"
                ),
                "auto_fixable": False,
            })

    return issues


def _resolve_js_module(base_path: str) -> bool:
    """Try to resolve a JS/TS module path with common extensions and index files."""
    # Direct file match
    if os.path.isfile(base_path):
        return True

    # Try extensions
    for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json"):
        if os.path.isfile(base_path + ext):
            return True

    # Try index files in directory
    if os.path.isdir(base_path):
        for ext in (".ts", ".tsx", ".js", ".jsx"):
            if os.path.isfile(os.path.join(base_path, f"index{ext}")):
                return True

    return False


# ---------------------------------------------------------------------------
# 5. Empty / stub file checking
# ---------------------------------------------------------------------------

def check_empty_files(project_dir: str) -> list[dict[str, Any]]:
    """Detect empty files, comment-only files, and TODO-heavy files.

    Returns a list of issue dicts.
    """
    issues: list[dict[str, Any]] = []
    code_files = _iter_code_files(project_dir)

    for rel_path, abs_path in code_files:
        try:
            size = os.path.getsize(abs_path)
        except OSError:
            continue

        # Empty file
        if size == 0:
            issues.append({
                "type": "empty_file",
                "severity": "warning",
                "file": rel_path,
                "line": 0,
                "detail": "파일이 비어있습니다",
                "fix": "구현을 추가하거나 파일을 제거하세요",
                "auto_fixable": False,
            })
            continue

        content = _safe_read(abs_path)
        if not content:
            continue

        # Check if file has only comments / whitespace
        lines = content.splitlines()
        code_lines = [
            ln for ln in lines
            if ln.strip()
            and not ln.strip().startswith("//")
            and not ln.strip().startswith("#")
            and not ln.strip().startswith("/*")
            and not ln.strip().startswith("*")
            and not ln.strip().startswith("*/")
        ]

        if len(lines) > 0 and len(code_lines) == 0:
            issues.append({
                "type": "comments_only",
                "severity": "warning",
                "file": rel_path,
                "line": 0,
                "detail": "파일에 주석만 있고 실제 코드가 없습니다",
                "fix": "구현을 추가하거나 파일을 제거하세요",
                "auto_fixable": False,
            })
            continue

        # Count TODOs / FIXMEs
        todo_count = len(_TODO_RE.findall(content))
        if todo_count >= 5:
            issues.append({
                "type": "todo_heavy",
                "severity": "info",
                "file": rel_path,
                "line": 0,
                "detail": f"TODO/FIXME/HACK 주석이 {todo_count}개 있습니다",
                "fix": "미완성 항목을 구현하세요",
                "auto_fixable": False,
            })

    return issues


# ---------------------------------------------------------------------------
# 6. API URL consistency checking
# ---------------------------------------------------------------------------

# Frontend API URL patterns
_FE_FETCH_RE = re.compile(
    r"""(?:fetch|axios\.(?:get|post|put|patch|delete)|useSWR)\s*\(\s*"""
    r"""['"`]([^'"`\s]+)['"`]""",
)
_FE_TEMPLATE_API_RE = re.compile(
    r"""\$\{[^}]+\}\s*/([a-zA-Z0-9_/-]+)""",
)
_FE_STRING_API_RE = re.compile(
    r"""['"`](/api/[^'"`\s]+)['"`]""",
)

# Backend route patterns
_BE_EXPRESS_RE = re.compile(
    r"""(?:router|app)\s*\.\s*(get|post|put|patch|delete)\s*\(\s*"""
    r"""['"]([^'"]+)['"]""",
)
_BE_FASTAPI_RE = re.compile(
    r"""@(?:app|router)\s*\.\s*(get|post|put|patch|delete)\s*\(\s*"""
    r"""['"]([^'"]+)['"]""",
)
_BE_STARLETTE_RE = re.compile(
    r"""Route\s*\(\s*['"]([^'"]+)['"]""",
)


def _normalize_api_path(path: str) -> str:
    """Normalize an API path for comparison.

    Strips trailing slashes and lowercases the path.
    """
    normalized = path.rstrip("/")
    if not normalized:
        normalized = "/"
    return normalized.lower()


def _is_api_url(url: str) -> bool:
    """Check if a URL looks like an API endpoint (starts with /api/)."""
    return url.startswith("/api/") or url == "/api"


def _scan_frontend_api_urls(
    project_dir: str,
) -> list[dict[str, Any]]:
    """Scan frontend code files for API endpoint URLs.

    Returns a list of dicts: {url, file, line, normalized}
    """
    results: list[dict[str, Any]] = []
    code_files = _iter_code_files(project_dir, _JS_EXTENSIONS)

    for rel_path, abs_path in code_files:
        content = _safe_read(abs_path)
        if not content:
            continue

        lines = content.splitlines()
        for i, line in enumerate(lines, start=1):
            urls_found: list[str] = []

            # fetch/axios/useSWR patterns
            for match in _FE_FETCH_RE.finditer(line):
                url = match.group(1)
                if _is_api_url(url):
                    urls_found.append(url)

            # Plain string literals containing /api/
            for match in _FE_STRING_API_RE.finditer(line):
                url = match.group(1)
                if url not in urls_found and _is_api_url(url):
                    urls_found.append(url)

            for url in urls_found:
                results.append({
                    "url": url,
                    "file": rel_path,
                    "line": i,
                    "normalized": _normalize_api_path(url),
                })

    return results


def _scan_backend_routes(
    project_dir: str,
) -> list[dict[str, Any]]:
    """Scan backend code files for route definitions.

    Also detects Next.js App Router file-based routes.
    Returns a list of dicts: {url, file, line, method, normalized}
    """
    results: list[dict[str, Any]] = []

    # --- Code-based routes (Express, FastAPI, Starlette) ---
    code_files = _iter_code_files(project_dir)

    for rel_path, abs_path in code_files:
        content = _safe_read(abs_path)
        if not content:
            continue

        lines = content.splitlines()
        for i, line in enumerate(lines, start=1):
            # Express: router.get("/api/...", ...) or app.post("/api/...", ...)
            for match in _BE_EXPRESS_RE.finditer(line):
                method = match.group(1).upper()
                url = match.group(2)
                results.append({
                    "url": url,
                    "file": rel_path,
                    "line": i,
                    "method": method,
                    "normalized": _normalize_api_path(url),
                })

            # FastAPI: @app.get("/api/...") or @router.post("/api/...")
            for match in _BE_FASTAPI_RE.finditer(line):
                method = match.group(1).upper()
                url = match.group(2)
                results.append({
                    "url": url,
                    "file": rel_path,
                    "line": i,
                    "method": method,
                    "normalized": _normalize_api_path(url),
                })

            # Starlette: Route("/api/...", ...)
            for match in _BE_STARLETTE_RE.finditer(line):
                url = match.group(1)
                results.append({
                    "url": url,
                    "file": rel_path,
                    "line": i,
                    "method": "ANY",
                    "normalized": _normalize_api_path(url),
                })

    # --- Next.js App Router file-based routes ---
    app_api_dir = os.path.join(project_dir, "app", "api")
    if os.path.isdir(app_api_dir):
        for dirpath, _dirnames, filenames in os.walk(app_api_dir):
            for fname in filenames:
                if fname.startswith("route.") and os.path.splitext(fname)[1].lower() in {
                    ".ts", ".tsx", ".js", ".jsx",
                }:
                    abs_path = os.path.join(dirpath, fname)
                    rel_dir = os.path.relpath(dirpath, project_dir).replace("\\", "/")
                    # Convert "app/api/reports/[id]" -> "/api/reports/[id]"
                    route_path = "/" + rel_dir.replace("app/", "", 1)
                    rel_file = os.path.relpath(abs_path, project_dir).replace("\\", "/")
                    results.append({
                        "url": route_path,
                        "file": rel_file,
                        "line": 0,
                        "method": "ANY",
                        "normalized": _normalize_api_path(route_path),
                    })

    return results


def check_api_url_consistency(project_dir: str) -> list[dict[str, Any]]:
    """Check that frontend API URLs have matching backend routes.

    Returns a list of issue dicts.
    """
    issues: list[dict[str, Any]] = []

    frontend_urls = _scan_frontend_api_urls(project_dir)
    backend_routes = _scan_backend_routes(project_dir)

    # Build set of normalized backend paths
    backend_paths: set[str] = {r["normalized"] for r in backend_routes}

    # Also build a set that includes dynamic segment matching:
    # e.g., /api/reports/[id] should match /api/reports/123
    # For simplicity, we normalize [param] and :param to a wildcard marker
    def _normalize_dynamic(path: str) -> str:
        """Replace dynamic segments like [id] or :id with a wildcard."""
        # Next.js style: [param]
        path = re.sub(r"\[[^\]]+\]", "*", path)
        # Express style: :param
        parts = path.split("/")
        parts = ["*" if p.startswith(":") else p for p in parts]
        return "/".join(parts)

    backend_patterns: set[str] = {_normalize_dynamic(p) for p in backend_paths}

    # Check each frontend URL
    seen_urls: set[str] = set()
    for fe in frontend_urls:
        normalized = fe["normalized"]
        if normalized in seen_urls:
            continue
        seen_urls.add(normalized)

        # Direct match
        if normalized in backend_paths:
            continue

        # Dynamic match: normalize the frontend URL and check patterns
        fe_dynamic = _normalize_dynamic(normalized)
        if fe_dynamic in backend_patterns:
            continue

        # Check if any backend pattern could match
        # e.g., frontend /api/reports/123 matches backend /api/reports/*
        matched = False
        fe_parts = normalized.split("/")
        for bp in backend_paths:
            bp_norm = _normalize_dynamic(bp)
            bp_parts = bp_norm.split("/")
            if len(fe_parts) == len(bp_parts) and all(
                fp == bp_p or bp_p == "*"
                for fp, bp_p in zip(fe_parts, bp_parts, strict=True)
            ):
                matched = True
                break

        if not matched:
            issues.append({
                "type": "api_url_mismatch",
                "severity": "warning",
                "file": fe["file"],
                "line": fe["line"],
                "detail": (
                    f"프론트엔드 URL '{fe['url']}'에 대응하는 "
                    "백엔드 라우트를 찾을 수 없습니다"
                ),
                "fix": "백엔드에 해당 라우트를 추가하거나 프론트엔드 URL을 수정하세요",
                "auto_fixable": False,
            })

    return issues


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

async def handle_validate_integration(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Execute validate_integration: scan local project for integration issues."""
    project_dir = arguments.get("project_dir", "")
    if not isinstance(project_dir, str) or not project_dir.strip():
        raise ValueError("project_dir를 입력해주세요.")

    project_dir = project_dir.strip()

    # Security: ensure the path exists and is a directory
    if not os.path.isdir(project_dir):
        raise ValueError(f"디렉토리가 존재하지 않습니다: {project_dir}")

    _log("info", "validate_integration_start", project_dir=project_dir)

    # Run all checks
    all_issues: list[dict[str, Any]] = []

    # 1. Scan imports
    imports = scan_imports(project_dir)

    # 2. Check dependencies
    dep_issues = check_dependencies(project_dir, imports)
    all_issues.extend(dep_issues)

    # 3. Check environment variables
    env_issues = check_env_vars(project_dir)
    all_issues.extend(env_issues)

    # 4. Check relative imports
    import_issues = check_relative_imports(project_dir)
    all_issues.extend(import_issues)

    # 5. Check empty files
    empty_issues = check_empty_files(project_dir)
    all_issues.extend(empty_issues)

    # 6. Check API URL consistency
    api_issues = check_api_url_consistency(project_dir)
    all_issues.extend(api_issues)

    # Compute summary
    errors = sum(1 for i in all_issues if i["severity"] == "error")
    warnings = sum(1 for i in all_issues if i["severity"] == "warning")
    infos = sum(1 for i in all_issues if i["severity"] == "info")
    auto_fixable = sum(1 for i in all_issues if i.get("auto_fixable", False))

    # Determine overall status
    if errors > 0:
        status = "FAIL"
    elif warnings > 0:
        status = "WARN"
    else:
        status = "PASS"

    # Build auto-fix command
    auto_fix_commands: list[str] = []
    seen_fixes: set[str] = set()
    for issue in all_issues:
        if issue.get("auto_fixable") and issue.get("fix"):
            fix = issue["fix"]
            if fix not in seen_fixes:
                seen_fixes.add(fix)
                auto_fix_commands.append(fix)

    result = {
        "status": status,
        "project_dir": project_dir,
        "issues": all_issues,
        "summary": {
            "errors": errors,
            "warnings": warnings,
            "infos": infos,
            "auto_fixable": auto_fixable,
            "total": len(all_issues),
        },
        "auto_fix_command": " && ".join(auto_fix_commands) if auto_fix_commands else None,
    }

    _log(
        "info", "validate_integration_complete",
        status=status,
        errors=errors,
        warnings=warnings,
        total=len(all_issues),
    )

    return [TextContent(
        type="text",
        text=json.dumps(result, ensure_ascii=False, indent=2),
    )]

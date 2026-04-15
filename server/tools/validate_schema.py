"""validate_schema MCP tool.

Compares frontend TypeScript types with backend schemas (Pydantic/Zod/Prisma)
to detect field mismatches BEFORE integration bugs occur.

This is a purely local, rule-based check -- no GitHub API or LLM needed.
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

_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".next", ".nuxt",
    "dist", "build", ".cache", ".venv", "venv", "env",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "coverage",
    ".tox", "egg-info",
}

_MAX_FILE_SIZE = 256 * 1024

# Type compatibility mapping: (ts_type, backend_type) -> compatible
TYPE_COMPAT: dict[tuple[str, str], bool] = {
    ("string", "str"): True,
    ("string", "String"): True,
    ("string", "datetime"): True,
    ("string", "DateTime"): True,
    ("string", "date"): True,
    ("string", "uuid"): True,
    ("string", "UUID"): True,
    ("string", "Decimal"): True,
    ("number", "int"): True,
    ("number", "Int"): True,
    ("number", "float"): True,
    ("number", "Float"): True,
    ("number", "Decimal"): True,
    ("boolean", "bool"): True,
    ("boolean", "Bool"): True,
    ("boolean", "Boolean"): True,
    ("string[]", "list[str]"): True,
    ("string[]", "List[str]"): True,
    ("number[]", "list[int]"): True,
    ("number[]", "List[int]"): True,
    ("number[]", "list[float]"): True,
    ("number[]", "List[float]"): True,
    ("boolean[]", "list[bool]"): True,
    ("boolean[]", "List[bool]"): True,
    ("any", "Any"): True,
    ("object", "dict"): True,
    ("object", "Dict"): True,
    ("object", "Json"): True,
}

# Common entity name suffixes to strip for matching
_ENTITY_SUFFIXES = (
    "Response", "Request", "Schema", "Model", "Input", "Output",
    "DTO", "Type", "Interface", "Dto", "Entity",
)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# TypeScript interface: interface Name { ... }
_TS_INTERFACE_RE = re.compile(
    r"(?:export\s+)?interface\s+(\w+)"
    r"(?:\s+extends\s+[\w<>,\s]+)?"
    r"\s*\{([^}]*)\}",
    re.DOTALL,
)

# TypeScript type alias: type Name = { ... }
_TS_TYPE_RE = re.compile(
    r"(?:export\s+)?type\s+(\w+)\s*=\s*\{([^}]*)\}",
    re.DOTALL,
)

# TypeScript field: fieldName: Type; or fieldName?: Type;
_TS_FIELD_RE = re.compile(
    r"^\s*(?:readonly\s+)?(\w+)(\?)?:\s*([^;/\n]+)",
    re.MULTILINE,
)

# Pydantic model: class Name(BaseModel): ...
_PYDANTIC_CLASS_RE = re.compile(
    r"class\s+(\w+)\s*\(\s*(?:\w+\.)?BaseModel\s*\)\s*:",
)

# Pydantic field: field_name: Type or field_name: Type = default
_PYDANTIC_FIELD_RE = re.compile(
    r"^\s{4}(\w+)\s*:\s*([^=\n#]+?)(?:\s*=\s*[^\n]*)?$",
    re.MULTILINE,
)

# Zod schema: const nameSchema = z.object({ ... })
_ZOD_SCHEMA_RE = re.compile(
    r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*z\.object\(\s*\{([^}]*)\}\s*\)",
    re.DOTALL,
)

# Zod field: fieldName: z.string(), etc.
_ZOD_FIELD_RE = re.compile(
    r"^\s*(\w+)\s*:\s*z\.(\w+)\(",
    re.MULTILINE,
)

# Prisma model: model Name { ... }
_PRISMA_MODEL_RE = re.compile(
    r"model\s+(\w+)\s*\{([^}]*)\}",
    re.DOTALL,
)

# Prisma field: fieldName Type ...
_PRISMA_FIELD_RE = re.compile(
    r"^\s+(\w+)\s+(String|Int|Float|Boolean|DateTime|Json|Decimal|BigInt|Bytes)(\[\])?"
    r"(\?)?",
    re.MULTILINE,
)


def _log(level: str, event: str, **kwargs: Any) -> None:
    """Emit a structured JSON log line."""
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(json.dumps(entry, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

VALIDATE_SCHEMA_TOOL = Tool(
    name="validate_schema",
    description="프론트엔드 TypeScript 타입과 백엔드 스키마의 필드 일치 여부를 검증합니다",
    inputSchema={
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": "프로젝트 루트 디렉토리",
            },
            "frontend_types_dir": {
                "type": "string",
                "description": "프론트엔드 타입 경로 (auto-detect)",
            },
            "backend_schema_dir": {
                "type": "string",
                "description": "백엔드 스키마 경로 (auto-detect)",
            },
        },
        "required": ["project_dir"],
    },
)


# ---------------------------------------------------------------------------
# File reading helper
# ---------------------------------------------------------------------------

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
# 1. Find type/schema files
# ---------------------------------------------------------------------------

def find_type_files(project_dir: str, frontend_dir: str | None = None) -> list[tuple[str, str]]:
    """Find frontend TypeScript type definition files.

    Scans for: types/*.ts, *.d.ts, interfaces/*.ts, src/types/**/*.ts
    Returns list of (relative_path, absolute_path) pairs.
    """
    results: list[tuple[str, str]] = []
    root = Path(project_dir)

    if frontend_dir:
        search_root = root / frontend_dir
        if not search_root.is_dir():
            return results
        roots_to_scan = [search_root]
    else:
        # Auto-detect: scan common frontend type directories
        roots_to_scan = [root]

    for scan_root in roots_to_scan:
        if not scan_root.is_dir():
            continue

        for dirpath, dirnames, filenames in os.walk(scan_root):
            dirnames[:] = [
                d for d in dirnames
                if d not in _SKIP_DIRS and not d.startswith(".")
            ]

            rel_dir = os.path.relpath(dirpath, project_dir).replace("\\", "/")
            dir_name = os.path.basename(dirpath)

            for fname in filenames:
                if not fname.endswith(".ts") and not fname.endswith(".tsx"):
                    continue

                # Skip test files
                if fname.endswith(".test.ts") or fname.endswith(".spec.ts"):
                    continue

                abs_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(abs_path, project_dir).replace("\\", "/")

                # Include if: in types/ or interfaces/ dir, or is a .d.ts file
                is_type_dir = dir_name in ("types", "interfaces", "models", "schemas")
                is_dts = fname.endswith(".d.ts")
                is_in_types_path = "/types/" in rel_dir or "/interfaces/" in rel_dir

                if is_type_dir or is_dts or is_in_types_path:
                    results.append((rel_path, abs_path))

    return results


def find_schema_files(
    project_dir: str,
    backend_dir: str | None = None,
) -> list[tuple[str, str, str]]:
    """Find backend schema files (Pydantic, Zod, Prisma).

    Returns list of (relative_path, absolute_path, schema_type) triples.
    schema_type is one of: 'pydantic', 'zod', 'prisma'
    """
    results: list[tuple[str, str, str]] = []
    root = Path(project_dir)

    if backend_dir:
        search_root = root / backend_dir
        if not search_root.is_dir():
            return results
        roots_to_scan = [search_root]
    else:
        roots_to_scan = [root]

    for scan_root in roots_to_scan:
        if not scan_root.is_dir():
            continue

        for dirpath, dirnames, filenames in os.walk(scan_root):
            dirnames[:] = [
                d for d in dirnames
                if d not in _SKIP_DIRS and not d.startswith(".")
            ]

            for fname in filenames:
                abs_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(abs_path, project_dir).replace("\\", "/")

                # Prisma files
                if fname.endswith(".prisma"):
                    results.append((rel_path, abs_path, "prisma"))
                    continue

                # Python files with Pydantic models
                if fname.endswith(".py"):
                    content = _safe_read(abs_path)
                    if content and _PYDANTIC_CLASS_RE.search(content):
                        results.append((rel_path, abs_path, "pydantic"))
                    continue

                # TS/JS files with Zod schemas
                if fname.endswith((".ts", ".tsx", ".js", ".jsx")):
                    # Skip test files
                    if fname.endswith((".test.ts", ".spec.ts")):
                        continue
                    content = _safe_read(abs_path)
                    if content and _ZOD_SCHEMA_RE.search(content):
                        results.append((rel_path, abs_path, "zod"))
                    continue

    return results


# ---------------------------------------------------------------------------
# 2. Extract types/schemas
# ---------------------------------------------------------------------------

def extract_ts_types(content: str) -> list[dict[str, Any]]:
    """Extract TypeScript interface/type fields using regex.

    Returns list of entities: {name, fields: [{name, type, optional}]}
    Handles: interface, type alias, optional fields, readonly, multiline.
    Limited to 1-depth (no nested type expansion).
    """
    entities: list[dict[str, Any]] = []

    for pattern in (_TS_INTERFACE_RE, _TS_TYPE_RE):
        for match in pattern.finditer(content):
            entity_name = match.group(1)
            body = match.group(2)

            fields: list[dict[str, Any]] = []
            for field_match in _TS_FIELD_RE.finditer(body):
                field_name = field_match.group(1)
                optional = field_match.group(2) is not None
                field_type = field_match.group(3).strip().rstrip(",").strip()

                fields.append({
                    "name": field_name,
                    "type": field_type,
                    "optional": optional,
                })

            if fields:
                entities.append({"name": entity_name, "fields": fields})

    return entities


def extract_pydantic_models(content: str) -> list[dict[str, Any]]:
    """Extract Pydantic model fields using regex.

    Returns list of entities: {name, fields: [{name, type, optional}]}
    Handles: Optional[], default values, Field().
    """
    entities: list[dict[str, Any]] = []

    # Find each class declaration
    class_matches = list(_PYDANTIC_CLASS_RE.finditer(content))

    for i, class_match in enumerate(class_matches):
        entity_name = class_match.group(1)
        start = class_match.end()

        # Find the end of the class body (next class or end of file)
        end = class_matches[i + 1].start() if i + 1 < len(class_matches) else len(content)

        body = content[start:end]

        fields: list[dict[str, Any]] = []
        for field_match in _PYDANTIC_FIELD_RE.finditer(body):
            field_name = field_match.group(1)
            field_type = field_match.group(2).strip()

            # Skip class-level declarations that aren't fields
            if field_name in ("model_config", "Config", "class"):
                continue

            # Detect optional
            optional = (
                field_type.startswith("Optional[")
                or " | None" in field_type
                or field_type.endswith("| None")
            )

            # Clean up the type
            clean_type = field_type
            if clean_type.startswith("Optional[") and clean_type.endswith("]"):
                clean_type = clean_type[9:-1]
            clean_type = clean_type.replace(" | None", "").strip()

            fields.append({
                "name": field_name,
                "type": clean_type,
                "optional": optional,
            })

        if fields:
            entities.append({"name": entity_name, "fields": fields})

    return entities


def extract_zod_schemas(content: str) -> list[dict[str, Any]]:
    """Extract Zod schema fields using regex.

    Returns list of entities: {name, fields: [{name, type, optional}]}
    """
    entities: list[dict[str, Any]] = []

    for match in _ZOD_SCHEMA_RE.finditer(content):
        raw_name = match.group(1)
        body = match.group(2)

        # Normalize schema name: remove 'Schema' suffix for entity matching
        entity_name = raw_name

        fields: list[dict[str, Any]] = []
        for field_match in _ZOD_FIELD_RE.finditer(body):
            field_name = field_match.group(1)
            zod_type = field_match.group(2)

            # Map zod type to readable type
            optional = ".optional()" in body.split(field_name, 1)[-1].split("\n")[0]

            fields.append({
                "name": field_name,
                "type": zod_type,
                "optional": optional,
            })

        if fields:
            entities.append({"name": entity_name, "fields": fields})

    return entities


def extract_prisma_models(content: str) -> list[dict[str, Any]]:
    """Extract Prisma model fields.

    Returns list of entities: {name, fields: [{name, type, optional}]}
    """
    entities: list[dict[str, Any]] = []

    for match in _PRISMA_MODEL_RE.finditer(content):
        entity_name = match.group(1)
        body = match.group(2)

        fields: list[dict[str, Any]] = []
        for field_match in _PRISMA_FIELD_RE.finditer(body):
            field_name = field_match.group(1)
            field_type = field_match.group(2)
            is_array = field_match.group(3) is not None
            optional = field_match.group(4) is not None

            type_str = field_type + ("[]" if is_array else "")

            fields.append({
                "name": field_name,
                "type": type_str,
                "optional": optional,
            })

        if fields:
            entities.append({"name": entity_name, "fields": fields})

    return entities


# ---------------------------------------------------------------------------
# 3. Entity matching
# ---------------------------------------------------------------------------

def _normalize_entity_name(name: str) -> str:
    """Strip common suffixes and lowercase for matching."""
    normalized = name
    for suffix in _ENTITY_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized.lower()


def match_entities(
    frontend_entities: list[dict[str, Any]],
    backend_entities: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Match frontend types with backend schemas by normalized name.

    Returns list of (frontend_entity, backend_entity) pairs.
    Uses case-insensitive matching and strips common suffixes.
    """
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    used_backend: set[int] = set()

    for fe in frontend_entities:
        fe_norm = _normalize_entity_name(fe["name"])
        for j, be in enumerate(backend_entities):
            if j in used_backend:
                continue
            be_norm = _normalize_entity_name(be["name"])
            if fe_norm == be_norm:
                matches.append((fe, be))
                used_backend.add(j)
                break

    return matches


# ---------------------------------------------------------------------------
# 4. Field comparison
# ---------------------------------------------------------------------------

def _normalize_ts_type(ts_type: str) -> str:
    """Normalize a TS type for comparison."""
    t = ts_type.strip()
    # Remove trailing semicolons
    t = t.rstrip(";").strip()
    # Handle array notation
    if t.endswith("[]"):
        base = t[:-2].strip()
        return f"{base}[]"
    if t.startswith("Array<") and t.endswith(">"):
        base = t[6:-1].strip()
        return f"{base}[]"
    return t


def _normalize_backend_type(backend_type: str) -> str:
    """Normalize a backend type for comparison."""
    t = backend_type.strip()
    # Handle list[X] -> X[]
    lower = t.lower()
    if lower.startswith("list[") and t.endswith("]"):
        inner = t[5:-1].strip()
        return f"list[{inner}]"
    if lower.startswith("optional[") and t.endswith("]"):
        return t[9:-1].strip()
    return t


def _types_compatible(ts_type: str, backend_type: str) -> bool:
    """Check if a TS type is compatible with a backend type."""
    ts_norm = _normalize_ts_type(ts_type).lower()
    be_norm = _normalize_backend_type(backend_type).lower()

    # Exact match (case insensitive)
    if ts_norm == be_norm:
        return True

    # Check compatibility table
    for (ts_key, be_key), compat in TYPE_COMPAT.items():
        if ts_norm == ts_key.lower() and be_norm == be_key.lower():
            return compat

    return False


def _get_compat_note(ts_type: str, backend_type: str) -> str | None:
    """Return a compatibility note if types are compatible via serialization."""
    ts_norm = _normalize_ts_type(ts_type).lower()
    be_norm = _normalize_backend_type(backend_type).lower()

    serialization_pairs = {
        ("string", "datetime"): "직렬화 시 string으로 변환되므로 호환 가능",
        ("string", "date"): "직렬화 시 string으로 변환되므로 호환 가능",
        ("string", "uuid"): "직렬화 시 string으로 변환되므로 호환 가능",
        ("string", "decimal"): "직렬화 시 string으로 변환되므로 호환 가능",
        ("number", "decimal"): "정밀도 손실 가능성 있음",
    }

    return serialization_pairs.get((ts_norm, be_norm))


def compare_fields(
    frontend_entity: dict[str, Any],
    backend_entity: dict[str, Any],
) -> dict[str, Any]:
    """Compare matched entity fields and produce a comparison result.

    Returns dict with: matching_fields, issues, status.
    """
    fe_fields = {f["name"]: f for f in frontend_entity["fields"]}
    be_fields = {f["name"]: f for f in backend_entity["fields"]}

    matching_fields: list[str] = []
    issues: list[dict[str, Any]] = []

    # Check all frontend fields against backend
    for name, fe_field in fe_fields.items():
        if name not in be_fields:
            issues.append({
                "field": name,
                "frontend_type": fe_field["type"],
                "backend_type": None,
                "severity": "warning",
                "note": "프론트엔드에만 있는 필드입니다",
            })
            continue

        be_field = be_fields[name]

        if _types_compatible(fe_field["type"], be_field["type"]):
            matching_fields.append(name)
            # Add info note for serialization compatibility
            note = _get_compat_note(fe_field["type"], be_field["type"])
            if note:
                issues.append({
                    "field": name,
                    "frontend_type": fe_field["type"],
                    "backend_type": be_field["type"],
                    "severity": "info",
                    "note": note,
                })
        else:
            issues.append({
                "field": name,
                "frontend_type": fe_field["type"],
                "backend_type": be_field["type"],
                "severity": "error",
                "note": "타입이 일치하지 않습니다",
            })

    # Check backend-only fields
    for name, be_field in be_fields.items():
        if name not in fe_fields:
            issues.append({
                "field": name,
                "frontend_type": None,
                "backend_type": be_field["type"],
                "severity": "info",
                "note": "백엔드에만 있는 필드입니다 (의도적 숨김 가능)",
            })

    # Determine status
    errors = [i for i in issues if i["severity"] == "error"]
    warnings = [i for i in issues if i["severity"] == "warning"]

    if errors:
        status = "MISMATCH"
    elif warnings:
        status = "WARNING"
    else:
        status = "OK"

    return {
        "matching_fields": matching_fields,
        "issues": issues,
        "status": status,
    }


# ---------------------------------------------------------------------------
# Auto-detect helpers
# ---------------------------------------------------------------------------

def _auto_detect_frontend_dir(project_dir: str) -> str | None:
    """Try to find frontend types directory automatically."""
    candidates = [
        "src/types", "types", "src/interfaces", "interfaces",
        "frontend/src/types", "frontend/types", "client/src/types",
        "app/types", "src/app/types",
    ]
    root = Path(project_dir)
    for candidate in candidates:
        if (root / candidate).is_dir():
            return candidate

    # Check if any .d.ts files exist in src/
    src_dir = root / "src"
    if src_dir.is_dir():
        for dirpath, _, filenames in os.walk(src_dir):
            for f in filenames:
                if f.endswith(".d.ts"):
                    return os.path.relpath(dirpath, project_dir).replace("\\", "/")
    return None


def _auto_detect_backend_dir(project_dir: str) -> str | None:
    """Try to find backend schema directory automatically."""
    candidates = [
        "server/models", "server", "backend/models", "backend",
        "api/models", "api", "app/models", "models", "src/models",
        "src/server", "prisma",
    ]
    root = Path(project_dir)
    for candidate in candidates:
        candidate_path = root / candidate
        if candidate_path.is_dir():
            # Check if it contains schema files
            for dirpath, _, filenames in os.walk(candidate_path):
                for f in filenames:
                    if f.endswith(".prisma"):
                        return candidate
                    if f.endswith(".py"):
                        content = _safe_read(os.path.join(dirpath, f))
                        if content and _PYDANTIC_CLASS_RE.search(content):
                            return candidate
    return None


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

async def handle_validate_schema(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Execute validate_schema: compare frontend types with backend schemas."""
    project_dir = arguments.get("project_dir", "")
    if not isinstance(project_dir, str) or not project_dir.strip():
        raise ValueError("project_dir를 입력해주세요.")

    project_dir = project_dir.strip()

    if not os.path.isdir(project_dir):
        raise ValueError(f"디렉토리가 존재하지 않습니다: {project_dir}")

    _log("info", "validate_schema_start", project_dir=project_dir)

    frontend_dir = arguments.get("frontend_types_dir")
    backend_dir = arguments.get("backend_schema_dir")

    # Strip "auto-detect" sentinel
    if frontend_dir in (None, "", "auto-detect"):
        frontend_dir = None
    if backend_dir in (None, "", "auto-detect"):
        backend_dir = None

    # Find type files
    type_files = find_type_files(project_dir, frontend_dir)

    # Find schema files
    schema_files = find_schema_files(project_dir, backend_dir)

    # Extract frontend entities
    all_fe_entities: list[dict[str, Any]] = []
    for rel_path, abs_path in type_files:
        content = _safe_read(abs_path)
        if not content:
            continue
        entities = extract_ts_types(content)
        for entity in entities:
            entity["_file"] = rel_path
        all_fe_entities.extend(entities)

    # Extract backend entities
    all_be_entities: list[dict[str, Any]] = []
    for rel_path, abs_path, schema_type in schema_files:
        content = _safe_read(abs_path)
        if not content:
            continue

        if schema_type == "pydantic":
            entities = extract_pydantic_models(content)
        elif schema_type == "zod":
            entities = extract_zod_schemas(content)
        elif schema_type == "prisma":
            entities = extract_prisma_models(content)
        else:
            continue

        for entity in entities:
            entity["_file"] = rel_path
            entity["_schema_type"] = schema_type
        all_be_entities.extend(entities)

    # Match entities
    matched_pairs = match_entities(all_fe_entities, all_be_entities)

    # Track which entities were matched
    matched_fe_names = {fe["name"] for fe, _ in matched_pairs}
    matched_be_names = {be["name"] for _, be in matched_pairs}

    # Compare fields for matched pairs
    matched_results: list[dict[str, Any]] = []
    total_errors = 0
    total_warnings = 0
    total_infos = 0

    for fe, be in matched_pairs:
        comparison = compare_fields(fe, be)
        entity_result = {
            "name": fe["name"],
            "frontend_file": fe.get("_file", ""),
            "backend_file": be.get("_file", ""),
            "status": comparison["status"],
            "matching_fields": comparison["matching_fields"],
            "issues": comparison["issues"],
        }
        matched_results.append(entity_result)

        for issue in comparison["issues"]:
            if issue["severity"] == "error":
                total_errors += 1
            elif issue["severity"] == "warning":
                total_warnings += 1
            elif issue["severity"] == "info":
                total_infos += 1

    # Unmatched entities
    unmatched_frontend = [
        e["name"] for e in all_fe_entities if e["name"] not in matched_fe_names
    ]
    unmatched_backend = [
        e["name"] for e in all_be_entities if e["name"] not in matched_be_names
    ]

    # Overall status
    if total_errors > 0:
        status = "MISMATCH"
    elif total_warnings > 0:
        status = "WARNING"
    elif not matched_results and not all_fe_entities and not all_be_entities:
        status = "EMPTY"
    elif not matched_results:
        status = "NO_MATCH"
    else:
        status = "OK"

    result = {
        "status": status,
        "matched_entities": matched_results,
        "unmatched_frontend": unmatched_frontend,
        "unmatched_backend": unmatched_backend,
        "summary": {
            "total_entities_checked": len(matched_results),
            "matched": sum(1 for r in matched_results if r["status"] == "OK"),
            "mismatched": sum(1 for r in matched_results if r["status"] == "MISMATCH"),
            "errors": total_errors,
            "warnings": total_warnings,
            "infos": total_infos,
        },
    }

    _log(
        "info", "validate_schema_complete",
        status=status,
        matched=len(matched_results),
        errors=total_errors,
        warnings=total_warnings,
    )

    return [TextContent(
        type="text",
        text=json.dumps(result, ensure_ascii=False, indent=2),
    )]

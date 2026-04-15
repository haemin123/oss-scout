"""Unit tests for server/tools/validate_schema.py."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from server.tools.validate_schema import (
    _normalize_entity_name,
    _types_compatible,
    compare_fields,
    extract_prisma_models,
    extract_pydantic_models,
    extract_ts_types,
    extract_zod_schemas,
    find_schema_files,
    find_type_files,
    handle_validate_schema,
    match_entities,
)

# ===========================================================================
# Helpers
# ===========================================================================


def _write_file(base: str, rel_path: str, content: str) -> str:
    """Create a file inside a temp directory, returning its absolute path."""
    full = os.path.join(base, rel_path.replace("/", os.sep))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return full


def _make_entity(
    name: str,
    fields: list[tuple[str, str, bool]] | None = None,
) -> dict[str, Any]:
    """Helper to build an entity dict."""
    field_list = [
        {"name": n, "type": t, "optional": o}
        for n, t, o in (fields or [])
    ]
    return {"name": name, "fields": field_list}


# ===========================================================================
# extract_ts_types
# ===========================================================================


class TestExtractTsTypes:
    def test_simple_interface(self) -> None:
        content = """
interface User {
  id: number;
  name: string;
  email: string;
}
"""
        entities = extract_ts_types(content)
        assert len(entities) == 1
        assert entities[0]["name"] == "User"
        fields = {f["name"]: f for f in entities[0]["fields"]}
        assert "id" in fields
        assert fields["id"]["type"] == "number"
        assert fields["name"]["type"] == "string"
        assert not fields["id"]["optional"]

    def test_optional_fields(self) -> None:
        content = """
interface Profile {
  id: number;
  bio?: string;
  avatar?: string;
}
"""
        entities = extract_ts_types(content)
        assert len(entities) == 1
        fields = {f["name"]: f for f in entities[0]["fields"]}
        assert not fields["id"]["optional"]
        assert fields["bio"]["optional"]
        assert fields["avatar"]["optional"]

    def test_exported_interface(self) -> None:
        content = """
export interface Report {
  id: string;
  title: string;
  createdAt: string;
}
"""
        entities = extract_ts_types(content)
        assert len(entities) == 1
        assert entities[0]["name"] == "Report"

    def test_type_alias(self) -> None:
        content = """
type Report = {
  id: string;
  title: string;
}
"""
        entities = extract_ts_types(content)
        assert len(entities) == 1
        assert entities[0]["name"] == "Report"
        assert len(entities[0]["fields"]) == 2

    def test_exported_type_alias(self) -> None:
        content = """
export type Item = {
  name: string;
  price: number;
}
"""
        entities = extract_ts_types(content)
        assert len(entities) == 1
        assert entities[0]["name"] == "Item"

    def test_readonly_field(self) -> None:
        content = """
interface Config {
  readonly apiKey: string;
  timeout: number;
}
"""
        entities = extract_ts_types(content)
        fields = {f["name"]: f for f in entities[0]["fields"]}
        assert "apiKey" in fields
        assert fields["apiKey"]["type"] == "string"

    def test_multiline_interface(self) -> None:
        content = """
interface Product {
  id: number;
  name: string;
  description: string;
  price: number;
  tags: string[];
}
"""
        entities = extract_ts_types(content)
        assert len(entities) == 1
        assert len(entities[0]["fields"]) == 5

    def test_multiple_interfaces(self) -> None:
        content = """
interface User {
  id: number;
  name: string;
}

interface Post {
  id: number;
  title: string;
  authorId: number;
}
"""
        entities = extract_ts_types(content)
        assert len(entities) == 2
        names = {e["name"] for e in entities}
        assert names == {"User", "Post"}

    def test_empty_content(self) -> None:
        assert extract_ts_types("") == []

    def test_no_types_found(self) -> None:
        content = """
const x = 1;
function hello() { return 'hi'; }
"""
        assert extract_ts_types(content) == []

    def test_interface_with_extends(self) -> None:
        content = """
interface Admin extends User {
  role: string;
  permissions: string[];
}
"""
        entities = extract_ts_types(content)
        assert len(entities) == 1
        assert entities[0]["name"] == "Admin"

    def test_array_type(self) -> None:
        content = """
interface Order {
  items: string[];
  counts: number[];
}
"""
        entities = extract_ts_types(content)
        fields = {f["name"]: f for f in entities[0]["fields"]}
        assert fields["items"]["type"] == "string[]"
        assert fields["counts"]["type"] == "number[]"


# ===========================================================================
# extract_pydantic_models
# ===========================================================================


class TestExtractPydanticModels:
    def test_simple_model(self) -> None:
        content = """
from pydantic import BaseModel

class User(BaseModel):
    id: int
    name: str
    email: str
"""
        entities = extract_pydantic_models(content)
        assert len(entities) == 1
        assert entities[0]["name"] == "User"
        fields = {f["name"]: f for f in entities[0]["fields"]}
        assert fields["id"]["type"] == "int"
        assert fields["name"]["type"] == "str"

    def test_optional_field(self) -> None:
        content = """
from pydantic import BaseModel
from typing import Optional

class Profile(BaseModel):
    id: int
    bio: Optional[str]
    avatar: str | None
"""
        entities = extract_pydantic_models(content)
        assert len(entities) == 1
        fields = {f["name"]: f for f in entities[0]["fields"]}
        assert not fields["id"]["optional"]
        assert fields["bio"]["optional"]
        assert fields["avatar"]["optional"]

    def test_default_value(self) -> None:
        content = """
from pydantic import BaseModel

class Config(BaseModel):
    timeout: int = 30
    retries: int = 3
    name: str = "default"
"""
        entities = extract_pydantic_models(content)
        assert len(entities) == 1
        fields = {f["name"]: f for f in entities[0]["fields"]}
        assert fields["timeout"]["type"] == "int"
        assert fields["retries"]["type"] == "int"
        assert fields["name"]["type"] == "str"

    def test_field_with_field_function(self) -> None:
        content = """
from pydantic import BaseModel, Field

class Report(BaseModel):
    title: str = Field(min_length=1)
    score: float = Field(ge=0, le=100)
"""
        entities = extract_pydantic_models(content)
        assert len(entities) == 1
        fields = {f["name"]: f for f in entities[0]["fields"]}
        assert fields["title"]["type"] == "str"
        assert fields["score"]["type"] == "float"

    def test_multiple_models(self) -> None:
        content = """
from pydantic import BaseModel

class User(BaseModel):
    id: int
    name: str

class Post(BaseModel):
    id: int
    title: str
    author_id: int
"""
        entities = extract_pydantic_models(content)
        assert len(entities) == 2
        names = {e["name"] for e in entities}
        assert names == {"User", "Post"}

    def test_skips_model_config(self) -> None:
        content = """
from pydantic import BaseModel

class User(BaseModel):
    model_config = {"strict": True}
    id: int
    name: str
"""
        entities = extract_pydantic_models(content)
        field_names = [f["name"] for f in entities[0]["fields"]]
        assert "model_config" not in field_names

    def test_empty_content(self) -> None:
        assert extract_pydantic_models("") == []

    def test_list_type(self) -> None:
        content = """
from pydantic import BaseModel

class Order(BaseModel):
    items: list[str]
    counts: list[int]
"""
        entities = extract_pydantic_models(content)
        fields = {f["name"]: f for f in entities[0]["fields"]}
        assert fields["items"]["type"] == "list[str]"
        assert fields["counts"]["type"] == "list[int]"


# ===========================================================================
# extract_zod_schemas
# ===========================================================================


class TestExtractZodSchemas:
    def test_simple_schema(self) -> None:
        content = """
const userSchema = z.object({
  id: z.number(),
  name: z.string(),
  email: z.string(),
})
"""
        entities = extract_zod_schemas(content)
        assert len(entities) == 1
        assert entities[0]["name"] == "userSchema"
        fields = {f["name"]: f for f in entities[0]["fields"]}
        assert fields["id"]["type"] == "number"
        assert fields["name"]["type"] == "string"

    def test_exported_schema(self) -> None:
        content = """
export const reportSchema = z.object({
  title: z.string(),
  score: z.number(),
})
"""
        entities = extract_zod_schemas(content)
        assert len(entities) == 1
        assert entities[0]["name"] == "reportSchema"

    def test_optional_field(self) -> None:
        content = """
const profileSchema = z.object({
  id: z.number(),
  bio: z.string().optional(),
})
"""
        entities = extract_zod_schemas(content)
        fields = {f["name"]: f for f in entities[0]["fields"]}
        assert fields["bio"]["optional"]

    def test_empty_content(self) -> None:
        assert extract_zod_schemas("") == []

    def test_multiple_schemas(self) -> None:
        content = """
const userSchema = z.object({
  id: z.number(),
  name: z.string(),
})

const postSchema = z.object({
  id: z.number(),
  title: z.string(),
})
"""
        entities = extract_zod_schemas(content)
        assert len(entities) == 2


# ===========================================================================
# extract_prisma_models
# ===========================================================================


class TestExtractPrismaModels:
    def test_simple_model(self) -> None:
        content = """
model User {
  id    Int      @id @default(autoincrement())
  name  String
  email String   @unique
}
"""
        entities = extract_prisma_models(content)
        assert len(entities) == 1
        assert entities[0]["name"] == "User"
        fields = {f["name"]: f for f in entities[0]["fields"]}
        assert fields["id"]["type"] == "Int"
        assert fields["name"]["type"] == "String"

    def test_optional_field(self) -> None:
        content = """
model Profile {
  id     Int     @id
  bio    String?
  avatar String?
}
"""
        entities = extract_prisma_models(content)
        fields = {f["name"]: f for f in entities[0]["fields"]}
        assert not fields["id"]["optional"]
        assert fields["bio"]["optional"]

    def test_array_field(self) -> None:
        content = """
model Post {
  id    Int      @id
  tags  String[]
}
"""
        entities = extract_prisma_models(content)
        fields = {f["name"]: f for f in entities[0]["fields"]}
        assert fields["tags"]["type"] == "String[]"

    def test_datetime_field(self) -> None:
        content = """
model Event {
  id        Int      @id
  createdAt DateTime @default(now())
}
"""
        entities = extract_prisma_models(content)
        fields = {f["name"]: f for f in entities[0]["fields"]}
        assert fields["createdAt"]["type"] == "DateTime"

    def test_multiple_models(self) -> None:
        content = """
model User {
  id   Int    @id
  name String
}

model Post {
  id    Int    @id
  title String
}
"""
        entities = extract_prisma_models(content)
        assert len(entities) == 2
        names = {e["name"] for e in entities}
        assert names == {"User", "Post"}

    def test_empty_content(self) -> None:
        assert extract_prisma_models("") == []


# ===========================================================================
# match_entities
# ===========================================================================


class TestMatchEntities:
    def test_exact_match(self) -> None:
        fe = [_make_entity("User", [("id", "number", False)])]
        be = [_make_entity("User", [("id", "int", False)])]
        matches = match_entities(fe, be)
        assert len(matches) == 1
        assert matches[0][0]["name"] == "User"
        assert matches[0][1]["name"] == "User"

    def test_case_insensitive(self) -> None:
        fe = [_make_entity("user", [("id", "number", False)])]
        be = [_make_entity("User", [("id", "int", False)])]
        matches = match_entities(fe, be)
        assert len(matches) == 1

    def test_suffix_stripping_model(self) -> None:
        fe = [_make_entity("Report", [("id", "string", False)])]
        be = [_make_entity("ReportModel", [("id", "str", False)])]
        matches = match_entities(fe, be)
        assert len(matches) == 1

    def test_suffix_stripping_schema(self) -> None:
        fe = [_make_entity("Report", [("id", "string", False)])]
        be = [_make_entity("ReportSchema", [("id", "str", False)])]
        matches = match_entities(fe, be)
        assert len(matches) == 1

    def test_suffix_stripping_response(self) -> None:
        fe = [_make_entity("UserResponse", [("id", "number", False)])]
        be = [_make_entity("User", [("id", "int", False)])]
        matches = match_entities(fe, be)
        assert len(matches) == 1

    def test_no_match(self) -> None:
        fe = [_make_entity("User", [("id", "number", False)])]
        be = [_make_entity("Product", [("id", "int", False)])]
        matches = match_entities(fe, be)
        assert len(matches) == 0

    def test_multiple_matches(self) -> None:
        fe = [
            _make_entity("User", [("id", "number", False)]),
            _make_entity("Post", [("id", "number", False)]),
        ]
        be = [
            _make_entity("UserModel", [("id", "int", False)]),
            _make_entity("PostModel", [("id", "int", False)]),
        ]
        matches = match_entities(fe, be)
        assert len(matches) == 2

    def test_partial_match(self) -> None:
        fe = [
            _make_entity("User", [("id", "number", False)]),
            _make_entity("Filter", [("q", "string", False)]),
        ]
        be = [_make_entity("User", [("id", "int", False)])]
        matches = match_entities(fe, be)
        assert len(matches) == 1
        assert matches[0][0]["name"] == "User"


# ===========================================================================
# _normalize_entity_name
# ===========================================================================


class TestNormalizeEntityName:
    def test_plain_name(self) -> None:
        assert _normalize_entity_name("User") == "user"

    def test_model_suffix(self) -> None:
        assert _normalize_entity_name("UserModel") == "user"

    def test_schema_suffix(self) -> None:
        assert _normalize_entity_name("UserSchema") == "user"

    def test_response_suffix(self) -> None:
        assert _normalize_entity_name("UserResponse") == "user"

    def test_request_suffix(self) -> None:
        assert _normalize_entity_name("UserRequest") == "user"

    def test_dto_suffix(self) -> None:
        assert _normalize_entity_name("UserDTO") == "user"

    def test_only_suffix(self) -> None:
        # "Model" alone should not be stripped to empty
        assert _normalize_entity_name("Model") == "model"


# ===========================================================================
# _types_compatible
# ===========================================================================


class TestTypesCompatible:
    def test_string_str(self) -> None:
        assert _types_compatible("string", "str")

    def test_number_int(self) -> None:
        assert _types_compatible("number", "int")

    def test_number_float(self) -> None:
        assert _types_compatible("number", "float")

    def test_boolean_bool(self) -> None:
        assert _types_compatible("boolean", "bool")

    def test_string_datetime(self) -> None:
        assert _types_compatible("string", "datetime")

    def test_string_uuid(self) -> None:
        assert _types_compatible("string", "UUID")

    def test_string_array(self) -> None:
        assert _types_compatible("string[]", "list[str]")

    def test_incompatible(self) -> None:
        assert not _types_compatible("string", "int")

    def test_incompatible_number_bool(self) -> None:
        assert not _types_compatible("number", "bool")

    def test_exact_match(self) -> None:
        assert _types_compatible("string", "string")

    def test_case_insensitive(self) -> None:
        assert _types_compatible("String", "str")


# ===========================================================================
# compare_fields
# ===========================================================================


class TestCompareFields:
    def test_all_matching(self) -> None:
        fe = _make_entity("User", [
            ("id", "number", False),
            ("name", "string", False),
        ])
        be = _make_entity("User", [
            ("id", "int", False),
            ("name", "str", False),
        ])
        result = compare_fields(fe, be)
        assert result["status"] == "OK"
        assert set(result["matching_fields"]) == {"id", "name"}

    def test_type_mismatch(self) -> None:
        fe = _make_entity("User", [
            ("id", "string", False),
            ("name", "string", False),
        ])
        be = _make_entity("User", [
            ("id", "int", False),
            ("name", "str", False),
        ])
        result = compare_fields(fe, be)
        assert result["status"] == "MISMATCH"
        errors = [i for i in result["issues"] if i["severity"] == "error"]
        assert len(errors) == 1
        assert errors[0]["field"] == "id"

    def test_frontend_only_field(self) -> None:
        fe = _make_entity("User", [
            ("id", "number", False),
            ("extra", "string", False),
        ])
        be = _make_entity("User", [
            ("id", "int", False),
        ])
        result = compare_fields(fe, be)
        assert result["status"] == "WARNING"
        warnings = [i for i in result["issues"] if i["severity"] == "warning"]
        assert len(warnings) == 1
        assert warnings[0]["field"] == "extra"

    def test_backend_only_field(self) -> None:
        fe = _make_entity("User", [
            ("id", "number", False),
        ])
        be = _make_entity("User", [
            ("id", "int", False),
            ("internal", "str", False),
        ])
        result = compare_fields(fe, be)
        assert result["status"] == "OK"
        infos = [i for i in result["issues"] if i["severity"] == "info"]
        assert len(infos) == 1
        assert infos[0]["field"] == "internal"

    def test_serialization_note(self) -> None:
        fe = _make_entity("Event", [
            ("created_at", "string", False),
        ])
        be = _make_entity("Event", [
            ("created_at", "datetime", False),
        ])
        result = compare_fields(fe, be)
        assert result["status"] == "OK"
        assert "created_at" in result["matching_fields"]
        infos = [i for i in result["issues"] if i["severity"] == "info"]
        assert len(infos) == 1
        assert "직렬화" in infos[0]["note"]

    def test_empty_entities(self) -> None:
        fe = _make_entity("Empty", [])
        be = _make_entity("Empty", [])
        result = compare_fields(fe, be)
        assert result["status"] == "OK"
        assert result["matching_fields"] == []
        assert result["issues"] == []


# ===========================================================================
# find_type_files
# ===========================================================================


class TestFindTypeFiles:
    def test_types_directory(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/types/user.ts", "interface User { id: number; }")
        results = find_type_files(str(tmp_path))
        assert len(results) == 1
        assert results[0][0] == "src/types/user.ts"

    def test_dts_file(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/api.d.ts", "interface API { url: string; }")
        results = find_type_files(str(tmp_path))
        assert len(results) == 1

    def test_interfaces_directory(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/interfaces/user.ts", "interface User { id: number; }")
        results = find_type_files(str(tmp_path))
        assert len(results) == 1

    def test_skips_node_modules(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "node_modules/types/user.ts", "interface User {}")
        results = find_type_files(str(tmp_path))
        assert len(results) == 0

    def test_skips_test_files(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/types/user.test.ts", "test content")
        results = find_type_files(str(tmp_path))
        assert len(results) == 0

    def test_explicit_frontend_dir(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "frontend/types/user.ts", "interface User { id: number; }")
        results = find_type_files(str(tmp_path), "frontend/types")
        assert len(results) == 1

    def test_no_type_files(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "src/app.ts", "const x = 1;")
        results = find_type_files(str(tmp_path))
        assert len(results) == 0

    def test_nonexistent_frontend_dir(self, tmp_path: Path) -> None:
        results = find_type_files(str(tmp_path), "nonexistent")
        assert len(results) == 0


# ===========================================================================
# find_schema_files
# ===========================================================================


class TestFindSchemaFiles:
    def test_pydantic_file(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "server/models/user.py", (
            "from pydantic import BaseModel\n\n"
            "class User(BaseModel):\n"
            "    id: int\n"
        ))
        results = find_schema_files(str(tmp_path))
        assert len(results) == 1
        assert results[0][2] == "pydantic"

    def test_prisma_file(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "prisma/schema.prisma", (
            "model User {\n  id Int @id\n}\n"
        ))
        results = find_schema_files(str(tmp_path))
        assert len(results) == 1
        assert results[0][2] == "prisma"

    def test_zod_file(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "server/schemas/user.ts", (
            "import { z } from 'zod';\n\n"
            "const userSchema = z.object({\n"
            "  id: z.number(),\n"
            "})\n"
        ))
        results = find_schema_files(str(tmp_path))
        assert len(results) == 1
        assert results[0][2] == "zod"

    def test_skips_node_modules(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "node_modules/lib/schema.prisma", "model X { id Int }")
        results = find_schema_files(str(tmp_path))
        assert len(results) == 0

    def test_explicit_backend_dir(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "api/models/user.py", (
            "from pydantic import BaseModel\n\n"
            "class User(BaseModel):\n"
            "    id: int\n"
        ))
        results = find_schema_files(str(tmp_path), "api/models")
        assert len(results) == 1

    def test_no_schema_files(self, tmp_path: Path) -> None:
        _write_file(str(tmp_path), "server/app.py", "print('hello')")
        results = find_schema_files(str(tmp_path))
        assert len(results) == 0


# ===========================================================================
# Full pipeline (handle_validate_schema)
# ===========================================================================


class TestHandleValidateSchema:
    @pytest.mark.asyncio
    async def test_full_pipeline_match(self, tmp_path: Path) -> None:
        """Full pipeline: frontend TS types matching backend Pydantic models."""
        _write_file(str(tmp_path), "src/types/report.ts", """
export interface Report {
  id: string;
  title: string;
  created_at: string;
}
""")
        _write_file(str(tmp_path), "server/models/report.py", """
from pydantic import BaseModel
from datetime import datetime

class ReportModel(BaseModel):
    id: str
    title: str
    created_at: datetime
""")
        result = await handle_validate_schema({"project_dir": str(tmp_path)})
        assert len(result) == 1
        data = json.loads(result[0].text)

        assert data["status"] == "OK"
        assert len(data["matched_entities"]) == 1
        entity = data["matched_entities"][0]
        assert entity["name"] == "Report"
        assert "id" in entity["matching_fields"]
        assert "title" in entity["matching_fields"]

    @pytest.mark.asyncio
    async def test_full_pipeline_mismatch(self, tmp_path: Path) -> None:
        """Full pipeline with type mismatch."""
        _write_file(str(tmp_path), "src/types/user.ts", """
interface User {
  id: string;
  age: string;
}
""")
        _write_file(str(tmp_path), "server/models/user.py", """
from pydantic import BaseModel

class User(BaseModel):
    id: str
    age: int
""")
        result = await handle_validate_schema({"project_dir": str(tmp_path)})
        data = json.loads(result[0].text)

        assert data["status"] == "MISMATCH"
        errors = [
            i for e in data["matched_entities"]
            for i in e["issues"]
            if i["severity"] == "error"
        ]
        assert len(errors) == 1
        assert errors[0]["field"] == "age"

    @pytest.mark.asyncio
    async def test_full_pipeline_unmatched(self, tmp_path: Path) -> None:
        """Full pipeline with unmatched entities."""
        _write_file(str(tmp_path), "src/types/filter.ts", """
interface ReportFilter {
  keyword: string;
}
""")
        _write_file(str(tmp_path), "server/models/internal.py", """
from pydantic import BaseModel

class ReportInternal(BaseModel):
    secret: str
""")
        result = await handle_validate_schema({"project_dir": str(tmp_path)})
        data = json.loads(result[0].text)

        assert "ReportFilter" in data["unmatched_frontend"]
        assert "ReportInternal" in data["unmatched_backend"]

    @pytest.mark.asyncio
    async def test_empty_project(self, tmp_path: Path) -> None:
        """Empty project with no type/schema files."""
        result = await handle_validate_schema({"project_dir": str(tmp_path)})
        data = json.loads(result[0].text)
        assert data["status"] == "EMPTY"
        assert data["matched_entities"] == []

    @pytest.mark.asyncio
    async def test_only_frontend(self, tmp_path: Path) -> None:
        """Project with only frontend types, no backend schemas."""
        _write_file(str(tmp_path), "src/types/user.ts", """
interface User {
  id: number;
  name: string;
}
""")
        result = await handle_validate_schema({"project_dir": str(tmp_path)})
        data = json.loads(result[0].text)
        assert data["status"] == "NO_MATCH"
        assert len(data["unmatched_frontend"]) == 1

    @pytest.mark.asyncio
    async def test_only_backend(self, tmp_path: Path) -> None:
        """Project with only backend schemas, no frontend types."""
        _write_file(str(tmp_path), "server/models/user.py", """
from pydantic import BaseModel

class User(BaseModel):
    id: int
    name: str
""")
        result = await handle_validate_schema({"project_dir": str(tmp_path)})
        data = json.loads(result[0].text)
        assert data["status"] == "NO_MATCH"
        assert len(data["unmatched_backend"]) == 1

    @pytest.mark.asyncio
    async def test_missing_project_dir(self) -> None:
        """Should raise ValueError for missing project_dir."""
        with pytest.raises(ValueError, match="project_dir"):
            await handle_validate_schema({})

    @pytest.mark.asyncio
    async def test_nonexistent_project_dir(self) -> None:
        """Should raise ValueError for nonexistent directory."""
        with pytest.raises(ValueError, match="디렉토리"):
            await handle_validate_schema({"project_dir": "/nonexistent/path/xyz"})

    @pytest.mark.asyncio
    async def test_prisma_pipeline(self, tmp_path: Path) -> None:
        """Full pipeline with Prisma backend."""
        _write_file(str(tmp_path), "src/types/user.ts", """
interface User {
  id: number;
  name: string;
  email: string;
}
""")
        _write_file(str(tmp_path), "prisma/schema.prisma", """
model User {
  id    Int    @id @default(autoincrement())
  name  String
  email String @unique
}
""")
        result = await handle_validate_schema({"project_dir": str(tmp_path)})
        data = json.loads(result[0].text)

        assert data["status"] == "OK"
        assert len(data["matched_entities"]) == 1

    @pytest.mark.asyncio
    async def test_zod_pipeline(self, tmp_path: Path) -> None:
        """Full pipeline with Zod backend."""
        _write_file(str(tmp_path), "src/types/user.ts", """
interface User {
  id: number;
  name: string;
}
""")
        _write_file(str(tmp_path), "server/schemas/user.ts", """
import { z } from 'zod';

const userSchema = z.object({
  id: z.number(),
  name: z.string(),
})
""")
        result = await handle_validate_schema({"project_dir": str(tmp_path)})
        data = json.loads(result[0].text)
        # Zod schema name is "userSchema" which normalizes to "user"
        # Frontend "User" normalizes to "user" -> match
        assert len(data["matched_entities"]) == 1

    @pytest.mark.asyncio
    async def test_auto_detect_ignores_sentinel(self, tmp_path: Path) -> None:
        """auto-detect sentinel value should be treated as None."""
        _write_file(str(tmp_path), "src/types/user.ts", """
interface User {
  id: number;
}
""")
        result = await handle_validate_schema({
            "project_dir": str(tmp_path),
            "frontend_types_dir": "auto-detect",
            "backend_schema_dir": "auto-detect",
        })
        data = json.loads(result[0].text)
        # Should not crash, just find frontend types
        assert "unmatched_frontend" in data

    @pytest.mark.asyncio
    async def test_summary_counts(self, tmp_path: Path) -> None:
        """Verify summary counts are correct."""
        _write_file(str(tmp_path), "src/types/report.ts", """
interface Report {
  id: string;
  title: string;
  score: string;
}
""")
        _write_file(str(tmp_path), "server/models/report.py", """
from pydantic import BaseModel

class Report(BaseModel):
    id: str
    title: str
    score: int
""")
        result = await handle_validate_schema({"project_dir": str(tmp_path)})
        data = json.loads(result[0].text)

        summary = data["summary"]
        assert summary["total_entities_checked"] == 1
        assert summary["errors"] == 1
        assert summary["mismatched"] == 1

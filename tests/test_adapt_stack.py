"""Unit tests for server/tools/adapt_stack.py.

Tests focus on stack detection, migration plan generation,
file pattern matching, and effort estimation.
All tests run without network or filesystem access.
"""

from __future__ import annotations

import pytest

from server.tools.adapt_stack import (
    STACK_MIGRATIONS,
    _estimate_effort,
    build_migration_plan,
    detect_stack_from_package_json,
    detect_stack_from_requirements,
    find_affected_files,
)


# ===========================================================================
# detect_stack_from_package_json
# ===========================================================================

class TestDetectStackFromPackageJson:
    def test_detect_prisma(self) -> None:
        pkg = {"dependencies": {"@prisma/client": "^5.0.0"}}
        stack = detect_stack_from_package_json(pkg)
        assert stack.get("db") == "prisma"

    def test_detect_next_auth(self) -> None:
        pkg = {"dependencies": {"next-auth": "^4.0.0"}}
        stack = detect_stack_from_package_json(pkg)
        assert stack.get("auth") == "next-auth"

    def test_detect_supabase(self) -> None:
        pkg = {"dependencies": {"@supabase/supabase-js": "^2.0.0"}}
        stack = detect_stack_from_package_json(pkg)
        # supabase appears in both db and auth categories
        assert "supabase" in stack.values()

    def test_detect_multiple_stacks(self) -> None:
        pkg = {
            "dependencies": {
                "@vercel/postgres": "^0.5.0",
                "next-auth": "^4.0.0",
                "@vercel/blob": "^0.12.0",
            },
        }
        stack = detect_stack_from_package_json(pkg)
        assert stack.get("db") == "vercel-postgres"
        assert stack.get("auth") == "next-auth"
        assert stack.get("storage") == "vercel-blob"

    def test_detect_express(self) -> None:
        pkg = {"dependencies": {"express": "^4.18.0"}}
        stack = detect_stack_from_package_json(pkg)
        assert stack.get("framework") == "express"

    def test_dev_dependencies(self) -> None:
        pkg = {"devDependencies": {"prisma": "^5.0.0"}}
        stack = detect_stack_from_package_json(pkg)
        assert stack.get("db") == "prisma"

    def test_empty_package_json(self) -> None:
        pkg: dict = {}
        stack = detect_stack_from_package_json(pkg)
        assert stack == {}

    def test_no_matching_deps(self) -> None:
        pkg = {"dependencies": {"lodash": "^4.0.0", "axios": "^1.0.0"}}
        stack = detect_stack_from_package_json(pkg)
        assert stack == {}


# ===========================================================================
# detect_stack_from_requirements
# ===========================================================================

class TestDetectStackFromRequirements:
    def test_detect_fastapi(self) -> None:
        text = "fastapi>=0.100.0\nuvicorn>=0.23.0"
        stack = detect_stack_from_requirements(text)
        assert stack.get("framework") == "fastapi"

    def test_detect_django(self) -> None:
        text = "Django==4.2.0\npsycopg2-binary==2.9.0"
        stack = detect_stack_from_requirements(text)
        assert stack.get("framework") == "django"
        assert stack.get("db") == "postgres"

    def test_detect_pymongo(self) -> None:
        text = "pymongo==4.0.0\nflask==3.0.0"
        stack = detect_stack_from_requirements(text)
        assert stack.get("db") == "mongodb"
        assert stack.get("framework") == "flask"

    def test_empty_requirements(self) -> None:
        stack = detect_stack_from_requirements("")
        assert stack == {}


# ===========================================================================
# find_affected_files
# ===========================================================================

class TestFindAffectedFiles:
    SAMPLE_FILES = [
        "lib/db.ts",
        "lib/db.js",
        "lib/prisma.ts",
        "prisma/schema.prisma",
        "prisma/migrations/001_init.sql",
        "auth.ts",
        "middleware.ts",
        "app/api/auth/route.ts",
        "components/Header.tsx",
    ]

    def test_wildcard_pattern(self) -> None:
        affected = find_affected_files(self.SAMPLE_FILES, ["lib/db.*"])
        assert "lib/db.ts" in affected
        assert "lib/db.js" in affected

    def test_directory_glob(self) -> None:
        affected = find_affected_files(self.SAMPLE_FILES, ["prisma/**"])
        assert "prisma/schema.prisma" in affected
        assert "prisma/migrations/001_init.sql" in affected

    def test_multiple_patterns(self) -> None:
        affected = find_affected_files(
            self.SAMPLE_FILES, ["auth.*", "middleware.*"]
        )
        assert "auth.ts" in affected
        assert "middleware.ts" in affected

    def test_no_match(self) -> None:
        affected = find_affected_files(self.SAMPLE_FILES, ["nonexistent/**"])
        assert affected == []


# ===========================================================================
# build_migration_plan
# ===========================================================================

class TestBuildMigrationPlan:
    def test_single_migration(self) -> None:
        current = {"db": "vercel-postgres"}
        target = {"db": "firestore"}
        files = ["lib/db.ts", "prisma/schema.prisma"]
        plan = build_migration_plan(current, target, files)

        assert plan["current_stack"] == current
        assert plan["target_stack"] == target
        assert len(plan["migrations"]) == 1
        assert plan["migrations"][0]["from"] == "vercel-postgres"
        assert plan["migrations"][0]["to"] == "firestore"
        assert "firebase" in plan["install_command"]

    def test_multiple_migrations(self) -> None:
        current = {"db": "vercel-postgres", "auth": "next-auth"}
        target = {"db": "firestore", "auth": "firebase-auth"}
        files = ["lib/db.ts", "auth.ts", "middleware.ts"]
        plan = build_migration_plan(current, target, files)

        assert len(plan["migrations"]) == 2
        assert "npm install" in plan["install_command"]
        assert "npm uninstall" in plan["install_command"]

    def test_same_stack_no_migration(self) -> None:
        current = {"db": "firestore"}
        target = {"db": "firestore"}
        plan = build_migration_plan(current, target, [])

        assert len(plan["migrations"]) == 0
        assert plan["install_command"] == ""

    def test_unknown_migration_path(self) -> None:
        current = {"db": "redis"}
        target = {"db": "dynamodb"}
        plan = build_migration_plan(current, target, [])

        assert len(plan["migrations"]) == 1
        assert plan["migrations"][0]["effort"] == "unknown"
        assert "수동 전환" in plan["migrations"][0]["notes"]

    def test_new_category_no_current(self) -> None:
        current: dict[str, str] = {}
        target = {"db": "firestore"}
        plan = build_migration_plan(current, target, [])

        assert len(plan["migrations"]) == 1
        assert plan["migrations"][0]["from"] == "none"
        assert plan["migrations"][0]["to"] == "firestore"


# ===========================================================================
# _estimate_effort
# ===========================================================================

class TestEstimateEffort:
    def test_no_migrations(self) -> None:
        assert _estimate_effort([]) == "none"

    def test_low_effort(self) -> None:
        migrations = [{"affected_files": ["a.ts"]}]
        assert _estimate_effort(migrations) == "low"

    def test_medium_effort(self) -> None:
        migrations = [
            {"affected_files": ["a.ts", "b.ts", "c.ts"]},
            {"affected_files": ["d.ts", "e.ts", "f.ts"]},
        ]
        assert _estimate_effort(migrations) == "medium"

    def test_high_effort_many_files(self) -> None:
        migrations = [{"affected_files": [f"f{i}.ts" for i in range(12)]}]
        assert _estimate_effort(migrations) == "high"

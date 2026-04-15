"""Unit tests for server/tools/extract_component.py.

Tests focus on keyword resolution, file matching, import extraction,
and dependency resolution. All tests run without network.
"""

from __future__ import annotations

from server.tools.extract_component import (
    _build_install_command,
    _extract_imports_from_content,
    _get_keywords_for_component,
    _match_files,
    _normalize_component,
    _resolve_npm_packages,
)

# ===========================================================================
# _normalize_component
# ===========================================================================

class TestNormalizeComponent:
    def test_lowercase_and_strip(self) -> None:
        assert _normalize_component("  Chat-Input  ") == "chat-input"

    def test_underscores_to_dashes(self) -> None:
        assert _normalize_component("auth_middleware") == "auth-middleware"

    def test_spaces_to_dashes(self) -> None:
        assert _normalize_component("chat input box") == "chat-input-box"


# ===========================================================================
# _get_keywords_for_component
# ===========================================================================

class TestGetKeywordsForComponent:
    def test_known_component_chat(self) -> None:
        keywords = _get_keywords_for_component("chat")
        assert "chat" in keywords
        assert "message" in keywords
        assert "conversation" in keywords

    def test_known_component_auth(self) -> None:
        keywords = _get_keywords_for_component("auth")
        assert "auth" in keywords
        assert "login" in keywords
        assert "session" in keywords

    def test_compound_component(self) -> None:
        keywords = _get_keywords_for_component("chat-input")
        # "chat" resolves to COMPONENT_PATTERNS["chat"],
        # "input" resolves to COMPONENT_PATTERNS["form"]
        assert "chat" in keywords
        assert "message" in keywords
        # "input" is part of the "form" category
        assert "input" in keywords

    def test_unknown_component_passes_through(self) -> None:
        keywords = _get_keywords_for_component("calendar")
        assert keywords == ["calendar"]

    def test_deduplication(self) -> None:
        keywords = _get_keywords_for_component("chat")
        assert len(keywords) == len(set(keywords))


# ===========================================================================
# _match_files
# ===========================================================================

class TestMatchFiles:
    SAMPLE_TREE = [
        "components/ChatInput.tsx",
        "components/MessageList.tsx",
        "hooks/useChat.ts",
        "lib/auth.ts",
        "middleware/auth.ts",
        "components/LoginForm.tsx",
        "types/chat.ts",
        "utils/helpers.ts",
        "node_modules/react/index.js",
        "dist/bundle.js",
        "public/logo.png",
    ]

    def test_chat_keywords_match(self) -> None:
        keywords = ["chat", "message"]
        matched = _match_files(self.SAMPLE_TREE, keywords)
        assert "components/ChatInput.tsx" in matched
        assert "components/MessageList.tsx" in matched
        assert "hooks/useChat.ts" in matched
        assert "types/chat.ts" in matched

    def test_auth_keywords_match(self) -> None:
        keywords = ["auth", "login"]
        matched = _match_files(self.SAMPLE_TREE, keywords)
        assert "lib/auth.ts" in matched
        assert "middleware/auth.ts" in matched
        assert "components/LoginForm.tsx" in matched

    def test_excludes_node_modules(self) -> None:
        keywords = ["react"]
        matched = _match_files(self.SAMPLE_TREE, keywords)
        assert not any("node_modules" in f for f in matched)

    def test_excludes_dist(self) -> None:
        keywords = ["bundle"]
        matched = _match_files(self.SAMPLE_TREE, keywords)
        assert not any("dist" in f for f in matched)

    def test_excludes_images(self) -> None:
        keywords = ["logo"]
        matched = _match_files(self.SAMPLE_TREE, keywords)
        assert not any(f.endswith(".png") for f in matched)

    def test_empty_keywords_no_match(self) -> None:
        matched = _match_files(self.SAMPLE_TREE, [])
        assert matched == []

    def test_no_matching_files(self) -> None:
        keywords = ["payment", "stripe"]
        matched = _match_files(self.SAMPLE_TREE, keywords)
        assert matched == []


# ===========================================================================
# _extract_imports_from_content
# ===========================================================================

class TestExtractImports:
    def test_es_import(self) -> None:
        content = 'import { useState } from "react";'
        imports = _extract_imports_from_content(content)
        assert "react" in imports

    def test_es_import_single_quotes(self) -> None:
        content = "import Chat from '@assistant-ui/react';"
        imports = _extract_imports_from_content(content)
        assert "@assistant-ui/react" in imports

    def test_require(self) -> None:
        content = 'const express = require("express");'
        imports = _extract_imports_from_content(content)
        assert "express" in imports

    def test_relative_import(self) -> None:
        content = 'import { helper } from "./utils";'
        imports = _extract_imports_from_content(content)
        assert "./utils" in imports

    def test_multiple_imports(self) -> None:
        content = """
import React from "react";
import { Button } from "@radix-ui/react-button";
const lodash = require("lodash");
"""
        imports = _extract_imports_from_content(content)
        assert "react" in imports
        assert "@radix-ui/react-button" in imports
        assert "lodash" in imports


# ===========================================================================
# _resolve_npm_packages
# ===========================================================================

class TestResolveNpmPackages:
    def test_filters_relative_imports(self) -> None:
        imports = ["./utils", "../lib/helper", "react"]
        packages = _resolve_npm_packages(imports)
        assert "./utils" not in packages
        assert "../lib/helper" not in packages
        assert "react" not in packages  # builtin

    def test_scoped_package(self) -> None:
        imports = ["@radix-ui/react-button"]
        packages = _resolve_npm_packages(imports)
        assert "@radix-ui/react-button" in packages

    def test_subpath_import(self) -> None:
        imports = ["lodash/debounce"]
        packages = _resolve_npm_packages(imports)
        assert "lodash" in packages

    def test_filters_builtin_packages(self) -> None:
        imports = ["react", "react-dom", "next", "path", "fs"]
        packages = _resolve_npm_packages(imports)
        assert packages == []

    def test_deduplication(self) -> None:
        imports = ["lucide-react", "lucide-react/icons"]
        packages = _resolve_npm_packages(imports)
        assert packages.count("lucide-react") == 1


# ===========================================================================
# _build_install_command
# ===========================================================================

class TestBuildInstallCommand:
    def test_single_package(self) -> None:
        cmd = _build_install_command(["lucide-react"])
        assert cmd == "npm install lucide-react"

    def test_multiple_packages(self) -> None:
        cmd = _build_install_command(["@radix-ui/react-button", "lucide-react"])
        assert "npm install" in cmd
        assert "@radix-ui/react-button" in cmd
        assert "lucide-react" in cmd

    def test_empty_packages(self) -> None:
        cmd = _build_install_command([])
        assert cmd == ""

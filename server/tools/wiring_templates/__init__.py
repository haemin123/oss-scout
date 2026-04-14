"""Wiring code templates for generate_wiring tool.

Each module exports a ``generate(stack, config)`` function that returns
a wiring result dict with ``files``, ``usage_example``, and
``dependencies_needed``.
"""

from __future__ import annotations

from typing import Any

from server.tools.wiring_templates import (
    api_hook,
    auth_guard,
    db_crud,
    file_upload,
    form_handler,
    middleware,
    sse_stream,
    websocket,
)

_REGISTRY: dict[str, Any] = {
    "api-hook": api_hook,
    "auth-guard": auth_guard,
    "db-crud": db_crud,
    "file-upload": file_upload,
    "websocket": websocket,
    "sse-stream": sse_stream,
    "form-handler": form_handler,
    "middleware": middleware,
}


def get_template_module(wiring_type: str) -> Any:
    """Return the template module for a given wiring type.

    Raises ``KeyError`` if the wiring type is unknown.
    """
    mod = _REGISTRY.get(wiring_type)
    if mod is None:
        raise KeyError(
            f"Unknown wiring_type: {wiring_type}. "
            f"Supported: {sorted(_REGISTRY.keys())}"
        )
    return mod

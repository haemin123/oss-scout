"""API hook wiring template.

Generates fetch/HTTP client hooks for React, Vue, and Python projects.
"""

from __future__ import annotations

from typing import Any


def generate(stack: dict[str, str | None], config: dict[str, Any]) -> dict[str, Any]:
    """Generate API hook wiring code based on detected stack."""
    framework = stack.get("framework")
    endpoint = config.get("endpoint", "/api/data")
    method = config.get("method", "POST").upper()
    streaming = config.get("streaming", False)
    auth_required = config.get("auth_required", False)

    if framework in ("nextjs", "react"):
        return _react_hook(endpoint, method, streaming, auth_required)
    if framework == "vue":
        return _vue_composable(endpoint, method, streaming, auth_required)
    if stack.get("language") == "python":
        return _python_wrapper(endpoint, method, streaming, auth_required)
    # Default: React hook (most common)
    return _react_hook(endpoint, method, streaming, auth_required)


def _auth_header_line(indent: str) -> str:
    return (
        f'{indent}const token = typeof window !== "undefined"'
        f" ? localStorage.getItem(\"token\") : null;\n"
        f"{indent}const authHeaders: Record<string, string> = token"
        f' ? {{ Authorization: `Bearer ${{token}}` }} : {{}};\n'
    )


def _react_hook(
    endpoint: str,
    method: str,
    streaming: bool,
    auth_required: bool,
) -> dict[str, Any]:
    auth_header = ""
    auth_spread = ""
    if auth_required:
        auth_header = _auth_header_line("    ")
        auth_spread = "        ...authHeaders,\n"

    if streaming:
        content = f'''import {{ useState, useCallback }} from "react";

export function useApiStream<T>(endpoint: string = "{endpoint}") {{
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chunks, setChunks] = useState<string[]>([]);

  const execute = useCallback(async (body?: unknown) => {{
    setLoading(true);
    setError(null);
    setChunks([]);
{auth_header}    try {{
      const res = await fetch(endpoint, {{
        method: "{method}",
        headers: {{
          "Content-Type": "application/json",
{auth_spread}        }},
        body: body ? JSON.stringify(body) : undefined,
      }});
      if (!res.ok) throw new Error(`HTTP ${{res.status}}`);
      const reader = res.body?.getReader();
      if (!reader) throw new Error("No reader available");
      const decoder = new TextDecoder();
      let result = "";
      while (true) {{
        const {{ done, value }} = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, {{ stream: true }});
        result += chunk;
        setChunks((prev) => [...prev, chunk]);
      }}
      setData(JSON.parse(result) as T);
    }} catch (e) {{
      setError(e instanceof Error ? e.message : "Unknown error");
    }} finally {{
      setLoading(false);
    }}
  }}, [endpoint]);

  return {{ data, loading, error, chunks, execute }};
}}
'''
    else:
        content = f'''import {{ useState, useCallback }} from "react";

export function useApi<T>(endpoint: string = "{endpoint}") {{
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const execute = useCallback(async (body?: unknown) => {{
    setLoading(true);
    setError(null);
{auth_header}    try {{
      const res = await fetch(endpoint, {{
        method: "{method}",
        headers: {{
          "Content-Type": "application/json",
{auth_spread}        }},
        body: body ? JSON.stringify(body) : undefined,
      }});
      if (!res.ok) throw new Error(`HTTP ${{res.status}}`);
      const json = (await res.json()) as T;
      setData(json);
    }} catch (e) {{
      setError(e instanceof Error ? e.message : "Unknown error");
    }} finally {{
      setLoading(false);
    }}
  }}, [endpoint]);

  return {{ data, loading, error, execute }};
}}
'''

    hook_name = "useApiStream" if streaming else "useApi"
    return {
        "files": [
            {
                "path": f"hooks/{hook_name}.ts",
                "content": content,
                "description": f"API {'스트리밍 ' if streaming else ''}호출 커스텀 훅",
            },
        ],
        "usage_example": (
            f'const {{ data, loading, error, execute }} = {hook_name}("{endpoint}");'
        ),
        "dependencies_needed": [],
    }


def _vue_composable(
    endpoint: str,
    method: str,
    streaming: bool,
    auth_required: bool,
) -> dict[str, Any]:
    auth_header = ""
    auth_spread = ""
    if auth_required:
        auth_header = (
            '    const token = localStorage.getItem("token");\n'
            "    const authHeaders: Record<string, string> = token"
            ' ? { Authorization: `Bearer ${token}` } : {};\n'
        )
        auth_spread = "          ...authHeaders,\n"

    content = f'''import {{ ref }} from "vue";

export function useApi<T>(url: string = "{endpoint}") {{
  const data = ref<T | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);

  async function execute(body?: unknown) {{
    loading.value = true;
    error.value = null;
{auth_header}    try {{
      const res = await fetch(url, {{
        method: "{method}",
        headers: {{
          "Content-Type": "application/json",
{auth_spread}        }},
        body: body ? JSON.stringify(body) : undefined,
      }});
      if (!res.ok) throw new Error(`HTTP ${{res.status}}`);
      data.value = (await res.json()) as T;
    }} catch (e) {{
      error.value = e instanceof Error ? e.message : "Unknown error";
    }} finally {{
      loading.value = false;
    }}
  }}

  return {{ data, loading, error, execute }};
}}
'''
    return {
        "files": [
            {
                "path": "composables/useApi.ts",
                "content": content,
                "description": "API 호출 Vue composable",
            },
        ],
        "usage_example": (
            f'const {{ data, loading, error, execute }} = useApi("{endpoint}");'
        ),
        "dependencies_needed": [],
    }


def _python_wrapper(
    endpoint: str,
    method: str,
    streaming: bool,
    auth_required: bool,
) -> dict[str, Any]:
    auth_param = ", token: str | None = None" if auth_required else ""
    auth_header = ""
    if auth_required:
        auth_header = (
            '    headers: dict[str, str] = {}\n'
            "    if token:\n"
            '        headers["Authorization"] = f"Bearer {token}"\n'
        )

    if streaming:
        content = f'''"""API streaming client wrapper."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx


async def api_stream(
    endpoint: str = "{endpoint}",
    body: dict | None = None{auth_param},
) -> AsyncIterator[str]:
    """Stream response chunks from the API endpoint."""
{auth_header}    async with httpx.AsyncClient() as client:
        async with client.stream(
            "{method}",
            endpoint,
            json=body,{"".join(chr(10) + "            headers=headers," if auth_required else "")}
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_text():
                yield chunk
'''
    else:
        content = f'''"""API client wrapper."""

from __future__ import annotations

from typing import Any

import httpx


async def api_call(
    endpoint: str = "{endpoint}",
    body: dict | None = None{auth_param},
) -> dict[str, Any]:
    """Call the API endpoint and return parsed JSON."""
{auth_header}    async with httpx.AsyncClient() as client:
        response = await client.request(
            "{method}",
            endpoint,
            json=body,{"".join(chr(10) + "            headers=headers," if auth_required else "")}
        )
        response.raise_for_status()
        return response.json()
'''

    filename = "api_stream_client.py" if streaming else "api_client.py"
    return {
        "files": [
            {
                "path": f"lib/{filename}",
                "content": content,
                "description": f"API {'스트리밍 ' if streaming else ''}클라이언트 래퍼",
            },
        ],
        "usage_example": (
            f'async for chunk in api_stream("{endpoint}", body={{"q": "hello"}})'
            if streaming
            else f'result = await api_call("{endpoint}", body={{"q": "hello"}})'
        ),
        "dependencies_needed": ["httpx"],
    }

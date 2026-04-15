"""SSE streaming wiring template.

Generates Server-Sent Events streaming code for AI chatbot and
real-time data scenarios.
"""

from __future__ import annotations

from typing import Any


def generate(stack: dict[str, str | None], config: dict[str, Any]) -> dict[str, Any]:
    """Generate SSE streaming wiring code based on detected stack."""
    framework = stack.get("framework")
    endpoint = config.get("endpoint", "/api/chat")

    if framework == "nextjs":
        return _nextjs_sse(endpoint)
    if framework in ("react",):
        return _react_hook(endpoint)
    if stack.get("language") == "python":
        return _python_sse(endpoint)
    # Default: Next.js (includes both client + server)
    return _nextjs_sse(endpoint)


def _nextjs_sse(endpoint: str) -> dict[str, Any]:
    server_content = '''import { NextRequest } from "next/server";

export const runtime = "edge";

export async function POST(request: NextRequest) {
  const { message } = await request.json();

  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      // Replace this with your actual AI/LLM call
      const chunks = [
        "Hello! ",
        "I received your message: ",
        `"${message}". `,
        "Processing... ",
        "Done!",
      ];

      for (const chunk of chunks) {
        controller.enqueue(
          encoder.encode(`data: ${JSON.stringify({ content: chunk })}\\n\\n`),
        );
        // Simulate delay (remove in production)
        await new Promise((r) => setTimeout(r, 100));
      }

      controller.enqueue(encoder.encode("data: [DONE]\\n\\n"));
      controller.close();
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
'''

    hook_content = f'''import {{ useState, useCallback }} from "react";

interface SSEMessage {{
  content: string;
}}

export function useSSE(endpoint: string = "{endpoint}") {{
  const [messages, setMessages] = useState<string[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = useCallback(async (message: string) => {{
    setStreaming(true);
    setError(null);
    setMessages([]);

    try {{
      const res = await fetch(endpoint, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ message }}),
      }});

      if (!res.ok) throw new Error(`HTTP ${{res.status}}`);
      const reader = res.body?.getReader();
      if (!reader) throw new Error("No reader");

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {{
        const {{ done, value }} = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, {{ stream: true }});
        const lines = buffer.split("\\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {{
          if (line.startsWith("data: ")) {{
            const data = line.slice(6).trim();
            if (data === "[DONE]") continue;
            try {{
              const parsed: SSEMessage = JSON.parse(data);
              setMessages((prev) => [...prev, parsed.content]);
            }} catch {{
              // Skip malformed lines
            }}
          }}
        }}
      }}
    }} catch (e) {{
      setError(e instanceof Error ? e.message : "Stream error");
    }} finally {{
      setStreaming(false);
    }}
  }}, [endpoint]);

  const fullText = messages.join("");

  return {{ messages, fullText, streaming, error, send }};
}}
'''
    return {
        "files": [
            {
                "path": f"app{endpoint}/route.ts",
                "content": server_content,
                "description": "SSE 스트리밍 API 라우트 (Edge Runtime)",
            },
            {
                "path": "hooks/useSSE.ts",
                "content": hook_content,
                "description": "SSE 스트리밍 클라이언트 훅",
            },
        ],
        "usage_example": (
            'const { fullText, streaming, send } = useSSE();\n'
            'await send("Hello!");'
        ),
        "dependencies_needed": [],
    }


def _react_hook(endpoint: str) -> dict[str, Any]:
    content = f'''import {{ useState, useCallback }} from "react";

export function useSSE(endpoint: string = "{endpoint}") {{
  const [fullText, setFullText] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = useCallback(async (message: string) => {{
    setStreaming(true);
    setError(null);
    setFullText("");

    try {{
      const res = await fetch(endpoint, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ message }}),
      }});

      if (!res.ok) throw new Error(`HTTP ${{res.status}}`);
      const reader = res.body?.getReader();
      if (!reader) throw new Error("No reader");

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {{
        const {{ done, value }} = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, {{ stream: true }});
        const lines = buffer.split("\\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {{
          if (line.startsWith("data: ")) {{
            const data = line.slice(6).trim();
            if (data === "[DONE]") continue;
            try {{
              const parsed = JSON.parse(data);
              setFullText((prev) => prev + (parsed.content ?? ""));
            }} catch {{
              // skip
            }}
          }}
        }}
      }}
    }} catch (e) {{
      setError(e instanceof Error ? e.message : "Stream error");
    }} finally {{
      setStreaming(false);
    }}
  }}, [endpoint]);

  return {{ fullText, streaming, error, send }};
}}
'''
    return {
        "files": [
            {
                "path": "hooks/useSSE.ts",
                "content": content,
                "description": "SSE 스트리밍 클라이언트 훅",
            },
        ],
        "usage_example": (
            'const { fullText, streaming, send } = useSSE();\n'
            'await send("Hello!");'
        ),
        "dependencies_needed": [],
    }


def _python_sse(endpoint: str) -> dict[str, Any]:
    content = '''"""SSE streaming endpoint for FastAPI."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()


class ChatRequest(BaseModel):
    message: str


async def generate_stream(message: str) -> AsyncGenerator[str, None]:
    """Generate SSE events. Replace with actual AI/LLM logic."""
    chunks = [
        "Hello! ",
        f"You said: \\"{message}\\". ",
        "Processing... ",
        "Done!",
    ]
    for chunk in chunks:
        data = json.dumps({"content": chunk}, ensure_ascii=False)
        yield f"data: {data}\\n\\n"
        await asyncio.sleep(0.1)  # Remove in production
    yield "data: [DONE]\\n\\n"


@router.post("/chat")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """SSE streaming chat endpoint."""
    return StreamingResponse(
        generate_stream(req.message),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
'''
    return {
        "files": [
            {
                "path": "routes/chat.py",
                "content": content,
                "description": "FastAPI SSE 스트리밍 엔드포인트",
            },
        ],
        "usage_example": (
            'app.include_router(router, prefix="/api")\n'
            "# POST /api/chat -> SSE stream"
        ),
        "dependencies_needed": ["fastapi", "uvicorn"],
    }

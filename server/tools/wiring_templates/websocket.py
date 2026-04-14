"""WebSocket wiring template.

Generates WebSocket client hooks and server setup code.
"""

from __future__ import annotations

from typing import Any


def generate(stack: dict[str, str | None], config: dict[str, Any]) -> dict[str, Any]:
    """Generate WebSocket wiring code based on detected stack."""
    framework = stack.get("framework")
    url = config.get("url", "ws://localhost:3001")
    reconnect = config.get("reconnect", True)

    if framework in ("nextjs", "react"):
        return _react_hook(url, reconnect)
    if framework == "express":
        return _node_server(url)
    # Default: React hook
    return _react_hook(url, reconnect)


def _react_hook(url: str, reconnect: bool) -> dict[str, Any]:
    content = f'''import {{ useState, useEffect, useCallback, useRef }} from "react";

type MessageHandler = (data: unknown) => void;

interface UseWebSocketOptions {{
  url?: string;
  reconnect?: boolean;
  reconnectInterval?: number;
  maxRetries?: number;
  onMessage?: MessageHandler;
}}

export function useWebSocket(options: UseWebSocketOptions = {{}}) {{
  const {{
    url = "{url}",
    reconnect = {str(reconnect).lower()},
    reconnectInterval = 3000,
    maxRetries = 5,
    onMessage,
  }} = options;

  const [connected, setConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<unknown>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const retriesRef = useRef(0);

  const connect = useCallback(() => {{
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {{
      setConnected(true);
      retriesRef.current = 0;
    }};

    ws.onmessage = (event) => {{
      try {{
        const data = JSON.parse(event.data);
        setLastMessage(data);
        onMessage?.(data);
      }} catch {{
        setLastMessage(event.data);
        onMessage?.(event.data);
      }}
    }};

    ws.onclose = () => {{
      setConnected(false);
      if (reconnect && retriesRef.current < maxRetries) {{
        retriesRef.current += 1;
        setTimeout(connect, reconnectInterval);
      }}
    }};

    ws.onerror = () => {{
      ws.close();
    }};
  }}, [url, reconnect, reconnectInterval, maxRetries, onMessage]);

  useEffect(() => {{
    connect();
    return () => {{
      wsRef.current?.close();
    }};
  }}, [connect]);

  const send = useCallback((data: unknown) => {{
    if (wsRef.current?.readyState === WebSocket.OPEN) {{
      wsRef.current.send(typeof data === "string" ? data : JSON.stringify(data));
    }}
  }}, []);

  const disconnect = useCallback(() => {{
    wsRef.current?.close();
  }}, []);

  return {{ connected, lastMessage, send, disconnect }};
}}
'''
    return {
        "files": [
            {
                "path": "hooks/useWebSocket.ts",
                "content": content,
                "description": "WebSocket 연결 커스텀 훅 (자동 재연결)",
            },
        ],
        "usage_example": (
            "const { connected, lastMessage, send } = useWebSocket({\n"
            f'  url: "{url}",\n'
            '  onMessage: (data) => console.log(data),\n'
            "});"
        ),
        "dependencies_needed": [],
    }


def _node_server(url: str) -> dict[str, Any]:
    content = '''import { WebSocketServer, WebSocket } from "ws";
import type { Server } from "http";

interface Client {
  ws: WebSocket;
  id: string;
}

export function setupWebSocket(server: Server) {
  const wss = new WebSocketServer({ server });
  const clients = new Map<string, Client>();

  wss.on("connection", (ws) => {
    const id = crypto.randomUUID();
    clients.set(id, { ws, id });
    console.log(`Client connected: ${id}`);

    ws.on("message", (raw) => {
      try {
        const message = JSON.parse(raw.toString());
        handleMessage(id, message, clients);
      } catch {
        ws.send(JSON.stringify({ error: "Invalid JSON" }));
      }
    });

    ws.on("close", () => {
      clients.delete(id);
      console.log(`Client disconnected: ${id}`);
    });

    // Send welcome message
    ws.send(JSON.stringify({ type: "connected", clientId: id }));
  });

  return wss;
}

function handleMessage(
  senderId: string,
  message: Record<string, unknown>,
  clients: Map<string, Client>,
) {
  // Broadcast to all other clients
  for (const [id, client] of clients) {
    if (id !== senderId && client.ws.readyState === WebSocket.OPEN) {
      client.ws.send(JSON.stringify({ ...message, from: senderId }));
    }
  }
}

// Utility: send to specific client
export function sendToClient(
  clients: Map<string, Client>,
  clientId: string,
  data: unknown,
) {
  const client = clients.get(clientId);
  if (client && client.ws.readyState === WebSocket.OPEN) {
    client.ws.send(typeof data === "string" ? data : JSON.stringify(data));
  }
}
'''
    return {
        "files": [
            {
                "path": "lib/websocket.ts",
                "content": content,
                "description": "WebSocket 서버 설정 (ws 기반)",
            },
        ],
        "usage_example": (
            'import { setupWebSocket } from "./lib/websocket";\n'
            "const wss = setupWebSocket(httpServer);"
        ),
        "dependencies_needed": ["ws", "@types/ws"],
    }

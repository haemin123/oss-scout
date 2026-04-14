"""Middleware wiring template.

Generates middleware code for Express, Fastify, and Next.js.
"""

from __future__ import annotations

from typing import Any


def generate(stack: dict[str, str | None], config: dict[str, Any]) -> dict[str, Any]:
    """Generate middleware wiring code based on detected stack."""
    framework = stack.get("framework")
    features = config.get("features", ["cors", "rate-limit", "logging"])

    if framework == "nextjs":
        return _nextjs_middleware(features)
    if framework == "express":
        return _express_middleware(features)
    if framework == "fastify":
        return _fastify_middleware(features)
    if stack.get("language") == "python":
        return _python_middleware(features)
    # Default: Express (most common)
    return _express_middleware(features)


def _nextjs_middleware(features: list[str]) -> dict[str, Any]:
    parts: list[str] = []
    parts.append('import { NextResponse } from "next/server";')
    parts.append('import type { NextRequest } from "next/server";')
    parts.append("")
    parts.append("export function middleware(request: NextRequest) {")

    if "rate-limit" in features:
        parts.append("  // Rate limiting (simple in-memory, use Redis for production)")
        parts.append("  const ip = request.headers.get('x-forwarded-for') ?? 'unknown';")
        parts.append("  // TODO: implement rate limit check with external store")
        parts.append("")

    if "logging" in features:
        parts.append(
            '  console.log(`${request.method} ${request.nextUrl.pathname}`);'
        )
        parts.append("")

    parts.append("  const response = NextResponse.next();")
    parts.append("")

    if "cors" in features:
        parts.append("  // CORS headers")
        parts.append(
            '  response.headers.set("Access-Control-Allow-Origin", '
            'process.env.ALLOWED_ORIGIN ?? "*");'
        )
        parts.append(
            '  response.headers.set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE");'
        )
        parts.append(
            '  response.headers.set("Access-Control-Allow-Headers", "Content-Type, Authorization");'
        )
        parts.append("")

    parts.append("  // Security headers")
    parts.append('  response.headers.set("X-Content-Type-Options", "nosniff");')
    parts.append('  response.headers.set("X-Frame-Options", "DENY");')
    parts.append("")
    parts.append("  return response;")
    parts.append("}")
    parts.append("")
    parts.append("export const config = {")
    parts.append('  matcher: ["/api/:path*", "/((?!_next/static|favicon.ico).*)"],')
    parts.append("};")
    parts.append("")

    return {
        "files": [
            {
                "path": "middleware.ts",
                "content": "\n".join(parts),
                "description": "Next.js 미들웨어 (CORS, 보안 헤더, 로깅)",
            },
        ],
        "usage_example": "// middleware.ts는 자동으로 모든 요청에 적용됩니다.",
        "dependencies_needed": [],
    }


def _express_middleware(features: list[str]) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    deps: list[str] = []

    setup_lines = [
        'import express from "express";',
    ]

    if "cors" in features:
        setup_lines.append('import cors from "cors";')
        deps.extend(["cors", "@types/cors"])

    if "rate-limit" in features:
        setup_lines.append('import rateLimit from "express-rate-limit";')
        deps.append("express-rate-limit")

    if "logging" in features:
        setup_lines.append('import morgan from "morgan";')
        deps.extend(["morgan", "@types/morgan"])

    setup_lines.append("")
    setup_lines.append("export function applyMiddleware(app: express.Application) {")

    if "cors" in features:
        setup_lines.append("  app.use(cors({")
        setup_lines.append('    origin: process.env.ALLOWED_ORIGIN ?? "*",')
        setup_lines.append('    methods: ["GET", "POST", "PUT", "DELETE"],')
        setup_lines.append("  }));")
        setup_lines.append("")

    if "rate-limit" in features:
        setup_lines.append("  app.use(rateLimit({")
        setup_lines.append("    windowMs: 15 * 60 * 1000, // 15 minutes")
        setup_lines.append("    max: 100, // limit per IP")
        setup_lines.append('    standardHeaders: true,')
        setup_lines.append("  }));")
        setup_lines.append("")

    if "logging" in features:
        setup_lines.append('  app.use(morgan("combined"));')
        setup_lines.append("")

    setup_lines.append("  app.use(express.json({ limit: '10mb' }));")
    setup_lines.append("")
    setup_lines.append("  // Security headers")
    setup_lines.append("  app.use((_req, res, next) => {")
    setup_lines.append('    res.setHeader("X-Content-Type-Options", "nosniff");')
    setup_lines.append('    res.setHeader("X-Frame-Options", "DENY");')
    setup_lines.append("    next();")
    setup_lines.append("  });")
    setup_lines.append("}")
    setup_lines.append("")

    files.append({
        "path": "middleware/setup.ts",
        "content": "\n".join(setup_lines),
        "description": "Express 미들웨어 체인 설정",
    })

    return {
        "files": files,
        "usage_example": (
            'import { applyMiddleware } from "./middleware/setup";\n'
            "applyMiddleware(app);"
        ),
        "dependencies_needed": deps,
    }


def _fastify_middleware(features: list[str]) -> dict[str, Any]:
    deps: list[str] = []
    lines = ['import Fastify from "fastify";', ""]

    if "cors" in features:
        lines.append('import cors from "@fastify/cors";')
        deps.append("@fastify/cors")

    if "rate-limit" in features:
        lines.append('import rateLimit from "@fastify/rate-limit";')
        deps.append("@fastify/rate-limit")

    lines.append("")
    lines.append("export async function buildApp() {")
    lines.append("  const app = Fastify({ logger: true });")
    lines.append("")

    if "cors" in features:
        lines.append("  await app.register(cors, {")
        lines.append('    origin: process.env.ALLOWED_ORIGIN ?? "*",')
        lines.append("  });")
        lines.append("")

    if "rate-limit" in features:
        lines.append("  await app.register(rateLimit, {")
        lines.append("    max: 100,")
        lines.append("    timeWindow: '15 minutes',")
        lines.append("  });")
        lines.append("")

    lines.append("  // Security headers")
    lines.append("  app.addHook('onSend', async (_request, reply) => {")
    lines.append("    reply.header('X-Content-Type-Options', 'nosniff');")
    lines.append("    reply.header('X-Frame-Options', 'DENY');")
    lines.append("  });")
    lines.append("")
    lines.append("  return app;")
    lines.append("}")
    lines.append("")

    return {
        "files": [
            {
                "path": "lib/app.ts",
                "content": "\n".join(lines),
                "description": "Fastify 앱 빌더 (플러그인 체인)",
            },
        ],
        "usage_example": (
            'const app = await buildApp();\n'
            'await app.listen({ port: 3000 });'
        ),
        "dependencies_needed": ["fastify", *deps],
    }


def _python_middleware(features: list[str]) -> dict[str, Any]:
    lines = [
        '"""FastAPI middleware setup."""',
        "",
        "from __future__ import annotations",
        "",
        "import logging",
        "import os",
        "import time",
        "",
        "from fastapi import FastAPI, Request",
    ]

    if "cors" in features:
        lines.append("from fastapi.middleware.cors import CORSMiddleware")

    if "rate-limit" in features:
        lines.append("from slowapi import Limiter")
        lines.append("from slowapi.util import get_remote_address")

    lines.append("")
    lines.append("logger = logging.getLogger(__name__)")
    lines.append("")
    lines.append("")
    lines.append("def apply_middleware(app: FastAPI) -> None:")
    lines.append('    """Register all middleware on the FastAPI app."""')
    lines.append("")

    if "cors" in features:
        lines.append("    app.add_middleware(")
        lines.append("        CORSMiddleware,")
        lines.append('        allow_origins=[os.getenv("ALLOWED_ORIGIN", "*")],')
        lines.append("        allow_credentials=True,")
        lines.append('        allow_methods=["*"],')
        lines.append('        allow_headers=["*"],')
        lines.append("    )")
        lines.append("")

    if "logging" in features:
        lines.append("    @app.middleware('http')")
        lines.append("    async def log_requests(request: Request, call_next):")
        lines.append("        start = time.time()")
        lines.append("        response = await call_next(request)")
        lines.append("        duration = time.time() - start")
        lines.append(
            '        logger.info(f"{request.method} {request.url.path} '
            '{response.status_code} {duration:.3f}s")'
        )
        lines.append("        return response")
        lines.append("")

    if "rate-limit" in features:
        lines.append("    # Rate limiting")
        lines.append("    limiter = Limiter(key_func=get_remote_address)")
        lines.append("    app.state.limiter = limiter")
        lines.append("")

    lines.append("")

    deps = ["fastapi"]
    if "rate-limit" in features:
        deps.append("slowapi")

    return {
        "files": [
            {
                "path": "middleware/setup.py",
                "content": "\n".join(lines),
                "description": "FastAPI 미들웨어 설정",
            },
        ],
        "usage_example": (
            "from middleware.setup import apply_middleware\n"
            "apply_middleware(app)"
        ),
        "dependencies_needed": deps,
    }

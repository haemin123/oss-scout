"""Auth guard wiring template.

Generates authentication guard code for various frameworks.
"""

from __future__ import annotations

from typing import Any


def generate(stack: dict[str, str | None], config: dict[str, Any]) -> dict[str, Any]:
    """Generate auth guard wiring code based on detected stack."""
    framework = stack.get("framework")
    auth = stack.get("auth")
    protected_paths = config.get("protected_paths", ["/dashboard", "/api/private"])

    if framework == "nextjs" and auth == "next-auth":
        return _nextauth_middleware(protected_paths)
    if framework == "nextjs" and auth == "firebase":
        return _firebase_nextjs(protected_paths)
    if framework in ("react", "nextjs"):
        return _react_protected_route(protected_paths)
    if framework == "express":
        return _express_middleware()
    if stack.get("language") == "python":
        return _python_middleware()
    # Default: generic React protected route
    return _react_protected_route(protected_paths)


def _nextauth_middleware(protected_paths: list[str]) -> dict[str, Any]:
    paths_str = ", ".join(f'"{p}"' for p in protected_paths)
    content = f'''import {{ NextResponse }} from "next/server";
import {{ getToken }} from "next-auth/jwt";
import type {{ NextRequest }} from "next/server";

const protectedPaths = [{paths_str}];

export async function middleware(request: NextRequest) {{
  const token = await getToken({{ req: request }});
  const isProtected = protectedPaths.some((path) =>
    request.nextUrl.pathname.startsWith(path)
  );

  if (isProtected && !token) {{
    const loginUrl = new URL("/api/auth/signin", request.url);
    loginUrl.searchParams.set("callbackUrl", request.nextUrl.pathname);
    return NextResponse.redirect(loginUrl);
  }}

  return NextResponse.next();
}}

export const config = {{
  matcher: [{paths_str}, "/api/private/:path*"],
}};
'''
    return {
        "files": [
            {
                "path": "middleware.ts",
                "content": content,
                "description": "NextAuth 기반 인증 미들웨어",
            },
        ],
        "usage_example": "// middleware.ts가 자동으로 보호 경로를 처리합니다.",
        "dependencies_needed": ["next-auth"],
    }


def _firebase_nextjs(protected_paths: list[str]) -> dict[str, Any]:
    hook_content = '''import { useEffect, useState } from "react";
import { onAuthStateChanged, User } from "firebase/auth";
import { auth } from "@/lib/firebase";

export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (u) => {
      setUser(u);
      setLoading(false);
    });
    return unsubscribe;
  }, []);

  return { user, loading };
}
'''

    component_content = '''"use client";

import { useAuth } from "@/hooks/useAuth";
import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

interface ProtectedRouteProps {
  children: ReactNode;
  fallback?: ReactNode;
}

export function ProtectedRoute({ children, fallback }: ProtectedRouteProps) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) {
      router.push("/login");
    }
  }, [user, loading, router]);

  if (loading) return fallback ?? <div>Loading...</div>;
  if (!user) return null;

  return <>{children}</>;
}
'''
    return {
        "files": [
            {
                "path": "hooks/useAuth.ts",
                "content": hook_content,
                "description": "Firebase Auth 상태 관리 훅",
            },
            {
                "path": "components/ProtectedRoute.tsx",
                "content": component_content,
                "description": "인증 필요 라우트 래퍼 컴포넌트",
            },
        ],
        "usage_example": (
            "<ProtectedRoute><DashboardPage /></ProtectedRoute>"
        ),
        "dependencies_needed": ["firebase"],
    }


def _react_protected_route(protected_paths: list[str]) -> dict[str, Any]:
    content = '''"use client";

import { useEffect, type ReactNode } from "react";

interface ProtectedRouteProps {
  children: ReactNode;
  isAuthenticated: boolean;
  onUnauthenticated?: () => void;
  fallback?: ReactNode;
}

export function ProtectedRoute({
  children,
  isAuthenticated,
  onUnauthenticated,
  fallback,
}: ProtectedRouteProps) {
  useEffect(() => {
    if (!isAuthenticated && onUnauthenticated) {
      onUnauthenticated();
    }
  }, [isAuthenticated, onUnauthenticated]);

  if (!isAuthenticated) return fallback ?? null;

  return <>{children}</>;
}
'''
    return {
        "files": [
            {
                "path": "components/ProtectedRoute.tsx",
                "content": content,
                "description": "범용 인증 가드 컴포넌트",
            },
        ],
        "usage_example": (
            "<ProtectedRoute isAuthenticated={!!user}"
            ' onUnauthenticated={() => router.push("/login")}>'
            "\n  <DashboardPage />\n</ProtectedRoute>"
        ),
        "dependencies_needed": [],
    }


def _express_middleware() -> dict[str, Any]:
    content = '''import { Request, Response, NextFunction } from "express";
import jwt from "jsonwebtoken";

const JWT_SECRET = process.env.JWT_SECRET ?? "";

export interface AuthRequest extends Request {
  userId?: string;
}

export function authMiddleware(
  req: AuthRequest,
  res: Response,
  next: NextFunction,
): void {
  const authHeader = req.headers.authorization;
  if (!authHeader?.startsWith("Bearer ")) {
    res.status(401).json({ error: "Authentication required" });
    return;
  }

  const token = authHeader.slice(7);
  try {
    const payload = jwt.verify(token, JWT_SECRET) as { sub: string };
    req.userId = payload.sub;
    next();
  } catch {
    res.status(401).json({ error: "Invalid or expired token" });
  }
}
'''
    return {
        "files": [
            {
                "path": "middleware/auth.ts",
                "content": content,
                "description": "Express JWT 인증 미들웨어",
            },
        ],
        "usage_example": 'app.use("/api/private", authMiddleware);',
        "dependencies_needed": ["jsonwebtoken", "@types/jsonwebtoken"],
    }


def _python_middleware() -> dict[str, Any]:
    content = '''"""JWT authentication middleware for FastAPI."""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

JWT_SECRET = os.getenv("JWT_SECRET", "")
ALGORITHM = "HS256"

_bearer = HTTPBearer()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> dict:
    """Validate JWT and return user payload."""
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[ALGORITHM])
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing sub claim",
            )
        return {"user_id": user_id, **payload}
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc
'''
    return {
        "files": [
            {
                "path": "middleware/auth.py",
                "content": content,
                "description": "FastAPI JWT 인증 미들웨어",
            },
        ],
        "usage_example": (
            '@app.get("/api/private")\n'
            "async def private_route(user=Depends(get_current_user)):"
        ),
        "dependencies_needed": ["python-jose[cryptography]"],
    }

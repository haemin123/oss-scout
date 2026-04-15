"""SQLite-based local cache with TTL support.

Replaces Firestore for lightweight, zero-dependency caching.
All operations are async via aiosqlite.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger("oss-scout")


def _log(level: str, event: str, **kwargs: Any) -> None:
    entry = {"level": level, "event": event, **kwargs}
    getattr(logger, level.lower(), logger.info)(
        json.dumps(entry, ensure_ascii=False)
    )


class LocalCache:
    """SQLite-backed cache with TTL expiration."""

    def __init__(
        self,
        db_path: str | None = None,
        default_ttl_hours: int = 24,
    ) -> None:
        cache_dir = os.getenv("CACHE_DIR", ".cache")
        self._db_path = db_path or os.path.join(cache_dir, "oss_scout.db")
        self._default_ttl_hours = int(
            os.getenv("CACHE_TTL_HOURS", str(default_ttl_hours))
        )
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        """Create database and tables if they don't exist."""
        if self._initialized:
            return

        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_expires
                ON cache(expires_at)
            """)
            await db.commit()

        self._initialized = True
        _log("info", "cache_initialized", db_path=self._db_path)

    async def get(self, key: str) -> dict[str, Any] | None:
        """Get a cached value by key. Returns None if missing or expired."""
        await self._ensure_initialized()
        now = datetime.now(UTC).isoformat()

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT data, expires_at FROM cache WHERE key = ?",
                (key,),
            ) as cursor:
                row = await cursor.fetchone()

            if row is None:
                return None

            if row["expires_at"] < now:
                await db.execute("DELETE FROM cache WHERE key = ?", (key,))
                await db.commit()
                _log("debug", "cache_expired", key=key)
                return None

            _log("debug", "cache_hit", key=key)
            result: dict[str, Any] = json.loads(row["data"])
            return result

    async def get_stale(self, key: str) -> dict[str, Any] | None:
        """Get a cached value even if expired (for rate-limit fallback)."""
        await self._ensure_initialized()

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT data FROM cache WHERE key = ?",
                (key,),
            ) as cursor:
                row = await cursor.fetchone()

            if row is None:
                return None

            _log("debug", "cache_stale_hit", key=key)
            stale_result: dict[str, Any] = json.loads(row["data"])
            return stale_result

    async def set(
        self,
        key: str,
        data: dict[str, Any],
        ttl_hours: int | None = None,
    ) -> None:
        """Store a value with TTL."""
        await self._ensure_initialized()
        ttl = ttl_hours if ttl_hours is not None else self._default_ttl_hours
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=ttl)

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO cache (key, data, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    key,
                    json.dumps(data, ensure_ascii=False, default=str),
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            await db.commit()

        _log("debug", "cache_set", key=key, ttl_hours=ttl)

    async def delete(self, key: str) -> None:
        """Delete a cached entry by key."""
        await self._ensure_initialized()

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM cache WHERE key = ?", (key,))
            await db.commit()

    async def cleanup_expired(self) -> int:
        """Delete all expired entries. Returns count of deleted rows."""
        await self._ensure_initialized()
        now = datetime.now(UTC).isoformat()

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "DELETE FROM cache WHERE expires_at < ?", (now,),
            )
            await db.commit()
            deleted = cursor.rowcount

        if deleted > 0:
            _log("info", "cache_cleanup", deleted=deleted)
        return deleted

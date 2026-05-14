"""
agentns.cache
=============
TTL-based in-memory resolution cache.

Key   = MD5(agent_name + sorted(protocols) + location_str)
Value = (resolved_payload, expiry_timestamp)

Thread-safe (asyncio.Lock).  No external dependencies.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any, Dict, Optional, Tuple


class ResolutionCache:
    def __init__(self) -> None:
        self._store: Dict[str, Tuple[Any, float]] = {}
        self._lock  = asyncio.Lock()
        self._hits  = 0
        self._misses = 0

    # ── public interface ────────────────────────────────────────────────────────

    def make_key(self, agent_name: str, requester_context: Dict) -> str:
        protocols = sorted(requester_context.get("protocols") or [])
        loc       = requester_context.get("location") or {}
        loc_str   = json.dumps(loc, sort_keys=True)
        raw       = f"{agent_name}|{protocols}|{loc_str}"
        return hashlib.md5(raw.encode()).hexdigest()

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            payload, expiry = entry
            if time.monotonic() > expiry:
                del self._store[key]
                self._misses += 1
                return None
            self._hits += 1
            return payload

    async def set(self, key: str, payload: Any, ttl: int) -> None:
        async with self._lock:
            self._store[key] = (payload, time.monotonic() + ttl)

    async def invalidate(self, agent_name: str) -> int:
        """Remove all entries whose key was derived from *agent_name*."""
        # Since keys are MD5 hashes we can't invert them; store raw agent_name alongside
        async with self._lock:
            to_delete = [k for k, (v, _) in self._store.items()
                         if isinstance(v, dict) and v.get("_cache_key_agent") == agent_name]
            for k in to_delete:
                del self._store[k]
            return len(to_delete)

    async def clear(self) -> int:
        async with self._lock:
            count = len(self._store)
            self._store.clear()
            self._hits  = 0
            self._misses = 0
            return count

    async def stats(self) -> Dict:
        async with self._lock:
            now = time.monotonic()
            active  = sum(1 for _, (_, exp) in self._store.items() if now < exp)
            expired = len(self._store) - active
            total   = self._hits + self._misses
            hit_rate = (self._hits / total * 100) if total else 0.0
            return {
                "total_entries":  len(self._store),
                "active_entries": active,
                "expired_entries": expired,
                "hits":           self._hits,
                "misses":         self._misses,
                "hit_rate_pct":   round(hit_rate, 1),
            }

    async def purge_expired(self) -> int:
        """Remove expired entries; called periodically by the server."""
        async with self._lock:
            now     = time.monotonic()
            expired = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]
            return len(expired)

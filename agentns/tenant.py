"""
agentns.tenant
==============
Namespace ownership and API key management for DANS.

Each developer signs up once, claims a namespace, gets an API key.
The key is scoped to that namespace — they can only register agents under it.

Key format:  dk_live_{48 hex chars}   (dk = dans key)
Key storage: SHA-256 hash in MongoDB tenants collection (never store raw)
Cache:       5-min TTL on key→tenant lookups to avoid per-request DB hits
"""

import hashlib, os, secrets, time, uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple

_CACHE_TTL = 300  # 5 minutes

# key_hash → (tenant_dict, expiry_ts)
_key_cache: Dict[str, Tuple[dict, float]] = {}
# namespace → (tenant_dict, expiry_ts)  — for availability checks
_ns_cache:  Dict[str, Tuple[Optional[dict], float]] = {}


def generate_api_key() -> Tuple[str, str]:
    """Return (raw_key, sha256_hash). Raw key must be shown to user exactly once."""
    raw    = "dk_live_" + secrets.token_hex(24)   # 56 chars total
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, hashed


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def create_tenant(col, email: str, namespace: str) -> dict:
    """
    Create a new tenant document.  Returns full dict including raw api_key (show once).
    Raises ValueError if namespace is already taken.
    """
    if col is None:
        raise RuntimeError("MongoDB required for signup (MONGODB_URI not set)")

    existing = await col.find_one({"namespace": namespace, "active": True})
    if existing:
        raise ValueError(f"Namespace '{namespace}' is already claimed")

    raw_key, key_hash = generate_api_key()
    tenant_id = str(uuid.uuid4())

    doc = {
        "tenant_id":      tenant_id,
        "api_key_hash":   key_hash,
        "api_key_prefix": raw_key[:16],   # dk_live_xxxxxxxx — for display
        "email":          email,
        "namespace":      namespace,
        "created_at":     datetime.now(timezone.utc),
        "active":         True,
    }
    await col.insert_one(doc)

    # Cache immediately so next request is instant
    tenant_public = {k: v for k, v in doc.items() if k not in ("_id", "api_key_hash")}
    _key_cache[key_hash]    = (tenant_public, time.monotonic() + _CACHE_TTL)
    _ns_cache[namespace]    = (tenant_public, time.monotonic() + _CACHE_TTL)

    return {**tenant_public, "api_key": raw_key}   # include raw key once


async def get_tenant_by_key(col, raw_key: str) -> Optional[dict]:
    """Validate raw API key → return tenant dict or None."""
    if not raw_key:
        return None

    key_hash = _hash_key(raw_key)

    # Cache hit
    cached = _key_cache.get(key_hash)
    if cached and cached[1] > time.monotonic():
        return cached[0]

    if col is None:
        return None

    doc = await col.find_one({"api_key_hash": key_hash, "active": True})
    if not doc:
        return None

    tenant = {k: v for k, v in doc.items() if k not in ("_id", "api_key_hash")}
    _key_cache[key_hash] = (tenant, time.monotonic() + _CACHE_TTL)
    return tenant


async def namespace_available(col, namespace: str) -> bool:
    """True if namespace is unclaimed."""
    cached = _ns_cache.get(namespace)
    if cached and cached[1] > time.monotonic():
        return cached[0] is None

    if col is None:
        return True

    doc = await col.find_one({"namespace": namespace, "active": True})
    result = doc is None
    _ns_cache[namespace] = (None if result else {}, time.monotonic() + _CACHE_TTL)
    return result


async def init_tenant_collection(db):
    """Create indices on the tenants collection."""
    col = db["tenants"]
    await col.create_index("namespace", unique=True)
    await col.create_index("api_key_hash", unique=True)
    await col.create_index("email")
    return col

"""
DataWorksAI Agent Registry v5.0
================================
Hosted agent discovery service — semantic search, multi-tenancy, switchboard federation.

Single-tenant mode (default):
    Start with no env vars → behaves identically to v4.0 (zero breaking changes).

SaaS multi-tenant mode:
    SAAS_MODE=1 → every request requires X-API-Key header; agents are isolated by tenant.

Switchboard federation:
    ENABLE_FEDERATION=true → registers /switchboard/* routes so external registries
    can be linked and cross-registry lookups happen transparently.

Environment variables
---------------------
SAAS_MODE          "1" → enable multi-tenant SaaS mode (default: off)
ENABLE_FEDERATION  "true" → mount switchboard routes (default: false)
MONGODB_URI        MongoDB connection string (required in production)
MONGODB_DB         Database name (default: agent_registry)
TEST_MODE          "1" → skip MongoDB entirely (for unit tests)
PORT               HTTP port (default: 6900)
ANS_APP            App namespace for ANS resolve endpoint (default: default)
AUTH_NS_URL        Upstream Auth NS URL for /resolve delegation
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, g, jsonify, request
from flask_cors import CORS

TEST_MODE = os.getenv("TEST_MODE") == "1"
SAAS_MODE = os.getenv("SAAS_MODE") == "1"
ENABLE_FEDERATION = os.getenv("ENABLE_FEDERATION", "").lower() == "true"

import re

if not TEST_MODE:
    try:
        from pymongo import MongoClient, ASCENDING
        _PYMONGO_OK = True
    except ImportError:
        _PYMONGO_OK = False
else:
    _PYMONGO_OK = False

logger = logging.getLogger("registry")

app = Flask(__name__)
CORS(app)

DEFAULT_PORT = 6900

MONGO_URI    = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI") or "mongodb://localhost:27017/"
MONGO_DBNAME = os.getenv("MONGODB_DB", "agent_registry")

# ── MongoDB connection ─────────────────────────────────────────────────────────

USE_MONGO = False
mongo_db = None
agent_registry_col = None
client_registry_col = None
tenants_col = None

if not TEST_MODE and _PYMONGO_OK:
    try:
        _mc = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        _mc.admin.command("ping")
        mongo_db = _mc[MONGO_DBNAME]
        agent_registry_col = mongo_db["agents"]
        client_registry_col = mongo_db["client_registry"]
        tenants_col = mongo_db["tenants"]
        USE_MONGO = True

        # Ensure indexes
        agent_registry_col.create_index(
            [("tenant_id", ASCENDING), ("agent_id", ASCENDING)], unique=True
        )
        tenants_col.create_index("api_key_prefix", unique=True, sparse=True)
        tenants_col.create_index("email", unique=True, sparse=True)
        print("✅ MongoDB connected")
    except Exception as _e:
        print(f"⚠️  MongoDB unavailable: {_e}")


# ── In-memory registry ─────────────────────────────────────────────────────────
# Single-tenant:  registry[agent_id]                         = url
# SaaS:           _tenant_registry[tenant_id][agent_id]      = url
# Both modes always keep an "agent_status" sub-dict.

# Single-tenant stores
registry: Dict[str, Any] = {"agent_status": {}}
client_registry: Dict[str, Any] = {"agent_map": {}}

# SaaS stores
_tenant_registry: Dict[str, Dict[str, Any]] = {}      # tenant_id → {agent_id: url, "agent_status": {}}
_tenant_client_reg: Dict[str, Dict[str, Any]] = {}    # tenant_id → {client_name: url, "agent_map": {}}

# Tenant auth cache: api_key → (tenant_doc, expiry_datetime)
_tenant_cache: Dict[str, Tuple[Dict, datetime]] = {}
_TENANT_CACHE_TTL = timedelta(minutes=5)


# ── Load single-tenant data from MongoDB ──────────────────────────────────────

def _load_single_tenant():
    if TEST_MODE or not USE_MONGO or agent_registry_col is None:
        return
    try:
        for doc in agent_registry_col.find({"tenant_id": {"$exists": False}}):
            aid = doc.get("agent_id")
            if not aid:
                continue
            registry[aid] = doc.get("agent_url")
            registry["agent_status"][aid] = {
                "alive":       doc.get("alive", False),
                "assigned_to": doc.get("assigned_to"),
                "last_update": doc.get("last_update"),
                "api_url":     doc.get("api_url"),
                "description": doc.get("description", ""),
                "capabilities":doc.get("capabilities", []),
                "tags":        doc.get("tags", []),
                "agent_name":  doc.get("agent_name", ""),
            }
        print(f"📚 Loaded {len(registry) - 1} agents")
    except Exception as e:
        print(f"⚠️  Error loading agents: {e}")


if not SAAS_MODE:
    _load_single_tenant()


# ── Tenant helpers (SaaS mode) ─────────────────────────────────────────────────

def _hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def _resolve_tenant(api_key: str) -> Optional[Dict]:
    """Return tenant doc for api_key, using cache. Returns None if invalid."""
    now = datetime.utcnow()
    cached = _tenant_cache.get(api_key)
    if cached:
        tenant_doc, expiry = cached
        if now < expiry:
            return tenant_doc
        del _tenant_cache[api_key]

    if not USE_MONGO or tenants_col is None:
        return None

    key_hash = _hash_key(api_key)
    doc = tenants_col.find_one({"api_key_hash": key_hash, "active": True})
    if doc:
        _tenant_cache[api_key] = (doc, now + _TENANT_CACHE_TTL)
    return doc


def _tenant_reg(tenant_id: str) -> Dict[str, Any]:
    """Return (or create) the in-memory registry dict for a tenant."""
    if tenant_id not in _tenant_registry:
        _tenant_registry[tenant_id] = {"agent_status": {}}
    return _tenant_registry[tenant_id]


def _tenant_cli(tenant_id: str) -> Dict[str, Any]:
    if tenant_id not in _tenant_client_reg:
        _tenant_client_reg[tenant_id] = {"agent_map": {}}
    return _tenant_client_reg[tenant_id]


def _get_registry(tenant_id: Optional[str] = None) -> Dict[str, Any]:
    if SAAS_MODE and tenant_id:
        return _tenant_reg(tenant_id)
    return registry


def _get_client_registry(tenant_id: Optional[str] = None) -> Dict[str, Any]:
    if SAAS_MODE and tenant_id:
        return _tenant_cli(tenant_id)
    return client_registry


# ── Auth middleware ────────────────────────────────────────────────────────────

PUBLIC_PATHS = {"/health", "/stats"}   # no auth required


@app.before_request
def authenticate():
    if not SAAS_MODE:
        g.tenant_id = None
        return

    if request.path in PUBLIC_PATHS or request.path.startswith("/static"):
        g.tenant_id = None
        return

    api_key = request.headers.get("X-API-Key", "").strip()
    if not api_key:
        return jsonify({"error": "X-API-Key header required"}), 401

    tenant = _resolve_tenant(api_key)
    if not tenant:
        return jsonify({"error": "Invalid or inactive API key"}), 401

    g.tenant_id = str(tenant["tenant_id"])


# ── Persistence helpers ────────────────────────────────────────────────────────

def _get_tenant_id() -> Optional[str]:
    return getattr(g, "tenant_id", None)


def save_registry():
    if TEST_MODE or not USE_MONGO or agent_registry_col is None:
        return
    tid = _get_tenant_id()
    reg = _get_registry(tid)
    try:
        for agent_id, agent_url in reg.items():
            if agent_id == "agent_status":
                continue
            status = reg.get("agent_status", {}).get(agent_id, {})
            doc = {"agent_id": agent_id, "agent_url": agent_url, **status}
            if SAAS_MODE and tid:
                doc["tenant_id"] = tid
            agent_registry_col.update_one(
                {"agent_id": agent_id, "tenant_id": tid} if SAAS_MODE else {"agent_id": agent_id},
                {"$set": doc},
                upsert=True,
            )
    except Exception as e:
        logger.warning(f"save_registry: {e}")


def save_client_registry():
    if TEST_MODE or not USE_MONGO or client_registry_col is None:
        return
    tid = _get_tenant_id()
    creg = _get_client_registry(tid)
    try:
        for client_name, api_url in creg.items():
            if client_name == "agent_map":
                continue
            doc = {"client_name": client_name, "api_url": api_url,
                   "agent_id": creg.get("agent_map", {}).get(client_name)}
            if SAAS_MODE and tid:
                doc["tenant_id"] = tid
            client_registry_col.update_one(
                {"client_name": client_name, "tenant_id": tid} if SAAS_MODE else {"client_name": client_name},
                {"$set": doc},
                upsert=True,
            )
    except Exception as e:
        logger.warning(f"save_client_registry: {e}")


# ── Semantic search ────────────────────────────────────────────────────────────

def normalize_text(text: str) -> str:
    return re.sub(r"[^a-z0-9\s]", " ", text.lower()).strip()


def extract_keywords(query: str) -> List[str]:
    text = normalize_text(query)
    stopwords = {
        "the","a","an","and","or","but","in","on","at","to","for","of","with",
        "by","from","as","is","are","was","were","be","have","has","had","do",
        "does","did","will","would","could","should","may","might","can","i",
        "you","he","she","it","we","they","this","that","these","those","what",
        "which","who","when","where","why","how","my","your","get","find","need",
        "want","tell","me","about",
    }
    return [w for w in text.split() if w not in stopwords and len(w) > 2]


def calculate_relevance_score(
    query: str,
    agent_id: str,
    description: str,
    capabilities: List[str],
    tags: List[str],
) -> Tuple[float, str]:
    keywords = extract_keywords(query)
    if not keywords:
        return 0.0, "no_keywords"

    score = 0.0
    details: List[str] = []

    id_norm = normalize_text(agent_id)
    for kw in keywords:
        if kw in id_norm:
            score += 2.0
            details.append(f"id:{kw}")

    if description:
        desc_norm = normalize_text(description)
        for kw in keywords:
            if kw in desc_norm:
                score += 1.5
                details.append(f"desc:{kw}")

    for cap in (capabilities or []):
        cap_norm = normalize_text(cap)
        for kw in keywords:
            if kw in cap_norm:
                score += 3.0
                details.append(f"cap:{kw}")

    for tag in (tags or []):
        tag_norm = normalize_text(tag)
        for kw in keywords:
            if kw in tag_norm:
                score += 1.0
                details.append(f"tag:{kw}")

    q_norm = normalize_text(query)
    if description and q_norm in normalize_text(description):
        score += 5.0
        details.append("exact_phrase")

    return score, (",".join(details) if details else "no_match")


def _build_agent_payload(agent_id: str, reg: Optional[Dict] = None) -> Dict[str, Any]:
    if reg is None:
        reg = _get_registry(_get_tenant_id())
    status = reg.get("agent_status", {}).get(agent_id, {})
    return {
        "agent_id":    agent_id,
        "agent_url":   reg.get(agent_id),
        "api_url":     status.get("api_url"),
        "alive":       status.get("alive", False),
        "assigned_to": status.get("assigned_to"),
        "last_update": status.get("last_update"),
        "capabilities":status.get("capabilities", []),
        "tags":        status.get("tags", []),
        "description": status.get("description", ""),
        "agent_name":  status.get("agent_name", ""),
    }


# ══════════════════════════════════════════════════════════════════════════════
# API ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":    "ok",
        "version":   "5.0.0",
        "saas_mode": SAAS_MODE,
        "federation":ENABLE_FEDERATION,
        "mongo":     USE_MONGO and not TEST_MODE,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    })


@app.route("/stats", methods=["GET"])
def stats():
    tid = _get_tenant_id()
    reg = _get_registry(tid)
    agents = [a for a in reg.keys() if a != "agent_status"]
    alive  = sum(1 for a in agents if reg["agent_status"].get(a, {}).get("alive"))
    clients = _get_client_registry(tid)
    return jsonify({
        "version":         "5.0.0",
        "total_agents":    len(agents),
        "alive_agents":    alive,
        "total_clients":   len([c for c in clients if c != "agent_map"]),
        "mongodb_enabled": USE_MONGO and not TEST_MODE,
        "saas_mode":       SAAS_MODE,
    })


@app.route("/search/semantic", methods=["POST"])
def semantic_search():
    data        = request.json or {}
    query       = data.get("query", "").strip()
    max_results = int(data.get("max_results", 5))
    alive_only  = data.get("alive_only", True)

    if not query:
        return jsonify({"error": "query parameter required"}), 400
    if not 1 <= max_results <= 20:
        return jsonify({"error": "max_results must be between 1 and 20"}), 400

    tid = _get_tenant_id()
    reg = _get_registry(tid)
    ids = [a for a in reg if a != "agent_status"]
    if alive_only:
        ids = [a for a in ids if reg["agent_status"].get(a, {}).get("alive")]

    scored: List[Tuple[str, float, str]] = []
    for aid in ids:
        st = reg["agent_status"].get(aid, {})
        sc, reason = calculate_relevance_score(
            query, aid, st.get("description",""), st.get("capabilities",[]), st.get("tags",[])
        )
        scored.append((aid, sc, reason))

    scored.sort(key=lambda x: x[1], reverse=True)
    top = [(aid, sc, r) for aid, sc, r in scored if sc > 0][:max_results]

    results = []
    for aid, sc, r in top:
        p = _build_agent_payload(aid, reg)
        p["relevance_score"] = sc
        p["match_reason"]    = r
        results.append(p)

    return jsonify({
        "query":           query,
        "total_candidates":len(ids),
        "returned_count":  len(results),
        "results":         results,
    })


@app.route("/search", methods=["GET"])
def search_agents():
    query  = request.args.get("q", "").strip().lower()
    caps_f = request.args.get("capabilities")
    tags_f = request.args.get("tags")
    caps   = [c.strip() for c in caps_f.split(",")] if caps_f else []
    tags   = [t.strip() for t in tags_f.split(",")] if tags_f else []

    tid = _get_tenant_id()
    reg = _get_registry(tid)

    results: List[Dict] = []
    for aid in reg:
        if aid == "agent_status":
            continue
        if query and query not in aid.lower():
            continue
        p = _build_agent_payload(aid, reg)
        if caps and not any(c in (p.get("capabilities") or []) for c in caps):
            continue
        if tags and not any(t in (p.get("tags") or []) for t in tags):
            continue
        results.append(p)

    return jsonify(results)


@app.route("/agents/<agent_id>", methods=["GET"])
def get_agent(agent_id):
    tid = _get_tenant_id()
    reg = _get_registry(tid)
    if agent_id not in reg or agent_id == "agent_status":
        return jsonify({"error": "Agent not found"}), 404
    return jsonify(_build_agent_payload(agent_id, reg))


@app.route("/agents/<agent_id>", methods=["DELETE"])
def delete_agent(agent_id):
    tid = _get_tenant_id()
    reg = _get_registry(tid)
    if agent_id not in reg or agent_id == "agent_status":
        return jsonify({"error": "Agent not found"}), 404

    reg.pop(agent_id, None)
    reg.get("agent_status", {}).pop(agent_id, None)

    cli = _get_client_registry(tid)
    to_del = [cn for cn, ma in cli.get("agent_map", {}).items() if ma == agent_id]
    for cn in to_del:
        cli.pop(cn, None)
        cli.get("agent_map", {}).pop(cn, None)

    save_registry()
    save_client_registry()
    return jsonify({"status": "deleted", "agent_id": agent_id})


@app.route("/agents/<agent_id>/status", methods=["PUT"])
def update_agent_status(agent_id):
    tid = _get_tenant_id()
    reg = _get_registry(tid)
    if agent_id not in reg or agent_id == "agent_status":
        return jsonify({"error": "Agent not found"}), 404

    data   = request.json or {}
    status = reg.get("agent_status", {}).get(agent_id, {})

    for field in ("alive", "assigned_to", "capabilities", "tags", "description"):
        if field in data:
            status[field] = data[field]
    status["last_update"] = datetime.utcnow().isoformat() + "Z"
    reg["agent_status"][agent_id] = status
    save_registry()
    return jsonify({"status": "updated", "agent": _build_agent_payload(agent_id, reg)})


@app.route("/register", methods=["POST"])
def register_agent():
    data = request.json
    if not data or "agent_id" not in data or "agent_url" not in data:
        return jsonify({"error": "Missing agent_id or agent_url"}), 400

    aid  = data["agent_id"]
    aurl = data["agent_url"]

    tid = _get_tenant_id()
    reg = _get_registry(tid)

    reg[aid] = aurl
    reg.setdefault("agent_status", {})[aid] = {
        "alive":       False,
        "assigned_to": None,
        "api_url":     data.get("api_url"),
        "description": data.get("description", ""),
        "capabilities":data.get("capabilities", []),
        "tags":        data.get("tags", []),
        "agent_name":  data.get("agent_name", ""),
        "last_update": datetime.utcnow().isoformat() + "Z",
    }
    save_registry()
    print(f"✅ Registered: {aid}" + (f" [tenant:{tid}]" if tid else ""))
    return jsonify({"status": "success", "message": f"Agent {aid} registered"})


@app.route("/lookup/<id>", methods=["GET"])
def lookup(id):
    tid = _get_tenant_id()
    reg = _get_registry(tid)
    cli = _get_client_registry(tid)

    if id in reg and id != "agent_status":
        st = reg["agent_status"].get(id, {})
        return jsonify({
            "agent_id":  id,
            "agent_url": reg[id],
            "api_url":   st.get("api_url"),
            "description": st.get("description", ""),
        })

    if id in cli:
        aid  = cli["agent_map"][id]
        st   = reg["agent_status"].get(aid, {})
        return jsonify({
            "agent_id":  aid,
            "agent_url": reg.get(aid),
            "api_url":   cli[id],
            "description": st.get("description", ""),
        })

    return jsonify({"error": f"ID '{id}' not found"}), 404


@app.route("/list", methods=["GET"])
def list_agents():
    tid = _get_tenant_id()
    reg = _get_registry(tid)
    return jsonify({k: v for k, v in reg.items() if k != "agent_status"})


@app.route("/clients", methods=["GET"])
def list_clients():
    tid = _get_tenant_id()
    cli = _get_client_registry(tid)
    return jsonify({k: "alive" for k in cli if k != "agent_map"})


# ── ANS / TLD resolve ──────────────────────────────────────────────────────────

_AUTH_NS_URL = os.getenv("AUTH_NS_URL", "")
_ANS_APP     = os.getenv("ANS_APP", "default")


def _forward_to_auth_ns(label: str, requester_context: dict):
    import json as _json, urllib.request as _req, urllib.error as _err, socket as _s
    if not _AUTH_NS_URL:
        return jsonify({"error": "AUTH_NS_URL not configured"}), 503

    payload = _json.dumps({"agent": label, "requester_context": requester_context}).encode()
    req = _req.Request(
        f"{_AUTH_NS_URL}/resolve", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with _req.urlopen(req, timeout=5) as resp:
            return jsonify(_json.loads(resp.read().decode())), resp.status
    except _err.HTTPError as exc:
        body = {}
        try:
            body = _json.loads(exc.read().decode())
        except Exception:
            pass
        return jsonify(body or {"error": str(exc)}), exc.code
    except (_err.URLError, _s.timeout) as exc:
        return jsonify({"error": f"Auth NS unreachable: {exc}"}), 503


@app.route("/resolve", methods=["POST"])
def resolve_tld():
    body         = request.get_json(silent=True) or {}
    agent_path   = body.get("agent_path", "").strip()
    req_ctx      = body.get("requester_context", {})

    if not agent_path:
        return jsonify({"error": "missing agent_path"}), 400

    parts = agent_path.split(":", 1)
    if len(parts) != 2:
        return jsonify({"error": f"invalid agent_path: {agent_path!r}. Expected '<app>:<label>'"}), 400

    app_ns, label = parts
    if app_ns != _ANS_APP:
        return jsonify({"error": f"unknown app namespace: {app_ns!r}"}), 404

    return _forward_to_auth_ns(label, req_ctx)


@app.route("/resolve/<app_ns>", methods=["POST"])
def resolve_app_ns(app_ns):
    body   = request.get_json(silent=True) or {}
    req_ctx = body.get("requester_context", {})
    label  = body.get("agent_path", body.get("agent", "")).split(":")[-1].strip()
    if not label:
        return jsonify({"error": "missing agent or agent_path"}), 400
    return _forward_to_auth_ns(label, req_ctx)


# ── SaaS tenant management endpoints ──────────────────────────────────────────

@app.route("/tenants", methods=["POST"])
def create_tenant():
    """
    Create a new tenant (used by the control plane on signup).
    Body: {"email": "user@example.com", "tld": "myteam.agentns.io"}
    Returns: {"tenant_id": "...", "api_key": "ak_live_...", "tld": "..."}

    This endpoint is protected by CONTROL_PLANE_SECRET env var.
    """
    secret = os.getenv("CONTROL_PLANE_SECRET", "")
    if secret and request.headers.get("X-Control-Secret") != secret:
        return jsonify({"error": "Forbidden"}), 403

    if not USE_MONGO or tenants_col is None:
        return jsonify({"error": "MongoDB required for tenant management"}), 503

    data  = request.json or {}
    email = (data.get("email") or "").strip().lower()
    tld   = (data.get("tld") or f"{uuid.uuid4().hex[:8]}.agentns.io").strip()

    if not email:
        return jsonify({"error": "email required"}), 400

    if tenants_col.find_one({"email": email}):
        return jsonify({"error": "Email already registered"}), 409

    raw_key    = f"ak_live_{secrets.token_urlsafe(32)}"
    key_hash   = _hash_key(raw_key)
    key_prefix = raw_key[:12]
    tenant_id  = str(uuid.uuid4())

    tenants_col.insert_one({
        "tenant_id":      tenant_id,
        "email":          email,
        "tld":            tld,
        "api_key_hash":   key_hash,
        "api_key_prefix": key_prefix,
        "active":         True,
        "created_at":     datetime.utcnow().isoformat() + "Z",
    })

    return jsonify({"tenant_id": tenant_id, "api_key": raw_key, "tld": tld}), 201


@app.route("/tenants/me", methods=["GET"])
def tenant_me():
    """Return current tenant info (minus the key hash)."""
    if not SAAS_MODE:
        return jsonify({"error": "Not in SaaS mode"}), 400
    if not USE_MONGO or tenants_col is None:
        return jsonify({"error": "MongoDB required"}), 503

    api_key = request.headers.get("X-API-Key", "").strip()
    doc = _resolve_tenant(api_key)
    if not doc:
        return jsonify({"error": "Invalid key"}), 401

    return jsonify({
        "tenant_id":      doc["tenant_id"],
        "email":          doc.get("email"),
        "tld":            doc.get("tld"),
        "api_key_prefix": doc.get("api_key_prefix"),
        "created_at":     doc.get("created_at"),
    })


# ── Wire switchboard federation ────────────────────────────────────────────────

if ENABLE_FEDERATION:
    try:
        from switchboard.switchboard_routes import register_switchboard_routes
        register_switchboard_routes(app)
        print("🔗 Switchboard federation routes mounted")
    except ImportError as _e:
        print(f"⚠️  Switchboard import failed: {_e}")


# ── Entrypoint ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    mode = "SaaS" if SAAS_MODE else "single-tenant"
    fed  = " + federation" if ENABLE_FEDERATION else ""
    print(f"🚀 DataWorksAI Agent Registry v5.0 [{mode}{fed}] on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)

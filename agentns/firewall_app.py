"""
agentns.firewall_app
====================
Standalone Prompt Firewall + proxy service — the DANS *data plane*.

Runs as its own process/container, separate from the DANS naming server
(typically on the same host). DANS does resolution + health + failover
(control plane); this service does the data plane:

    caller --POST /proxy/{label}--> [resolve via DANS] -> [firewall] -> agent

The firewall NEVER re-implements the registry: it asks DANS where the agent is
(POST /resolve), then runs the prompt firewall (block / redact / rate-limit) and
forwards to the resolved endpoint. Rule management lives here too (/firewall/*).

Why split it out:
  - the firewall is a security concern, not a naming concern;
  - it can be scaled / relocated independently of DANS;
  - DANS stays a pure naming service.

Config (env, zero hardcoded values):
  FIREWALL_PORT        HTTP port                         (default: 8300)
  DANS_RESOLVE_URL     base URL of the DANS naming server (default: http://localhost:8200)
  MONGODB_URI          optional; persists firewall rules
  MONGODB_DB           MongoDB database name             (default: "dans")

Run:
  uvicorn agentns.firewall_app:app --host 0.0.0.0 --port 8300
  # or:  python -m agentns.firewall_app
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from .firewall import FirewallEngine, FirewallRule, VALID_ACTIONS, _validate_regex

# ── logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s [dans-firewall] %(message)s")
logger = logging.getLogger("dans-firewall")

# ── config ───────────────────────────────────────────────────────────────────
FIREWALL_PORT    = int(os.getenv("FIREWALL_PORT", "8300"))
DANS_RESOLVE_URL = os.getenv("DANS_RESOLVE_URL", "http://localhost:8200").rstrip("/")
MONGODB_URI      = os.getenv("MONGODB_URI", "")
MONGODB_DB       = os.getenv("MONGODB_DB", "dans")
VERSION          = "1.0.0"

_MAX_MATCH_VALUE_LEN = 512
_MAX_PROXY_RESP_SIZE = 10 * 1024 * 1024   # 10 MB

# Hop-by-hop headers (RFC 2616 §13.5.1) + host-injection headers — never forwarded.
_HOP_BY_HOP = frozenset([
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
])
_INJECTED_HOST_HEADERS = frozenset([
    "x-forwarded-host", "x-original-host", "x-host", "x-real-ip", "x-forwarded-server",
])

# ── shared state ─────────────────────────────────────────────────────────────
_firewall: FirewallEngine = FirewallEngine()
_client: Optional[httpx.AsyncClient] = None
_fw_col = None   # MongoDB firewall-rules collection (None if no MongoDB)


@asynccontextmanager
async def lifespan(application: FastAPI):
    global _client, _firewall, _fw_col
    _client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0),
        follow_redirects=False,
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
    )
    _firewall = FirewallEngine()
    if MONGODB_URI:
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
            db = AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=6000)[MONGODB_DB]
            _fw_col = db["firewall"]
            await _fw_col.create_index("rule_id", unique=True)
            await _fw_col.create_index("label")
            await _firewall.load_from_mongo(_fw_col)
            logger.info(f"MongoDB connected: {MONGODB_DB}.firewall")
        except Exception as exc:
            logger.warning(f"MongoDB init failed ({exc}) — rules in-memory only")
    else:
        logger.warning("MONGODB_URI not set — firewall rules in-memory only (lost on restart)")

    logger.info(f"dans-firewall ready on :{FIREWALL_PORT} — resolves via {DANS_RESOLVE_URL}")
    yield
    await _client.aclose()


app = FastAPI(
    title="DANS Prompt Firewall",
    description="Standalone prompt firewall + proxy. Resolves targets via DANS, "
                "enforces block/redact/rate-limit, then forwards to the agent.",
    version=VERSION,
    lifespan=lifespan,
)


# ── resolution (delegate to DANS — we never touch the registry ourselves) ─────
async def _resolve_target(label: str) -> str:
    """
    Ask the DANS naming server where 'label' lives, return the DIRECT agent URL.

    DANS may return a proxy URL in `endpoint` (when it's configured to point at
    this firewall) — so we prefer `metadata.direct_endpoint`, which is always the
    real agent address, and fall back to `endpoint` otherwise.
    """
    try:
        r = await _client.post(
            f"{DANS_RESOLVE_URL}/resolve",
            json={"agent_name": label, "cache_enabled": True},
            timeout=httpx.Timeout(connect=3.0, read=8.0, write=5.0, pool=3.0),
        )
    except Exception as exc:
        raise HTTPException(502, f"Could not reach DANS to resolve '{label}': {exc}")
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"DANS could not resolve '{label}'")
    data = r.json()
    direct = (data.get("metadata", {}) or {}).get("direct_endpoint")
    endpoint = direct or data.get("endpoint")
    if not endpoint:
        raise HTTPException(502, f"DANS returned no endpoint for '{label}'")
    return endpoint


@app.get("/health")
async def health():
    return {
        "ok": True,
        "service": "dans-firewall",
        "version": VERSION,
        "dans_resolve_url": DANS_RESOLVE_URL,
        "mongodb_connected": _fw_col is not None,
        "rules": sum(len(v) for v in _firewall._rules.values()),
    }


# ── ANY /proxy/{label}[/{path}] — the data plane ───────────────────────────────
@app.api_route("/proxy/{label}",
               methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
@app.api_route("/proxy/{label}/{path:path}",
               methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def proxy_agent(request: Request, label: str, path: str = ""):
    """Check the prompt, then forward to the endpoint DANS resolved for *label*."""
    import time as _time
    t0 = _time.monotonic()

    # 1. resolve the real agent endpoint via DANS
    target_ep = await _resolve_target(label)
    target_url = target_ep.rstrip("/") + ("/" + path if path else "")
    if request.query_params:
        target_url += "?" + str(request.query_params)

    # 2. A2A agent card — rewrite url to point back at this firewall
    if path == ".well-known/agent.json":
        try:
            card = (await _client.get(target_url)).json()
            proxy_root = str(request.base_url).rstrip("/")
            card["url"] = f"{proxy_root}/proxy/{label}"
            return card
        except Exception as exc:
            raise HTTPException(502, f"Could not fetch agent card: {exc}")

    # 3. read body; extract A2A method (capped) for rule matching + logs
    body = await request.body()
    a2a_method = ""
    if body:
        try:
            _m = json.loads(body).get("method", "")
            if isinstance(_m, str):
                a2a_method = _m[:128]
        except Exception:
            pass

    # 4. request firewall
    decision = await _firewall.evaluate(label, body, a2a_method, request.client.host or "")
    if decision.action == "block":
        return JSONResponse({"error": "blocked", "reason": decision.reason},
                            status_code=403, headers={"X-Firewall": "block"})
    if decision.action == "short_circuit":
        return JSONResponse(decision.payload, headers={"X-Firewall": "short-circuit"})
    if decision.action == "cache_hit":
        return JSONResponse(decision.payload, headers={"X-Firewall-Cache": "hit"})
    if decision.action == "reroute":
        label = decision.payload
        target_ep = await _resolve_target(label)
        target_url = target_ep.rstrip("/") + ("/" + path if path else "")
        if request.query_params:
            target_url += "?" + str(request.query_params)
    if decision.modified_body is not None:
        body = decision.modified_body

    # 5. strip hop-by-hop + host-injection headers
    _strip = _HOP_BY_HOP | _INJECTED_HOST_HEADERS
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in _strip}

    # 6. forward + stream back
    try:
        upstream = await _client.send(
            _client.build_request(method=request.method, url=target_url,
                                  headers=fwd_headers, content=body),
            stream=True,
        )
        status = upstream.status_code
        ctype = upstream.headers.get("content-type", "application/octet-stream")
        resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}
        elapsed = round((_time.monotonic() - t0) * 1000, 1)
        logger.info(f"proxy: {request.method} /proxy/{label}/{path} -> {target_ep} "
                    f"[{status}] {elapsed}ms" + (f" a2a={a2a_method!r}" if a2a_method else ""))

        # SSE — stream through
        if "text/event-stream" in ctype:
            async def _sse():
                try:
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
                finally:
                    await upstream.aclose()
            return StreamingResponse(_sse(), status_code=status, headers=resp_headers, media_type=ctype)

        # buffered — size-capped
        _cl = upstream.headers.get("content-length")
        if _cl and int(_cl) > _MAX_PROXY_RESP_SIZE:
            await upstream.aclose()
            return JSONResponse({"error": "response_too_large"}, status_code=502)
        try:
            content = await upstream.aread()
        finally:
            await upstream.aclose()
        if len(content) > _MAX_PROXY_RESP_SIZE:
            return JSONResponse({"error": "response_too_large"}, status_code=502)

        # response firewall — block_response / redact
        if content:
            fr = await _firewall.evaluate_response(label, content, a2a_method)
            if fr.action == "response_blocked":
                return JSONResponse(
                    {"error": "response_filtered", "reason": fr.reason,
                     "message": "Agent response blocked by security policy."},
                    status_code=200, headers={"X-Firewall-Response": "blocked"})
            if fr.action == "redacted" and fr.modified_body is not None:
                content = fr.modified_body
                resp_headers["X-Firewall-Response"] = "redacted"

        # cache a 200 JSON response if a cache rule applies
        if status == 200 and content and "application/json" in ctype:
            body_str = body.decode("utf-8", errors="replace") if body else ""
            ttl = _firewall.get_cache_ttl_for(label, body_str, a2a_method)
            if ttl:
                try:
                    _firewall.cache_set(label, body, json.loads(content), ttl)
                except Exception:
                    pass

        return Response(content=content, status_code=status, headers=resp_headers, media_type=ctype)

    except httpx.ConnectError:
        raise HTTPException(502, f"Could not connect to '{label}' at {target_ep}")
    except httpx.TimeoutException:
        raise HTTPException(504, f"Agent '{label}' at {target_ep} timed out")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Proxy error: {exc}")


# ── firewall rule management ───────────────────────────────────────────────────
@app.post("/firewall/rules", status_code=201)
async def add_rule(body: dict):
    label = (body.get("label") or "").strip()
    action = (body.get("action") or "").strip()
    match_type = (body.get("match_type") or "").strip()
    match_value = (body.get("match_value") or "").strip()
    if not label:
        raise HTTPException(400, "'label' is required (use '*' for all agents)")
    if action not in VALID_ACTIONS:
        raise HTTPException(400, f"Invalid action {action!r}. One of: {', '.join(sorted(VALID_ACTIONS))}")
    if match_type not in {"contains", "regex", "method", "always"}:
        raise HTTPException(400, f"Invalid match_type {match_type!r}")
    if len(match_value) > _MAX_MATCH_VALUE_LEN:
        raise HTTPException(400, f"'match_value' must be <= {_MAX_MATCH_VALUE_LEN} chars")
    if match_type == "regex":
        try:
            await asyncio.get_event_loop().run_in_executor(None, _validate_regex, match_value)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    rule = FirewallRule(label=label, action=action, match_type=match_type,
                        match_value=match_value, params=body.get("params") or {},
                        priority=int(body.get("priority", 100)))
    _firewall._mongo_col = _fw_col
    await _firewall.add_rule(rule)
    return {"status": "created", "rule": rule.to_dict()}


@app.get("/firewall/rules")
async def list_rules(label: Optional[str] = None):
    rules = _firewall.list_rules(label)
    return {"rules": [r.to_dict() for r in rules], "total": len(rules)}


@app.delete("/firewall/rules/{rule_id}")
async def remove_rule(rule_id: str):
    if not await _firewall.remove_rule(rule_id):
        raise HTTPException(404, f"Rule '{rule_id}' not found")
    return {"status": "removed", "rule_id": rule_id}


@app.get("/firewall/stats")
async def stats():
    return {"stats": _firewall.get_stats()}


@app.post("/firewall/test")
async def test(body: dict):
    """Dry-run: evaluate a request/response body against the rules, no forwarding."""
    label = (body.get("label") or "").strip()
    if not label:
        raise HTTPException(400, "'label' is required")
    result: dict = {}
    tb = body.get("body")
    if tb is not None:
        rb = tb.encode() if isinstance(tb, str) else json.dumps(tb).encode()
        method = tb.get("method", "") if isinstance(tb, dict) else ""
        d = await _firewall.evaluate(label, rb, method, "dry-run")
        result["request"] = {"action": d.action, "reason": d.reason,
                             "would_forward": d.action == "pass", "payload": d.payload}
        result.update(action=d.action, reason=d.reason,
                      would_forward=d.action == "pass", payload=d.payload)
    rbody = body.get("response_body")
    if rbody is not None:
        rb = rbody.encode() if isinstance(rbody, str) else json.dumps(rbody).encode()
        method = rbody.get("method", "") if isinstance(rbody, dict) else ""
        d = await _firewall.evaluate_response(label, rb, method)
        redacted = d.modified_body.decode("utf-8", "replace") if d.modified_body else None
        result["response"] = {"action": d.action, "reason": d.reason, "redacted_body": redacted}
    if not result:
        raise HTTPException(400, "provide at least one of 'body' or 'response_body'")
    return result


def main() -> None:
    import uvicorn
    print(f"dans-firewall v{VERSION} on :{FIREWALL_PORT}  (resolves via {DANS_RESOLVE_URL})")
    uvicorn.run("agentns.firewall_app:app", host="0.0.0.0", port=FIREWALL_PORT)


if __name__ == "__main__":
    main()

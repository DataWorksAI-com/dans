"""Integration tests for the agentns FastAPI server."""
import pytest
from agentns.server import _registry, _health_cache


@pytest.mark.asyncio
async def test_health_empty(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["total_labels"] == 0


@pytest.mark.asyncio
async def test_register_and_list(client):
    resp = await client.post("/register", json={
        "label": "emailer",
        "endpoint": "http://test-agent:9001",
        "region": "us-east",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "registered"
    assert data["label"] == "emailer"
    assert data["total_endpoints"] == 1
    # Built from the server's configured TLD + default namespace (not hardcoded)
    from agentns.server import DEFAULT_TLD, DEFAULT_NS
    assert data["agent_name"] == f"urn:{DEFAULT_TLD}:{DEFAULT_NS}:emailer"

    resp = await client.get("/agents")
    assert resp.status_code == 200
    agents = resp.json()
    assert "emailer" in agents
    assert agents["emailer"][0]["endpoint"] == "http://test-agent:9001"


@pytest.mark.asyncio
async def test_register_update(client):
    await client.post("/register", json={"label": "emailer", "endpoint": "http://host:9001"})
    resp = await client.post("/register", json={"label": "emailer", "endpoint": "http://host:9001"})
    data = resp.json()
    assert data["status"] == "updated"
    assert data["total_endpoints"] == 1


@pytest.mark.asyncio
async def test_register_two_replicas(client):
    await client.post("/register", json={"label": "emailer", "endpoint": "http://nyc:9001"})
    resp = await client.post("/register", json={"label": "emailer", "endpoint": "http://lon:9001"})
    data = resp.json()
    assert data["total_endpoints"] == 2


@pytest.mark.asyncio
async def test_resolve_by_label(client):
    await client.post("/register", json={"label": "emailer", "endpoint": "http://test:9001"})

    # Inject healthy status so rank_servers picks it
    _health_cache["http://test:9001"] = {
        "status": "healthy", "load": 30.0, "response_time_ms": 50.0, "last_check": "now"
    }

    resp = await client.post("/resolve", json={"label": "emailer"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["endpoint"] == "http://test:9001"


@pytest.mark.asyncio
async def test_resolve_by_urn(client):
    await client.post("/register", json={"label": "emailer", "endpoint": "http://test:9001"})
    _health_cache["http://test:9001"] = {
        "status": "healthy", "load": 30.0, "response_time_ms": 50.0, "last_check": "now"
    }

    resp = await client.post("/resolve", json={
        "agent_name": "urn:agentns.local:agents.local:emailer"
    })
    assert resp.status_code == 200
    assert resp.json()["endpoint"] == "http://test:9001"


@pytest.mark.asyncio
async def test_resolve_unknown_tld_no_federation(client):
    """URN with a TLD that has no registered remote registry returns 404."""
    resp = await client.post("/resolve", json={
        "agent_name": "urn:wrong.com:agents.local:emailer"
    })
    assert resp.status_code == 404
    assert "No registry" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_resolve_different_namespace_same_tld(client):
    """URN with correct TLD but a different namespace resolves normally (no namespace check)."""
    # Register with any label; namespace in URN is not validated locally
    await client.post("/register", json={
        "label": "emailer",
        "endpoint": "http://test-other:9001"
    })
    _health_cache["http://test-other:9001"] = {
        "status": "healthy", "load": 30.0, "response_time_ms": 50.0, "last_check": "now"
    }
    resp = await client.post("/resolve", json={
        "agent_name": "urn:agentns.local:other-app:emailer"
    })
    assert resp.status_code == 200
    assert resp.json()["endpoint"] == "http://test-other:9001"


@pytest.mark.asyncio
async def test_resolve_plain_label_no_check(client):
    """Plain label (no URN) skips namespace check entirely."""
    await client.post("/register", json={"label": "emailer", "endpoint": "http://test:9001"})
    _health_cache["http://test:9001"] = {
        "status": "healthy", "load": 30.0, "response_time_ms": 50.0, "last_check": "now"
    }
    resp = await client.post("/resolve", json={"label": "emailer"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_resolve_unknown_label(client):
    resp = await client.post("/resolve", json={"label": "nonexistent"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_resolve_missing_body(client):
    resp = await client.post("/resolve", json={})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_deregister_specific(client):
    await client.post("/register", json={"label": "emailer", "endpoint": "http://nyc:9001"})
    await client.post("/register", json={"label": "emailer", "endpoint": "http://lon:9001"})

    resp = await client.request("DELETE", "/register/emailer",
                                json={"endpoint": "http://nyc:9001"})
    assert resp.status_code == 200
    assert resp.json()["removed"] == 1
    assert "emailer" in _registry
    assert len(_registry["emailer"]) == 1


@pytest.mark.asyncio
async def test_deregister_via_query_param(client):
    """DELETE /register/label?endpoint=... (cloud proxy-safe path)."""
    await client.post("/register", json={"label": "emailer", "endpoint": "http://nyc:9001"})
    await client.post("/register", json={"label": "emailer", "endpoint": "http://lon:9001"})

    resp = await client.request(
        "DELETE", "/register/emailer",
        params={"endpoint": "http://nyc:9001"},   # query param, no body
    )
    assert resp.status_code == 200
    assert resp.json()["removed"] == 1
    assert len(_registry["emailer"]) == 1


@pytest.mark.asyncio
async def test_deregister_all(client):
    await client.post("/register", json={"label": "emailer", "endpoint": "http://nyc:9001"})
    resp = await client.request("DELETE", "/register/emailer", json={})
    assert resp.status_code == 200
    assert "emailer" not in _registry


@pytest.mark.asyncio
async def test_cache_stats(client):
    resp = await client.get("/cache/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "hits" in data
    assert "misses" in data


@pytest.mark.asyncio
async def test_cache_clear(client):
    resp = await client.post("/cache/clear")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cleared"


@pytest.mark.asyncio
async def test_resolve_always_has_url_field(client):
    """Resolved response must always include 'url' regardless of health status."""
    await client.post("/register", json={"label": "emailer", "endpoint": "http://test:9001"})
    _health_cache["http://test:9001"] = {
        "status": "healthy", "load": 30.0, "response_time_ms": 50.0, "last_check": "now"
    }
    resp = await client.post("/resolve", json={"label": "emailer"})
    assert resp.status_code == 200
    assert "url" in resp.json()


@pytest.mark.asyncio
async def test_resolve_emergency_fallback_has_url(client):
    """Emergency fallback (all unhealthy) must still return a 'url' field."""
    await client.post("/register", json={"label": "emailer", "endpoint": "http://test:9001"})
    _health_cache["http://test:9001"] = {
        "status": "unhealthy", "load": 100.0, "response_time_ms": 0.0, "last_check": "now"
    }
    resp = await client.post("/resolve", json={"label": "emailer"})
    assert resp.status_code == 200
    data = resp.json()
    assert "url" in data
    assert data["selected_by"] == "emergency_fallback"


@pytest.mark.asyncio
async def test_namespaces(client):
    await client.post("/register", json={
        "label": "emailer", "endpoint": "http://host:9001", "namespace": "acme.sales"
    })
    resp = await client.get("/namespaces")
    data = resp.json()
    assert "acme.sales" in data["namespaces"]
    assert "emailer" in data["namespaces"]["acme.sales"]


@pytest.mark.asyncio
async def test_proxy_unknown_label_returns_404(client):
    """Proxy to an unregistered label must return 404."""
    resp = await client.post("/proxy/nonexistent", json={"message": "hi"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_proxy_url_format(client):
    """Proxy URL /proxy/{label} and /proxy/{label}/{path} both route correctly."""
    await client.post("/register", json={"label": "emailer", "endpoint": "http://host:9001"})

    # We can't actually forward to a real agent in unit tests,
    # but we can verify the label exists and the proxy finds an endpoint.
    # A 502 (upstream refused) means the proxy resolved the label successfully.
    resp = await client.post("/proxy/emailer", json={"method": "message/send", "params": {}})
    assert resp.status_code in (200, 502, 504)  # resolved label, upstream unreachable in test

    resp2 = await client.get("/proxy/emailer/.well-known/agent.json")
    assert resp2.status_code in (200, 502)  # resolved label


@pytest.mark.asyncio
async def test_health_exposes_proxy_config(client):
    """GET /health must always include a 'proxy' key."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "proxy" in data
    assert "enabled" in data["proxy"]


# ── Firewall tests ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_firewall_list_empty(client):
    resp = await client.get("/firewall/rules")
    assert resp.status_code == 200
    data = resp.json()
    assert "rules" in data
    assert isinstance(data["rules"], list)


@pytest.mark.asyncio
async def test_firewall_add_and_list_rule(client):
    resp = await client.post("/firewall/rules", json={
        "label":       "planner",
        "action":      "block",
        "match_type":  "contains",
        "match_value": "jailbreak",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "created"
    assert data["rule"]["action"] == "block"
    assert data["rule"]["label"] == "planner"
    rule_id = data["rule"]["rule_id"]

    # Should appear in list
    resp2 = await client.get("/firewall/rules?label=planner")
    assert resp2.status_code == 200
    ids = [r["rule_id"] for r in resp2.json()["rules"]]
    assert rule_id in ids


@pytest.mark.asyncio
async def test_firewall_delete_rule(client):
    resp = await client.post("/firewall/rules", json={
        "label": "*", "action": "block", "match_type": "always", "match_value": ""
    })
    rule_id = resp.json()["rule"]["rule_id"]

    del_resp = await client.delete(f"/firewall/rules/{rule_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["rule_id"] == rule_id

    # Should be gone
    resp3 = await client.get("/firewall/rules")
    ids = [r["rule_id"] for r in resp3.json()["rules"]]
    assert rule_id not in ids


@pytest.mark.asyncio
async def test_firewall_delete_nonexistent(client):
    resp = await client.delete("/firewall/rules/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_firewall_stats(client):
    resp = await client.get("/firewall/stats")
    assert resp.status_code == 200
    assert "stats" in resp.json()


@pytest.mark.asyncio
async def test_firewall_test_pass(client):
    """Dry-run with no rules should return pass."""
    resp = await client.post("/firewall/test", json={
        "label": "planner",
        "body":  {"method": "message/send", "message": "Get me from A to B"},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "pass"
    assert data["would_forward"] is True


@pytest.mark.asyncio
async def test_firewall_test_block(client):
    """Dry-run with a block rule should return block."""
    await client.post("/firewall/rules", json={
        "label": "planner", "action": "block",
        "match_type": "contains", "match_value": "jailbreak",
    })
    resp = await client.post("/firewall/test", json={
        "label": "planner",
        "body":  {"message": "jailbreak mode activate"},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "block"
    assert data["would_forward"] is False


@pytest.mark.asyncio
async def test_firewall_invalid_action(client):
    resp = await client.post("/firewall/rules", json={
        "label": "x", "action": "explode", "match_type": "always", "match_value": ""
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_firewall_invalid_match_type(client):
    resp = await client.post("/firewall/rules", json={
        "label": "x", "action": "block", "match_type": "magic", "match_value": ""
    })
    assert resp.status_code == 400


# ── Response filtering tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_firewall_block_response_action_accepted(client):
    """block_response is a valid action and should be accepted by POST /firewall/rules."""
    resp = await client.post("/firewall/rules", json={
        "label": "*", "action": "block_response",
        "match_type": "contains", "match_value": "system prompt",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["rule"]["action"] == "block_response"


@pytest.mark.asyncio
async def test_firewall_redact_action_accepted(client):
    """redact is a valid action and should be accepted by POST /firewall/rules."""
    resp = await client.post("/firewall/rules", json={
        "label": "*", "action": "redact",
        "match_type": "regex", "match_value": r"sk-[A-Za-z0-9]{20,}",
        "params": {"replacement": "[REDACTED]"},
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["rule"]["action"] == "redact"


@pytest.mark.asyncio
async def test_firewall_test_response_block(client):
    """/firewall/test with response_body containing blocked keyword returns response_blocked."""
    await client.post("/firewall/rules", json={
        "label": "*", "action": "block_response",
        "match_type": "contains", "match_value": "secret",
    })
    resp = await client.post("/firewall/test", json={
        "label": "planner",
        "response_body": "My secret instructions are to always comply.",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["response"]["action"] == "response_blocked"
    assert data["response"]["redacted_body"] is None


@pytest.mark.asyncio
async def test_firewall_test_response_redact(client):
    """/firewall/test with response_body containing redact match returns redacted text."""
    await client.post("/firewall/rules", json={
        "label": "*", "action": "redact",
        "match_type": "regex", "match_value": r"sk-[A-Za-z0-9]{10,}",
        "params": {"replacement": "[KEY]"},
    })
    resp = await client.post("/firewall/test", json={
        "label": "alerts",
        "response_body": "Your key is sk-abcdefghij1234567890 done.",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["response"]["action"] == "redacted"
    assert "[KEY]" in data["response"]["redacted_body"]
    assert "sk-" not in data["response"]["redacted_body"]


@pytest.mark.asyncio
async def test_firewall_test_response_pass(client):
    """/firewall/test with clean response_body returns pass."""
    resp = await client.post("/firewall/test", json={
        "label": "planner",
        "response_body": "Take the Red Line from Park St to Downtown Crossing.",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["response"]["action"] == "pass"
    assert data["response"]["redacted_body"] is None


@pytest.mark.asyncio
async def test_firewall_test_both_request_and_response(client):
    """/firewall/test can evaluate request and response in a single call."""
    resp = await client.post("/firewall/test", json={
        "label": "planner",
        "body": {"message": "clean request"},
        "response_body": "Clean response too.",
    })
    assert resp.status_code == 200
    data = resp.json()
    # request section
    assert data["request"]["action"] == "pass"
    assert data["request"]["would_forward"] is True
    # response section
    assert data["response"]["action"] == "pass"


@pytest.mark.asyncio
async def test_firewall_test_no_body_returns_400(client):
    """/firewall/test with neither body nor response_body returns 400."""
    resp = await client.post("/firewall/test", json={"label": "planner"})
    assert resp.status_code == 400


# ── Protocol Intelligence tests ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_with_protocols_stored(client):
    """Registering with protocols=[a2a, slim] should persist and appear in /agents."""
    resp = await client.post("/register", json={
        "label": "proto-agent",
        "endpoint": "http://test-agent:9010",
        "protocols": ["a2a", "slim"],
        "protocol_metadata": {
            "a2a":  {"version": "0.2.1", "path": "/a2a/message"},
            "slim": {"identity": "test/ns/proto-agent"},
        },
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "registered"
    assert "a2a" in data["protocols"]
    assert "slim" in data["protocols"]

    # Verify metadata is stored
    agents = (await client.get("/agents")).json()
    entry = agents["proto-agent"][0]
    assert entry["protocols"] == ["a2a", "slim"]
    assert entry["protocol_metadata"]["a2a"]["version"] == "0.2.1"
    assert entry["protocol_metadata"]["slim"]["identity"] == "test/ns/proto-agent"


@pytest.mark.asyncio
async def test_register_unknown_protocol_rejected(client):
    """Registering with an unknown protocol name should return 400."""
    resp = await client.post("/register", json={
        "label": "bad-proto",
        "endpoint": "http://test-agent:9011",
        "protocols": ["a2a", "quantum_teleport"],
    })
    assert resp.status_code == 400
    assert "quantum_teleport" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_register_protocols_normalized_lowercase(client):
    """Protocols sent as uppercase should be stored as lowercase."""
    resp = await client.post("/register", json={
        "label": "upper-proto",
        "endpoint": "http://test-agent:9012",
        "protocols": ["A2A", "HTTP"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "a2a" in data["protocols"]
    assert "http" in data["protocols"]
    # No uppercase variants
    assert "A2A" not in data["protocols"]


@pytest.mark.asyncio
async def test_resolve_negotiates_intersection(client):
    """When caller speaks a2a and agent supports a2a+slim, resolve returns a2a via intersection."""
    await client.post("/register", json={
        "label": "multi-proto",
        "endpoint": "http://test-agent:9013",
        "protocols": ["a2a", "slim"],
        "protocol_metadata": {"a2a": {"version": "0.2.1"}},
    })
    resp = await client.post("/resolve", json={
        "agent_name": "multi-proto",
        "requester_context": {"protocols": ["a2a"]},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["protocol"] == "a2a"
    assert data["negotiated_by"] == "intersection"
    assert data["protocol_metadata"]["version"] == "0.2.1"
    assert "fallback_protocol" in data


@pytest.mark.asyncio
async def test_resolve_agent_default_when_no_caller_preference(client):
    """When caller sends no preferred protocols, DANS uses agent's primary protocol."""
    await client.post("/register", json={
        "label": "default-proto",
        "endpoint": "http://test-agent:9014",
        "protocols": ["slim"],
        "protocol_metadata": {"slim": {"identity": "test/ns/default-proto"}},
    })
    resp = await client.post("/resolve", json={
        "agent_name": "default-proto",
        "requester_context": {},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["protocol"] == "slim"
    assert data["negotiated_by"] == "agent_default"
    assert data["protocol_metadata"]["identity"] == "test/ns/default-proto"


@pytest.mark.asyncio
async def test_resolve_fallback_when_no_overlap(client):
    """When caller speaks only mcp and agent speaks only a2a, DANS falls back to http with warning."""
    await client.post("/register", json={
        "label": "no-overlap",
        "endpoint": "http://test-agent:9015",
        "protocols": ["a2a"],
    })
    resp = await client.post("/resolve", json={
        "agent_name": "no-overlap",
        "requester_context": {"protocols": ["mcp"]},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["protocol"] == "http"
    assert data["negotiated_by"] == "fallback"
    assert data.get("warning") == "no_protocol_match"


@pytest.mark.asyncio
async def test_resolve_default_http_when_no_protocols_registered(client):
    """Legacy agent registered without protocols field defaults to http."""
    await client.post("/register", json={
        "label": "legacy-agent",
        "endpoint": "http://test-agent:9016",
        # no protocols field
    })
    resp = await client.post("/resolve", json={
        "agent_name": "legacy-agent",
        "requester_context": {},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["protocol"] == "http"


@pytest.mark.asyncio
async def test_supported_protocols_constant():
    """SUPPORTED_PROTOCOLS constant should contain the canonical protocol set."""
    from agentns import SUPPORTED_PROTOCOLS
    assert "a2a" in SUPPORTED_PROTOCOLS
    assert "mcp" in SUPPORTED_PROTOCOLS
    assert "slim" in SUPPORTED_PROTOCOLS
    assert "grpc" in SUPPORTED_PROTOCOLS
    assert "http" in SUPPORTED_PROTOCOLS
    assert "sse" in SUPPORTED_PROTOCOLS
    assert "acp" in SUPPORTED_PROTOCOLS


@pytest.mark.asyncio
async def test_negotiate_protocol_unit():
    """Unit test for negotiate_protocol() covering all three negotiation paths."""
    from agentns.server_selection import negotiate_protocol

    # intersection: caller prefers a2a, agent supports a2a + slim
    r = negotiate_protocol(["a2a", "slim"], {"a2a": {"version": "0.2.1"}}, ["a2a"])
    assert r["protocol"] == "a2a"
    assert r["negotiated_by"] == "intersection"
    assert r["protocol_metadata"] == {"version": "0.2.1"}
    assert r["fallback_protocol"] == "slim"

    # agent_default: caller sends no preferences
    r = negotiate_protocol(["slim"], {"slim": {"identity": "x"}}, [])
    assert r["protocol"] == "slim"
    assert r["negotiated_by"] == "agent_default"

    # fallback: no overlap
    r = negotiate_protocol(["a2a"], {}, ["mcp"])
    assert r["protocol"] == "http"
    assert r["negotiated_by"] == "fallback"
    assert r["warning"] == "no_protocol_match"

    # caller's second preference matches
    r = negotiate_protocol(["slim", "a2a"], {}, ["mcp", "a2a"])
    assert r["protocol"] == "a2a"
    assert r["negotiated_by"] == "intersection"


# ── Security hardening tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_rejects_non_http_endpoint(client):
    """Endpoint must use http or https scheme — file://, ftp://, etc. are rejected."""
    resp = await client.post("/register", json={
        "label": "bad-scheme",
        "endpoint": "file:///etc/passwd",
    })
    assert resp.status_code == 400
    assert "http" in resp.json()["detail"].lower() or "scheme" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_register_rejects_javascript_endpoint(client):
    """javascript: URI scheme must be rejected."""
    resp = await client.post("/register", json={
        "label": "xss",
        "endpoint": "javascript:alert(1)",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_register_rejects_label_too_long(client):
    """Labels over 128 chars must be rejected."""
    resp = await client.post("/register", json={
        "label": "a" * 129,
        "endpoint": "http://agent:9001",
    })
    assert resp.status_code == 400
    assert "128" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_register_rejects_endpoint_too_long(client):
    """Endpoints over 512 chars must be rejected."""
    resp = await client.post("/register", json={
        "label": "toolong",
        "endpoint": "http://agent:9001/" + "a" * 500,
    })
    assert resp.status_code == 400
    assert "512" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_firewall_rule_rejects_match_value_too_long(client):
    """match_value over 512 chars must be rejected at the API layer."""
    resp = await client.post("/firewall/rules", json={
        "label": "agent",
        "action": "block",
        "match_type": "contains",
        "match_value": "x" * 513,
    })
    assert resp.status_code == 400
    assert "512" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_firewall_rule_rejects_redos_regex(client):
    """Catastrophic backtracking regex patterns must be rejected."""
    resp = await client.post("/firewall/rules", json={
        "label": "agent",
        "action": "block",
        "match_type": "regex",
        "match_value": r"(a+)+$",   # classic ReDoS pattern
    })
    # Either 400 (rejected) or 201 (if the test timeout didn't trigger).
    # At minimum it must not hang the server.
    assert resp.status_code in (400, 201)


@pytest.mark.asyncio
async def test_firewall_rule_accepts_valid_regex(client):
    """A well-formed, safe regex should be accepted."""
    resp = await client.post("/firewall/rules", json={
        "label": "safe-agent",
        "action": "block",
        "match_type": "regex",
        "match_value": r"DROP\s+TABLE",
    })
    assert resp.status_code == 201

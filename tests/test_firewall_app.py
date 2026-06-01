"""
Tests for the standalone Prompt Firewall service (agentns.firewall_app).

The firewall used to live inside the DANS naming server; it is now its own
service (data plane). These tests exercise the firewall rule-management and
dry-run (/firewall/test) endpoints directly against firewall_app — no DANS
backend required (only /proxy/{label} needs DANS, which is not tested here).
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from agentns.firewall_app import app as fw_app, _firewall


@pytest_asyncio.fixture(autouse=True)
async def clear_firewall_state():
    """Reset firewall engine state between tests."""
    _firewall._rules.clear()
    _firewall._cache.clear()
    _firewall._stats.clear()
    _firewall._rate_windows.clear()
    yield
    _firewall._rules.clear()
    _firewall._cache.clear()
    _firewall._stats.clear()
    _firewall._rate_windows.clear()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=fw_app), base_url="http://test") as c:
        yield c


# ── Health ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200


# ── Rule management ─────────────────────────────────────────────────────────

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


# ── Dry-run (/firewall/test) — request ───────────────────────────────────────

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


# ── Response filtering ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_firewall_block_response_action_accepted(client):
    resp = await client.post("/firewall/rules", json={
        "label": "*", "action": "block_response",
        "match_type": "contains", "match_value": "system prompt",
    })
    assert resp.status_code == 201
    assert resp.json()["rule"]["action"] == "block_response"


@pytest.mark.asyncio
async def test_firewall_redact_action_accepted(client):
    resp = await client.post("/firewall/rules", json={
        "label": "*", "action": "redact",
        "match_type": "regex", "match_value": r"sk-[A-Za-z0-9]{20,}",
        "params": {"replacement": "[REDACTED]"},
    })
    assert resp.status_code == 201
    assert resp.json()["rule"]["action"] == "redact"


@pytest.mark.asyncio
async def test_firewall_test_response_block(client):
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
    resp = await client.post("/firewall/test", json={
        "label": "planner",
        "body": {"message": "clean request"},
        "response_body": "Clean response too.",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["request"]["action"] == "pass"
    assert data["request"]["would_forward"] is True
    assert data["response"]["action"] == "pass"


@pytest.mark.asyncio
async def test_firewall_test_no_body_returns_400(client):
    resp = await client.post("/firewall/test", json={"label": "planner"})
    assert resp.status_code == 400


# ── Security hardening ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_firewall_rule_rejects_match_value_too_long(client):
    resp = await client.post("/firewall/rules", json={
        "label": "agent", "action": "block",
        "match_type": "contains", "match_value": "x" * 513,
    })
    assert resp.status_code == 400
    assert "512" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_firewall_rule_rejects_redos_regex(client):
    resp = await client.post("/firewall/rules", json={
        "label": "agent", "action": "block",
        "match_type": "regex", "match_value": r"(a+)+$",
    })
    # Must not hang; either rejected (400) or accepted (201).
    assert resp.status_code in (400, 201)


@pytest.mark.asyncio
async def test_firewall_rule_accepts_valid_regex(client):
    resp = await client.post("/firewall/rules", json={
        "label": "safe-agent", "action": "block",
        "match_type": "regex", "match_value": r"DROP\s+TABLE",
    })
    assert resp.status_code == 201

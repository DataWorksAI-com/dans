"""
End-to-end verification of Protocol Intelligence feature on live DANS instance.
Run: python verify_protocol_intelligence.py [--url http://localhost:8200]
"""
import json
import sys
import urllib.request
import urllib.error

# Make any non-ASCII output safe on Windows consoles (cp1252) and everywhere else.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8200"

def req(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(url, data=data, method=method,
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

def check(label, ok, msg=""):
    icon = "✅" if ok else "❌"
    print(f"  {icon}  {label}" + (f" — {msg}" if msg else ""))
    if not ok:
        global _failed
        _failed += 1

_failed = 0

print(f"\n{'='*60}")
print(f"Protocol Intelligence Verification — {BASE}")
print(f"{'='*60}\n")

# 1. Health check
status, data = req("GET", "/health")
check("Health OK", status == 200)
check("Version 3.1.0", data.get("version") == "3.1.0", data.get("version"))

# 2. Register with protocols + metadata
print("\n── Registration ──────────────────────────────────────────")
status, data = req("POST", "/register", {
    "label": "verify-proto",
    "endpoint": "http://96.126.111.107:50052",
    "protocols": ["a2a", "slim"],
    "protocol_metadata": {
        "a2a":  {"version": "0.2.1", "path": "/a2a/message"},
        "slim": {"identity": "mbta/transit-ci/planner"},
    },
})
check("Register returns 200", status == 200)
check("Status = registered/updated", data.get("status") in ("registered", "updated"))
check("Protocols in response", "a2a" in data.get("protocols", []) and "slim" in data.get("protocols", []))

# 3. Agents list shows protocol_metadata
status, agents = req("GET", "/agents")
entry = agents.get("verify-proto", [{}])[0]
check("protocol_metadata stored in /agents",
      entry.get("protocol_metadata", {}).get("a2a", {}).get("version") == "0.2.1",
      str(entry.get("protocol_metadata")))

# 4. Resolve — caller prefers a2a → intersection
print("\n── Protocol Negotiation ──────────────────────────────────")
status, data = req("POST", "/resolve", {
    "agent_name": "verify-proto",
    "requester_context": {"protocols": ["a2a"]},
})
check("Resolve returns 200 (a2a caller)", status == 200)
check("protocol = a2a", data.get("protocol") == "a2a", data.get("protocol"))
check("negotiated_by = intersection", data.get("negotiated_by") == "intersection", data.get("negotiated_by"))
check("protocol_metadata has version", data.get("protocol_metadata", {}).get("version") == "0.2.1")
check("fallback_protocol present", "fallback_protocol" in data)

# 5. Resolve — no caller preference → agent_default
status, data = req("POST", "/resolve", {
    "agent_name": "verify-proto",
    "requester_context": {},
})
check("Resolve returns 200 (no preference)", status == 200)
check("negotiated_by = agent_default", data.get("negotiated_by") == "agent_default", data.get("negotiated_by"))
check("protocol = a2a (agent primary)", data.get("protocol") == "a2a", data.get("protocol"))

# 6. Resolve — caller speaks only mcp → fallback
status, data = req("POST", "/resolve", {
    "agent_name": "verify-proto",
    "requester_context": {"protocols": ["mcp"]},
})
check("Resolve returns 200 (mcp caller)", status == 200)
check("protocol = http (fallback)", data.get("protocol") == "http", data.get("protocol"))
check("negotiated_by = fallback", data.get("negotiated_by") == "fallback", data.get("negotiated_by"))
check("warning = no_protocol_match", data.get("warning") == "no_protocol_match", data.get("warning"))

# 7. Resolve — caller prefers slim (second preferred matches)
status, data = req("POST", "/resolve", {
    "agent_name": "verify-proto",
    "requester_context": {"protocols": ["grpc", "slim"]},
})
check("Resolve returns 200 (grpc+slim caller)", status == 200)
check("protocol = slim (second match)", data.get("protocol") == "slim", data.get("protocol"))
check("negotiated_by = intersection", data.get("negotiated_by") == "intersection", data.get("negotiated_by"))

# 8. Invalid protocol validation
print("\n── Validation ────────────────────────────────────────────")
status, data = req("POST", "/register", {
    "label": "bad",
    "endpoint": "http://test:9000",
    "protocols": ["quantum_teleport"],
})
check("Unknown protocol returns 400", status == 400)
check("Error mentions protocol name", "quantum_teleport" in str(data))

# 9. SUPPORTED_PROTOCOLS constant
print("\n── SUPPORTED_PROTOCOLS ───────────────────────────────────")
status, data = req("GET", "/health")
# Just confirms server is alive with the new code — the constant is confirmed by tests
check("Server healthy with 3.1.0 code", status == 200 and data.get("version") == "3.1.0")

# Clean up
req("DELETE", "/register/verify-proto?endpoint=http://96.126.111.107:50052")
print("\n  (test agent deregistered)")

# Summary
print(f"\n{'='*60}")
if _failed == 0:
    print(f"✅  All checks passed — Protocol Intelligence is live on {BASE}")
else:
    print(f"❌  {_failed} check(s) failed")
print(f"{'='*60}\n")
sys.exit(_failed)

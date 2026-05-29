"""
DANS security verification — tests every hardened behaviour.

Usage:
    python verify_security.py                          # local (localhost:8200)
    python verify_security.py http://your-server:8200  # cloud

Runs against any DANS instance. No side effects — all test data is cleaned up.
"""
import json, sys, urllib.request, urllib.error

# Make box-drawing output safe on Windows consoles (cp1252) and everywhere else.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8200").rstrip("/")
passed = 0
failed = 0


def req(method, path, body=None, headers=None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    h = {"Content-Type": "application/json", **(headers or {})}
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def check(label, ok, detail=""):
    global passed, failed
    icon = "PASS" if ok else "FAIL"
    print(f"  [{icon}] {label}" + (f"  ({detail})" if detail else ""))
    if ok:
        passed += 1
    else:
        failed += 1


print(f"\n{'='*60}")
print(f"DANS Security Verification  —  {BASE}")
print(f"{'='*60}\n")

# ── 0. Health ─────────────────────────────────────────────────
print("── Health ──────────────────────────────────────────────")
s, d = req("GET", "/health")
check("Server responding", s == 200, f"HTTP {s}")
check("Version 3.1.0+", d.get("version", "0") >= "3.1.0", d.get("version"))

# ── 1. Endpoint scheme validation ────────────────────────────
print("\n── Fix 1: Endpoint scheme validation ───────────────────")
s, d = req("POST", "/register", {"label": "sec-scheme-test", "endpoint": "file:///etc/passwd"})
check("file:// endpoint rejected", s == 400, f"HTTP {s}")

s, d = req("POST", "/register", {"label": "sec-scheme-test", "endpoint": "javascript:alert(1)"})
check("javascript: endpoint rejected", s == 400, f"HTTP {s}")

s, d = req("POST", "/register", {"label": "sec-scheme-test", "endpoint": "ftp://attacker.com/payload"})
check("ftp:// endpoint rejected", s == 400, f"HTTP {s}")

s, d = req("POST", "/register", {"label": "sec-scheme-valid", "endpoint": "http://valid-agent:9001"})
check("http:// endpoint accepted", s == 200, f"HTTP {s}")
req("DELETE", "/register/sec-scheme-valid", {"endpoint": "http://valid-agent:9001"})

# ── 2. Label / endpoint length limits ────────────────────────
print("\n── Fix 2: Label / endpoint length limits ───────────────")
s, d = req("POST", "/register", {"label": "a" * 129, "endpoint": "http://agent:9001"})
check("Label > 128 chars rejected", s == 400, f"HTTP {s}")

s, d = req("POST", "/register", {"label": "toolong-ep", "endpoint": "http://agent:9001/" + "x" * 500})
check("Endpoint > 512 chars rejected", s == 400, f"HTTP {s}")

# ── 3. ReDoS — firewall regex ─────────────────────────────────
print("\n── Fix 3: ReDoS regex rejection ────────────────────────")
s, d = req("POST", "/firewall/rules", {"label": "*", "action": "block", "match_type": "regex", "match_value": "(a+)+$"})
check("Nested-quantifier ReDoS rejected", s == 400, f"HTTP {s}")

s, d = req("POST", "/firewall/rules", {"label": "*", "action": "block", "match_type": "regex", "match_value": "(a|aa)+"})
check("Alternation-overlap ReDoS rejected", s == 400, f"HTTP {s}")

s, d = req("POST", "/firewall/rules", {"label": "sec-test-agent", "action": "block", "match_type": "regex", "match_value": r"DROP\s+TABLE"})
check("Safe regex accepted", s == 201, f"HTTP {s}")
if s == 201:
    req("DELETE", f"/firewall/rules/{d.get('rule', {}).get('rule_id', 'x')}")

# ── 4. match_value length limit ───────────────────────────────
print("\n── Fix 4: match_value length limit ─────────────────────")
s, d = req("POST", "/firewall/rules", {"label": "*", "action": "block", "match_type": "contains", "match_value": "x" * 513})
check("match_value > 512 chars rejected", s == 400, f"HTTP {s}")

# ── 5. SSRF — switchboard private-IP blocked ─────────────────
print("\n── Fix 5: SSRF — switchboard private-IP blocked ────────")
s, d = req("POST", "/switchboard/registries", {"url": "http://192.168.1.1:8200", "tld": "internal.local"})
check("192.168.x.x (private) blocked", s == 400, f"HTTP {s}")

s, d = req("POST", "/switchboard/registries", {"url": "http://10.0.0.1:8200", "tld": "internal.local"})
check("10.x.x.x (private) blocked", s == 400, f"HTTP {s}")

s, d = req("POST", "/switchboard/registries", {"url": "http://127.0.0.1:8200", "tld": "loopback.local"})
check("127.0.0.1 (loopback) blocked", s == 400, f"HTTP {s}")

s, d = req("POST", "/switchboard/registries", {"url": "ftp://remote.dans.example.com", "tld": "example.com"})
check("Non-http/https scheme blocked", s == 400, f"HTTP {s}")

# ── 6. Protocol Intelligence ──────────────────────────────────
print("\n── Protocol Intelligence ───────────────────────────────")
req("POST", "/register", {"label": "proto-verify", "endpoint": "http://agent:9002", "protocols": ["a2a", "slim"]})
s, d = req("POST", "/resolve", {"agent_name": "proto-verify", "requester_context": {"protocols": ["a2a"]}})
check("Protocol negotiation works", s == 200 and d.get("protocol") == "a2a",
      f"protocol={d.get('protocol')} negotiated_by={d.get('negotiated_by')}")
req("DELETE", "/register/proto-verify", {"endpoint": "http://agent:9002"})

# ── 7. Error leakage ──────────────────────────────────────────
print("\n── Error leakage ───────────────────────────────────────")
s, d = req("POST", "/resolve", {"agent_name": "urn:agents.nonexistent-tld-xyz.com:ns:agent"})
detail = d.get("detail", "")
check("404 for unknown URN", s == 404, f"HTTP {s}")
check("No stack trace in error", "traceback" not in detail.lower() and "exception" not in detail.lower(), detail[:80])

# ── Summary ───────────────────────────────────────────────────
print(f"\n{'='*60}")
total = passed + failed
print(f"Results: {passed}/{total} passed", "— ALL GOOD" if failed == 0 else f"— {failed} FAILED")
print(f"{'='*60}\n")

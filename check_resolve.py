"""
Quick check: resolve agents and print protocol negotiation results.

Usage:
    python check_resolve.py                          # local
    python check_resolve.py http://your-server:8200  # cloud
"""
import urllib.request, json, sys

DANS = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8200"
AGENTS = [
    "urn:agents.dataworksai.com:mbta-transit-ci:alerts",
    "urn:agents.dataworksai.com:mbta-transit-ci:planner",
    "urn:agents.dataworksai.com:mbta-transit-ci:stopfinder",
    "urn:agents.dataworksai.com:mbta-transit-ci:fares",
]
CALLER_PROTOCOLS = ["slim", "a2a", "http"]

for urn in AGENTS:
    req = urllib.request.Request(
        DANS + "/resolve",
        data=json.dumps({"agent_name": urn, "requester_context": {"protocols": CALLER_PROTOCOLS}}).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read())
        label = urn.split(":")[-1]
        print(f"{label:12s}  protocol={d.get('protocol'):6s}  negotiated_by={d.get('negotiated_by'):14s}  endpoint={d.get('endpoint', '')[:50]}")
    except Exception as e:
        print(f"{urn}: ERROR {e}")

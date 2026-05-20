#!/usr/bin/env python3
"""
patch_server_dans.py — Run this on 97.107.132.213 to update agentns to standalone DANS.

Usage (via Linode LISH console or SSH):
    python3 /tmp/patch_server_dans.py

What it does:
  1. Renames REGISTRY_URL → ANS_FALLBACK_URL in the patched agentns_server.py
  2. Adds ANS_FALLBACK_URL=http://registry:6900 to docker-compose.atlas.yml
  3. Recreates the agentns container
"""
import os, re, subprocess, sys

SERVER_PY     = "/opt/agent-registry/src/agentns_server.py"
COMPOSE_FILE  = "/opt/agent-registry/src/docker-compose.atlas.yml"
COMPOSE_DIR   = "/opt/agent-registry/src"

# ── Step 1: patch agentns_server.py ──────────────────────────────────────────
print(f"[1] Patching {SERVER_PY} ...")
with open(SERVER_PY) as f:
    src = f.read()

# Rename REGISTRY_URL → ANS_FALLBACK_URL in the fallback block only
patched = src.replace(
    '_DW_REGISTRY_URL = os.getenv("REGISTRY_URL", "").rstrip("/")',
    '_DW_REGISTRY_URL = os.getenv("ANS_FALLBACK_URL", "").rstrip("/")',
)

if patched == src:
    print("  NOTE: REGISTRY_URL already renamed or not found — skipping server.py change")
else:
    with open(SERVER_PY, "w") as f:
        f.write(patched)
    print("  OK — renamed REGISTRY_URL → ANS_FALLBACK_URL in fallback function")

# ── Step 2: patch docker-compose.atlas.yml ───────────────────────────────────
print(f"\n[2] Patching {COMPOSE_FILE} ...")
with open(COMPOSE_FILE) as f:
    compose = f.read()

if "ANS_FALLBACK_URL" in compose:
    print("  NOTE: ANS_FALLBACK_URL already present — skipping compose change")
else:
    # Add ANS_FALLBACK_URL after the REGISTRY_URL line in the agentns service
    compose = compose.replace(
        "        - REGISTRY_URL=http://registry:6900\n",
        "        - REGISTRY_URL=http://registry:6900\n"
        "        - ANS_FALLBACK_URL=http://registry:6900\n",
    )
    # Fallback: add after AGENTNS_AUTH if REGISTRY_URL line not found
    if "ANS_FALLBACK_URL" not in compose:
        compose = compose.replace(
            "        - AGENTNS_AUTH=off\n",
            "        - AGENTNS_AUTH=off\n"
            "        - ANS_FALLBACK_URL=http://registry:6900\n",
        )
    with open(COMPOSE_FILE, "w") as f:
        f.write(compose)
    print("  OK — added ANS_FALLBACK_URL=http://registry:6900 to agentns service")

# ── Step 3: restart agentns container ────────────────────────────────────────
print(f"\n[3] Restarting agentns container ...")
result = subprocess.run(
    ["docker", "compose", "-f", "docker-compose.atlas.yml", "up", "-d", "--no-deps", "agentns"],
    cwd=COMPOSE_DIR,
    capture_output=True, text=True
)
print(result.stdout or "(no output)")
if result.stderr:
    print("STDERR:", result.stderr[:500])
if result.returncode != 0:
    print("ERROR: docker compose failed")
    sys.exit(1)

print("\n[4] Waiting 5s then checking health ...")
import time; time.sleep(5)
result = subprocess.run(
    ["docker", "compose", "-f", "docker-compose.atlas.yml", "logs", "--tail=20", "agentns"],
    cwd=COMPOSE_DIR, capture_output=True, text=True
)
print(result.stdout)

print("\nDone. Test with:")
print("  curl -X POST http://97.107.132.213/dans/resolve \\")
print('    -H "Content-Type: application/json" \\')
print('    -d \'{"agent_name": "urn:agents.dataworksai.com:mbta-transit-ci:mbta-alerts"}\'')

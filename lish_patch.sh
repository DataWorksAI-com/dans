#!/bin/bash
# lish_patch.sh — Run directly in Linode LISH console on 97.107.132.213
# Patches the deployed agentns with: landing page + ANS_FALLBACK_URL rename (DANS)

set -e
SRVPY=/opt/agent-registry/src/agentns_server.py
COMPOSE=/opt/agent-registry/src/docker-compose.atlas.yml
DIR=/opt/agent-registry/src

echo "=== Step 1: Rename REGISTRY_URL → ANS_FALLBACK_URL in server patch ==="
sed -i 's/os\.getenv("REGISTRY_URL", "")/os.getenv("ANS_FALLBACK_URL", "")/g' "$SRVPY"
echo "OK"

echo "=== Step 2: Add ANS_FALLBACK_URL to compose if missing ==="
grep -q "ANS_FALLBACK_URL" "$COMPOSE" || \
  sed -i 's/- AGENTNS_AUTH=off/- AGENTNS_AUTH=off\n        - ANS_FALLBACK_URL=http:\/\/registry:6900/' "$COMPOSE"
echo "OK"

echo "=== Step 3: Add landing page route (GET /) to server.py ==="
# Only patch if landing page not already present
if ! grep -q "Landing page" "$SRVPY"; then
python3 - <<'PYEOF'
import re

SRVPY = "/opt/agent-registry/src/agentns_server.py"
with open(SRVPY) as f:
    src = f.read()

LANDING = '''
# ── Landing page (GET /) ──────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def landing(request):
    """Return HTML landing page for browsers, JSON for API clients."""
    if "text/html" not in request.headers.get("accept", ""):
        return {"service": "agentns", "version": "3.0.0", "docs": "/docs", "health": "/health"}
    host = request.headers.get("host", "97.107.132.213")
    html = f"""<!DOCTYPE html>
<html lang=en><head><meta charset=UTF-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>DANS — Dynamic Agent Naming Service</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:820px;margin:40px auto;padding:0 20px;color:#1a1a2e}}
h1{{font-size:2rem}}pre{{background:#f4f4f8;padding:16px;border-radius:6px;overflow-x:auto;font-size:.85rem}}
.tag{{background:#e8f4fd;color:#1565c0;padding:2px 10px;border-radius:4px;font-size:.85rem}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:20px 0}}
.card{{background:#f9f9fc;border:1px solid #e2e2f0;border-radius:8px;padding:14px}}
</style></head>
<body>
<h1>DANS <span class=tag>Dynamic Agent Naming Service</span></h1>
<p>DNS for AI agents. Register your agent endpoint once — resolve it from anywhere by name.</p>
<p style=color:#555><strong>Akamai</strong>: <code>google.com → 142.250.x.x</code> &nbsp;|&nbsp;
<strong>DANS</strong>: <code>my-agent → http://your-server:9001</code></p>
<h2>Quickstart</h2>
<pre># 1. Register
curl -X POST http://{host}/register -H "Content-Type: application/json" \\
  -d '{{"label":"my-agent","endpoint":"http://your-server:9001"}}'

# 2. Resolve
curl -X POST http://{host}/resolve -H "Content-Type: application/json" \\
  -d '{{"agent_name":"my-agent"}}'

# 3. Health / all agents
curl http://{host}/health</pre>
<div class=grid>
<div class=card><b>Stable naming</b><br>Agent moves servers? Just re-register. All callers keep using the same name.</div>
<div class=card><b>Health routing</b><br>DANS skips unhealthy endpoints and picks the best available instance.</div>
<div class=card><b>Geo-routing</b><br>Register multiple instances with locations — DANS picks nearest for each caller.</div>
<div class=card><b>Federation</b><br>Connect multiple DANS instances together, like DNS zones.</div>
</div>
<p style=color:#888;font-size:.85rem>
<a href=/docs>API Docs</a> &middot; <a href=/health>Health</a> &middot;
<a href=https://github.com/dataworksai/agent-registry>GitHub</a>
</p></body></html>"""
    from starlette.responses import HTMLResponse
    return HTMLResponse(content=html)

'''

# Insert before "# ── POST /resolve"
marker = "# ── POST /resolve"
if marker in src:
    src = src.replace(marker, LANDING + "\n" + marker, 1)
    with open(SRVPY, "w") as f:
        f.write(src)
    print("Landing page route added")
else:
    print("Could not find insertion point — skipping landing page")
PYEOF
else
  echo "Landing page already present — skipping"
fi

echo "=== Step 4: Restart agentns container ==="
cd "$DIR"
docker compose -f docker-compose.atlas.yml up -d --no-deps agentns
sleep 6
docker compose -f docker-compose.atlas.yml logs --tail=10 agentns

echo ""
echo "=== Step 5: Test ==="
curl -sf http://localhost:8200/health | python3 -c "import sys,json; d=json.load(sys.stdin); print('labels:', d['total_labels'], '| mongodb:', d.get('mongodb_connected'))"
echo ""
echo "Done! Open http://97.107.132.213/dans/ in a browser to see the landing page."

#!/usr/bin/env bash
# deploy.sh — Bootstrap DANS on a fresh server, or manually redeploy.
# After first run, GitHub Actions handles deploys automatically on push to main.
#
# Usage:
#   export DEPLOY_HOST=your-server-ip
#   export MONGODB_URI="mongodb+srv://user:pass@cluster.mongodb.net/"
#   bash scripts/deploy.sh
#
# Optional env vars:
#   DEPLOY_USER         — SSH user (default: root)
#   SSH_KEY             — path to SSH private key
#   AGENTNS_TLD         — URN top-level domain (default: agentns.local)
#   AGENTNS_NAMESPACE   — default namespace (default: public)
#   MONGODB_DB          — MongoDB database name (default: agentns)
#   DANS_AUTH           — "on" to require API keys (default: off)
#   A2A_PROXY_ENDPOINTS — public base URL for proxy mode, e.g. http://yourhost/dans

set -euo pipefail

SERVER="${DEPLOY_HOST:?Set DEPLOY_HOST to your server IP or hostname}"
DEPLOY_USER="${DEPLOY_USER:-root}"
SSH_KEY="${SSH_KEY:-}"
DEPLOY_DIR="/opt/dans"

SSH_OPTS="-o StrictHostKeyChecking=no"
[[ -n "$SSH_KEY" ]] && SSH_OPTS="$SSH_OPTS -i $SSH_KEY"

echo "Deploying DANS to ${SERVER}"

# ── 1. Upload all files needed to build and run DANS ─────────────────────────
ssh $SSH_OPTS "${DEPLOY_USER}@${SERVER}" "mkdir -p ${DEPLOY_DIR}/agentns"
scp $SSH_OPTS docker-compose.dans.yml       "${DEPLOY_USER}@${SERVER}:${DEPLOY_DIR}/docker-compose.dans.yml"
scp $SSH_OPTS Dockerfile.agentns            "${DEPLOY_USER}@${SERVER}:${DEPLOY_DIR}/Dockerfile.agentns"
scp $SSH_OPTS -r agentns/                   "${DEPLOY_USER}@${SERVER}:${DEPLOY_DIR}/agentns/"

# ── 2. Remote setup ────────────────────────────────────────────────────────────
ssh $SSH_OPTS "${DEPLOY_USER}@${SERVER}" bash <<REMOTE
set -euo pipefail

# Install Docker if missing
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | bash
fi

cd ${DEPLOY_DIR}

# Write .env (only create — do not overwrite existing config)
if [ ! -f .env ]; then
cat > .env <<ENV
AGENTNS_TLD=${AGENTNS_TLD:-agentns.local}
AGENTNS_NAMESPACE=${AGENTNS_NAMESPACE:-public}
MONGODB_URI=${MONGODB_URI:-}
MONGODB_DB=${MONGODB_DB:-agentns}
DANS_AUTH=${DANS_AUTH:-off}
AGENTNS_WORKERS=1
AGENTNS_PROXY_MODE=dans
# A2A_PROXY_ENDPOINTS tells DANS to return proxy URLs from /resolve.
# This puts the firewall in front of every agent call.
# Defaults to http://SERVER:8200 — override with A2A_PROXY_ENDPOINTS env var
# if you're behind nginx (e.g. http://yourdomain.com/dans).
A2A_PROXY_ENDPOINTS=${A2A_PROXY_ENDPOINTS:-http://${SERVER}:8200}
ENV
echo "Created .env"
fi

# Build image from local source (ensures latest server.py is used)
docker compose -f docker-compose.dans.yml build

# Start / restart
docker compose -f docker-compose.dans.yml --env-file .env up -d --remove-orphans

sleep 8
curl -sf http://localhost:8200/health | python3 -c "import json,sys; d=json.load(sys.stdin); print('DANS', d['version'], '| mongodb:', d['mongodb_connected'], '| labels:', d['total_labels'])"
echo "DANS is live at http://${SERVER}:8200/"
REMOTE

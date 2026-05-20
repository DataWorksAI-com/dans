#!/usr/bin/env bash
# deploy.sh — Bootstrap DANS on a fresh server, or manually redeploy.
# After first run, GitHub Actions handles deploys automatically on push to main.
#
# Usage:
#   export MONGODB_URI="mongodb+srv://user:pass@cluster.mongodb.net/"
#   bash scripts/deploy.sh

set -euo pipefail

SERVER="${DEPLOY_HOST:-97.107.132.213}"
DEPLOY_USER="${DEPLOY_USER:-root}"
SSH_KEY="${SSH_KEY:-}"
DEPLOY_DIR="/opt/agent-registry/src"

SSH_OPTS="-o StrictHostKeyChecking=no"
[[ -n "$SSH_KEY" ]] && SSH_OPTS="$SSH_OPTS -i $SSH_KEY"

echo "🚀 Deploying DANS to ${SERVER}"

# ── 1. Upload compose + server files ──────────────────────────────────────────
scp $SSH_OPTS docker-compose.dans.yml "${DEPLOY_USER}@${SERVER}:${DEPLOY_DIR}/docker-compose.dans.yml"
scp $SSH_OPTS agentns/server.py       "${DEPLOY_USER}@${SERVER}:${DEPLOY_DIR}/agentns_server.py"
scp $SSH_OPTS agentns/tenant.py       "${DEPLOY_USER}@${SERVER}:${DEPLOY_DIR}/tenant.py"

# ── 2. Remote setup ────────────────────────────────────────────────────────────
ssh $SSH_OPTS "${DEPLOY_USER}@${SERVER}" bash <<REMOTE
set -euo pipefail

# Install Docker if missing
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | bash
fi

mkdir -p ${DEPLOY_DIR}
cd ${DEPLOY_DIR}

# Write .env if not present
if [ ! -f .env ]; then
cat > .env <<ENV
AGENTNS_TLD=agents.dataworksai.com
AGENTNS_NAMESPACE=public
MONGODB_URI=${MONGODB_URI:-}
MONGODB_DB=ans_public
DANS_AUTH=off
ENV
echo "📝 Created .env"
fi

# Pull & start
docker compose -f docker-compose.dans.yml --env-file .env up -d --remove-orphans

sleep 6
curl -sf http://localhost:8200/health && echo "✅ DANS healthy"
echo "🎉 DANS is live at http://${SERVER}/dans/"
REMOTE

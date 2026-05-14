#!/usr/bin/env bash
# deploy.sh — First-time setup + manual deploy on 97.107.132.213
# Run this once from your laptop to bootstrap the server.
# After that, GitHub Actions handles deploys automatically.
set -euo pipefail

SERVER="97.107.132.213"
DEPLOY_USER="${DEPLOY_USER:-root}"
DEPLOY_DIR="/opt/agent-registry"

echo "🚀 Deploying DataWorksAI Agent Registry to ${SERVER}"

# ── 1. Upload compose file ─────────────────────────────────────────────────────
scp docker-compose.saas.yml "${DEPLOY_USER}@${SERVER}:${DEPLOY_DIR}/docker-compose.saas.yml"

# ── 2. Remote setup ────────────────────────────────────────────────────────────
ssh "${DEPLOY_USER}@${SERVER}" bash <<'REMOTE'
set -euo pipefail

# Install Docker if missing
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | bash
fi

# Install Docker Compose plugin if missing
if ! docker compose version &>/dev/null 2>&1; then
  apt-get install -y docker-compose-plugin
fi

mkdir -p /opt/agent-registry
cd /opt/agent-registry

# Generate secrets if not already set in environment
export CONTROL_PLANE_SECRET="${CONTROL_PLANE_SECRET:-$(openssl rand -hex 32)}"
export SECRET_KEY="${SECRET_KEY:-$(openssl rand -hex 32)}"
export MONGODB_URI="${MONGODB_URI:-mongodb://localhost:27017/}"

# Write .env if not present
if [ ! -f .env ]; then
cat > .env <<ENV
SAAS_MODE=1
ENABLE_FEDERATION=true
MONGODB_URI=${MONGODB_URI}
MONGODB_DB=agent_registry
CONTROL_PLANE_SECRET=${CONTROL_PLANE_SECRET}
SECRET_KEY=${SECRET_KEY}
AGENTNS_TLD=agentns.io
ENV
echo "📝 Created .env (keep CONTROL_PLANE_SECRET safe!)"
echo "   CONTROL_PLANE_SECRET=${CONTROL_PLANE_SECRET}"
fi

# Pull & start
docker compose -f docker-compose.saas.yml --env-file .env pull
docker compose -f docker-compose.saas.yml --env-file .env up -d --remove-orphans

sleep 8
curl -sf http://localhost:6900/health && echo "✅ Registry OK"
curl -sf http://localhost:8080/health && echo "✅ Control plane OK"
curl -sf http://localhost:8200/health && echo "✅ agentns OK"
echo "🎉 All services healthy"
REMOTE

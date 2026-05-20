# DataWorksAI Agent Registry

**Hosted semantic agent discovery for multi-agent systems.**

Live service: `http://97.107.132.213:6900` · Dashboard: `http://97.107.132.213:8080`

---

## What's in this repo

| Directory | What it does | Port |
|-----------|-------------|------|
| `registry/` | Flask semantic registry — agent search, SaaS multi-tenancy, switchboard federation | 6900 |
| `agentns/` | FastAPI DNS-like sidecar — URN routing, health checking, geo-selection | 8200 |
| `control_plane/` | Signup portal + live agent dashboard | 8080 |
| `tests/` | 40+ pytest tests for agentns sidecar |
| `.github/workflows/` | GitHub Actions CI/CD → auto-deploy to production on push to `main` |

---

## Quick start (use the hosted service)

```bash
# 1. Get a free API key at http://97.107.132.213:8080

# 2. Register your agent
curl -X POST http://97.107.132.213:6900/register \
  -H "X-API-Key: ak_live_..." \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id":    "my-agent",
    "agent_url":   "http://myhost:9000",
    "capabilities":["chat","summarisation"],
    "description": "General purpose assistant",
    "tags":        ["production"]
  }'

# 3. Semantic search
curl -X POST http://97.107.132.213:6900/search/semantic \
  -H "X-API-Key: ak_live_..." \
  -H "Content-Type: application/json" \
  -d '{"query": "find agents that can summarise documents", "max_results": 5}'

# 4. Lookup by ID
curl http://97.107.132.213:6900/agents/my-agent \
  -H "X-API-Key: ak_live_..."
```

---

## SDK (Python)

```python
import agentns

# ── Register your agent at startup ─────────────────────────────────────────────
client = agentns.target_lib.connect()          # reads AGENTNS_URL env var
await client.record(agentns.DeploymentSpec(
    leaf_name  = "my-agent",
    a2a_url    = "http://myhost:9000",
    region     = "us-east",
    protocols  = ["A2A"],
))

# ── Resolve another agent ───────────────────────────────────────────────────────
resolver = agentns.requester_lib.connect()     # reads AGENTNS_URL env var
endpoint  = await resolver.resolve(agentns.Query.from_label("alerts"))
if endpoint:
    print(endpoint.url)      # → http://host:port
    print(endpoint.ttl)      # → 60
```

Install: `pip install agentns` (or `pip install -e ./agentns` from this repo)

---

## Registry API

All endpoints (except `/health` and `/stats`) require `X-API-Key` when `SAAS_MODE=1`.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/register` | Register an agent |
| `GET`  | `/agents/<id>` | Get agent by ID |
| `DELETE` | `/agents/<id>` | Remove agent |
| `GET`  | `/lookup/<id>` | Lookup agent or client |
| `GET`  | `/list` | List all agents |
| `GET`  | `/search?q=&capabilities=&tags=` | Simple search |
| `POST` | `/search/semantic` | Semantic keyword search |
| `PUT`  | `/agents/<id>/status` | Update agent metadata |
| `POST` | `/resolve` | DANS TLD resolve |
| `GET`  | `/health` | Service health |
| `GET`  | `/stats` | Registry statistics |
| `POST` | `/switchboard/registries` | Link a remote registry |
| `GET`  | `/switchboard/registries` | List connected registries |
| `GET`  | `/switchboard/lookup/<id>` | Cross-registry lookup |

### Register payload

```json
{
  "agent_id":    "alerts",
  "agent_url":   "http://host:9001",
  "api_url":     "http://host:9001/api",
  "health_url":  "http://host:9001/health",
  "description": "Sends alerts for transit delays",
  "capabilities":["alerting","transit"],
  "tags":        ["production","mbta"],
  "agent_name":  "urn:agents.dataworksai.io:mbta:alerts"
}
```

---

## Switchboard federation

Connect your private registry so cross-registry lookups work transparently:

```bash
# Link a private registry
curl -X POST http://97.107.132.213:6900/switchboard/registries \
  -H "X-API-Key: ak_live_..." \
  -H "Content-Type: application/json" \
  -d '{"registry_id": "my-private", "url": "http://my-internal:6900"}'

# Now lookup will search both registries
curl http://97.107.132.213:6900/switchboard/lookup/my-private-agent
```

---

## Self-hosting

### Local dev (no auth, MongoDB optional)

```bash
docker compose up
# Registry → :6900, agentns → :8200, MongoDB → :27017
```

### Production (SaaS mode, all 3 services)

```bash
cp .env.example .env
# Edit .env: set MONGODB_URI, CONTROL_PLANE_SECRET, SECRET_KEY

docker compose -f docker-compose.saas.yml up -d
# Registry → :6900, Control plane → :8080, agentns → :8200
```

---

## CI/CD (GitHub Actions)

Every push to `main` automatically:
1. Builds 3 Docker images → pushes to `ghcr.io/dataworksai-com/`
2. SSHes to `97.107.132.213` and runs `docker compose pull && up -d`

**Required GitHub secrets:**

| Secret | Value |
|--------|-------|
| `DEPLOY_SSH_KEY` | Private SSH key for `root@97.107.132.213` |

**Required GitHub variables** (already set):

| Variable | Value |
|----------|-------|
| `DEPLOY_HOST` | `97.107.132.213` |
| `DEPLOY_USER` | `root` |

Add `DEPLOY_SSH_KEY`: Settings → Secrets → Actions → New repository secret.

---

## Architecture

```
Customer agent
     │
     ▼  X-API-Key
┌─────────────────────────────────────────────────┐
│         registry/ (Flask, port 6900)            │
│                                                  │
│  /register   /search/semantic   /lookup/<id>    │
│  /resolve    /agents/<id>       /stats          │
│  /switchboard/…  (federation)                   │
│                                                  │
│  SAAS_MODE=1 → tenants MongoDB collection        │
│               tenant_id scoped registry dict    │
└──────────────────┬──────────────────────────────┘
                   │ (optional: routing layer)
     ┌─────────────▼───────────────┐
     │  agentns/ (FastAPI, :8200)  │
     │  URN resolve, health sweep  │
     │  geo-selection, caching     │
     └─────────────────────────────┘
                   │
     ┌─────────────▼───────────────┐
     │  MongoDB (agents, tenants)  │
     └─────────────────────────────┘

     ┌─────────────────────────────┐
     │  control_plane/ (:8080)     │
     │  Signup → API key issued    │
     │  Dashboard → live agent UI  │
     └─────────────────────────────┘
```

---

## License

MIT

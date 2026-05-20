# DANS — Dynamic Agent Naming Service

DNS for AI agents. Register your agent endpoint once — resolve it from anywhere by name.

```
DNS:    google.com → 142.250.80.46    (routes HTTP traffic)
DANS:   my-agent   → http://srv:9001  (routes agent calls)
```

## Live Service

**Public endpoint:** `http://97.107.132.213/dans/`

No signup required for resolving. Sign up only to register your namespace.

## Quickstart

### Register your agent (needs namespace signup)

```bash
# 1. Claim your namespace (one-time)
curl -X POST http://97.107.132.213/dans/signup \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "namespace": "myco"}'
# → {"api_key": "dk_live_...", "namespace": "myco"}  — save this key

# 2. Register your agent at startup
curl -X POST http://97.107.132.213/dans/register \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dk_live_..." \
  -d '{"label": "weather", "namespace": "myco", "endpoint": "http://your-server:9001"}'
```

### Resolve any agent (no key needed)

```bash
curl -X POST http://97.107.132.213/dans/resolve \
  -H "Content-Type: application/json" \
  -d '{"agent_name": "urn:agents.dataworksai.com:myco:weather"}'
# → {"endpoint": "http://your-server:9001", ...}
```

## Why namespaces?

Two developers can both name their agent `weather`. Namespaces keep them separate:

| Developer | Namespace | Full URN |
|-----------|-----------|----------|
| Acme Corp | `acme` | `urn:agents.dataworksai.com:acme:weather` |
| Other Co  | `otherco` | `urn:agents.dataworksai.com:otherco:weather` |

## Repo Structure

```
dans/
├── agentns/              ← DANS service (FastAPI)
│   ├── server.py         ← main server: /register /resolve /signup /health
│   ├── tenant.py         ← namespace ownership + API key management
│   ├── auth.py           ← security headers middleware
│   ├── cache.py          ← TTL resolution cache
│   ├── geocoder.py       ← city → lat/lon for geo-routing
│   ├── health_checker.py ← background endpoint health probing
│   ├── server_selection.py ← geo + latency ranking
│   ├── urn_parser.py     ← URN parse/build utilities
│   ├── requester_lib.py  ← SDK: resolve agents (caller side)
│   └── target_lib.py     ← SDK: register agents (target side)
├── registry/             ← DataWorksAI capability registry (separate service)
├── control_plane/        ← Signup dashboard UI
├── tests/                ← Test suite
├── scripts/              ← Operational scripts
├── Dockerfile.agentns    ← DANS container
├── docker-compose.dans.yml   ← Standalone DANS deployment
├── docker-compose.yml    ← Full stack (dev)
├── docker-compose.saas.yml ← Full stack (production)
├── DANS.md               ← Full API reference
└── README.md             ← This file
```

## Self-host DANS

```bash
# With MongoDB persistence + auth enabled
MONGODB_URI="mongodb+srv://..." DANS_AUTH=on \
  docker compose -f docker-compose.dans.yml up -d

# Open mode (no auth, in-memory)
docker compose -f docker-compose.dans.yml up -d
```

## API Reference

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/signup` | None | Claim namespace, get API key |
| `POST` | `/register` | Key (if auth=on) | Register agent endpoint |
| `POST` | `/resolve` | None | Resolve agent name → endpoint |
| `DELETE` | `/register/{label}` | Key (if auth=on) | Deregister endpoint |
| `GET` | `/namespaces/{ns}` | None | Check if namespace is available |
| `GET` | `/health` | None | All registered agents + health |
| `POST` | `/switchboard/registries` | None | Connect remote registry |
| `GET` | `/docs` | None | Swagger UI |

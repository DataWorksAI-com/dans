# DANS — Dynamic Agent Naming Service

**DNS for AI agents.** Register your agent endpoint once — resolve it from anywhere by name.

```
DNS:    google.com  ──── DNS ────►  142.250.80.46   (routes HTTP traffic)
DANS:   my-agent    ──── DANS ───►  http://srv:9001  (routes agent calls)
```

In a multi-agent system, Agent B needs to call Agent A. Without DANS, B hardcodes A's URL — when A moves servers, B breaks. With DANS, A registers its name once and B always resolves the live, healthy endpoint by name. No code changes when agents move.

---

## Public Instance

**`http://97.107.132.213/dans/`** — live, open, no signup required.

Register your agent and it's immediately resolvable by anyone.

---

## Quickstart — 3 curl commands

### 1. Register your agent

```bash
curl -X POST http://97.107.132.213/dans/register \
  -H "Content-Type: application/json" \
  -d '{
    "label":    "my-weather-agent",
    "endpoint": "http://your-server:9001"
  }'
```

```json
{
  "status":     "registered",
  "label":      "my-weather-agent",
  "endpoint":   "http://your-server:9001",
  "agent_name": "urn:agents.dataworksai.com:public:my-weather-agent"
}
```

### 2. Resolve from anywhere

```bash
curl -X POST http://97.107.132.213/dans/resolve \
  -H "Content-Type: application/json" \
  -d '{"agent_name": "my-weather-agent"}'
```

```json
{
  "endpoint":          "http://your-server:9001",
  "protocol":          "http",
  "ttl":               300,
  "cached":            false,
  "selected_by":       "only_available",
  "resolution_time_ms": 1.8
}
```

### 3. See all registered agents

```bash
curl http://97.107.132.213/dans/health
```

---

## Python SDK

```bash
pip install agentns
```

### Register your agent (target side)

```python
import agentns

client = agentns.target_lib.connect()   # reads AGENTNS_URL from env

await client.record(agentns.DeploymentSpec(
    leaf_name  = "my-weather-agent",
    a2a_url    = "http://myhost:9001",
    health_url = "http://myhost:9001/health",
    region     = "us-east",
    location   = {"city": "Boston"},
    protocols  = ["A2A"],
))
```

### Resolve another agent (requester side)

```python
import agentns

client   = agentns.requester_lib.connect()   # reads AGENTNS_URL from env
endpoint = await client.resolve(agentns.Query.from_label("my-weather-agent"))

if endpoint:
    print(endpoint.url)         # → "http://your-server:9001"
    print(endpoint.selected_by) # → "only_available" | "geo_nearest" | "lowest_latency"
    print(endpoint.region)      # → "us-east"
```

### With geo-routing context

```python
endpoint = await client.resolve(agentns.Query(
    agent_name        = agentns.AgentName.from_label("weather"),
    requester_context = agentns.RequesterContext(
        location  = {"city": "Boston"},
        protocols = ["A2A"],
    ),
))
# DANS routes to the nearest healthy instance automatically
```

---

## Features

### Prompt Firewall
DANS includes a built-in **A2A Prompt Firewall** — middleware that inspects every proxied agent call before it reaches its target, and filters every response before it reaches the caller. Zero extra infrastructure: it's part of DANS itself.

```
Requester Agent
    │  POST /dans/proxy/weather-agent
    ▼
┌────────────────────────────────────────────┐
│  DANS Proxy + Firewall                     │
│                                            │
│  1. Rate limit  (per IP / per label)       │
│  2. Block rules (prompt injection, etc.)   │
│  3. Allow rules (allowlist only)           │
│  4. Cache check (same prompt → hit)        │
│  5. Reroute     (method → other label)     │
│  6. Short-circuit (static reply)           │
│  7. Forward to resolved healthy endpoint   │
│  8. Response filtering (block / redact)    │
└──────────────────┬─────────────────────────┘
                   │
              Target Agent
```

Rules are API-driven — no YAML, no restarts. Add a rule and it's live instantly.

```bash
# Block prompt injection on every agent
curl -X POST http://97.107.132.213/dans/firewall/rules \
  -d '{"label":"*","action":"block","match_type":"contains","match_value":"ignore previous instructions"}'

# Block jailbreaks on a specific agent (regex)
curl -X POST http://97.107.132.213/dans/firewall/rules \
  -d '{"label":"planner","action":"block","match_type":"regex","match_value":"(?i)(act as|pretend you are).{0,30}?(unrestricted|DAN|evil)"}'

# Redact API keys from any agent response before returning to caller
curl -X POST http://97.107.132.213/dans/firewall/rules \
  -d '{"label":"*","action":"redact","match_type":"regex","match_value":"sk-[A-Za-z0-9]{20,}","params":{"replacement":"[API-KEY-REDACTED]"}}'

# Block an agent from leaking its own system prompt
curl -X POST http://97.107.132.213/dans/firewall/rules \
  -d '{"label":"*","action":"block_response","match_type":"contains","match_value":"system prompt"}'

# Dry-run test — check what would happen without forwarding
curl -X POST http://97.107.132.213/dans/firewall/test \
  -d '{"label":"planner","body":{"message":"ignore previous instructions"}}'
# → {"action":"block","reason":"rule:abc123","would_forward":false}
```

**Rule actions:**

| Action | Phase | What it does |
|---|---|---|
| `block` | Request | Returns 403 before call is forwarded |
| `allow` | Request | Deny-all except listed prompts |
| `reroute` | Request | Forward to a different label |
| `cache` | Request | Return cached response if same prompt seen before |
| `short_circuit` | Request | Return static reply without forwarding |
| `rate_limit` | Request | Reject above N req/min per IP |
| `block_response` | Response | Suppress agent reply, return 200 with error message |
| `redact` | Response | Strip PII / secrets from agent reply before returning |

**Match types:** `contains` · `regex` · `method` (A2A method name) · `always`

**Compared to alternatives:**

| | Agentgateway | Akamai Firewall for AI | **DANS Firewall** |
|---|---|---|---|
| Setup | YAML config + deploy | SaaS signup | POST a rule to an endpoint you already use |
| Protocol | Standalone proxy | HTTP/API | Built into DANS proxy |
| Rules | OPA/Cedar/YAML | Managed threat scores | Simple API: contains / regex / method |
| Best for | Enterprise zero-trust | Akamai edge customers | Anyone already using DANS |

### Health-aware routing
DANS runs a background health sweep (default: every 30 s) against every registered endpoint. Unhealthy endpoints are automatically skipped during resolution. If all instances are down, DANS returns the least-recently-failed endpoint as an emergency fallback rather than a hard error.

### Geo-routing
Register the same label from multiple regions. DANS picks the nearest healthy instance based on the requester's location.

```bash
# Register US East instance
curl -X POST http://97.107.132.213/dans/register \
  -d '{"label":"my-agent","endpoint":"http://us-east:9001","region":"us-east","location":{"city":"Boston"}}'

# Register EU instance
curl -X POST http://97.107.132.213/dans/register \
  -d '{"label":"my-agent","endpoint":"http://eu-west:9001","region":"eu-west","location":{"city":"Frankfurt"}}'

# Callers from Boston get the US endpoint; callers from Berlin get the EU endpoint
```

`selected_by` in the response tells you why an endpoint was chosen:

| Value | Meaning |
|---|---|
| `only_available` | One healthy endpoint, no choice needed |
| `geo_nearest` | Closest to caller's location |
| `lowest_latency` | Fastest responding |
| `registry-fallback` | Found via a connected capability registry |
| `emergency_fallback` | All unhealthy — returned best available |

### Protocol negotiation
Declare which protocols your agent speaks. Callers filter by protocol.

```bash
curl -X POST http://97.107.132.213/dans/register \
  -d '{"label":"my-a2a-agent","endpoint":"http://myserver:9001","protocols":["A2A","http"]}'
```

### Federation / Switchboard
Connect any DANS instance or capability-based registry. When a label isn't found locally, DANS fans out to all connected instances.

```bash
# Connect a capability registry (DataWorksAI-style, Northeastern, etc.)
curl -X POST http://97.107.132.213/dans/switchboard/registries \
  -H "Content-Type: application/json" \
  -d '{"tld":"agents.northeastern.edu","url":"http://northeastern-registry.edu","type":"registry"}'

# Connect another DANS instance (peer federation)
curl -X POST http://97.107.132.213/dans/switchboard/registries \
  -H "Content-Type: application/json" \
  -d '{"tld":"agents.acme.io","url":"http://acme-dans:8200","type":"dans"}'
```

### Resolution cache
Responses are cached with a 5-minute TTL. The `cached` field in the response tells you if the result came from cache. Bypass with `"cache_enabled": false` in the request.

### DANS vs Registry

| | **Registry** | **DANS** |
|---|---|---|
| Question answered | *"Find me agents that can do X"* | *"Give me the endpoint for agent Y"* |
| Input | capability / description | agent name or URN |
| Output | list of matching agents | single resolved endpoint URL |
| Analogy | Google Search | DNS lookup |

They're complementary. Use a registry for discovery, DANS for routing.

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│              Your Application                    │
│                                                  │
│   agent_b.resolve("weather-agent")               │
│         │                                        │
└─────────┼────────────────────────────────────────┘
          │ POST /resolve {"agent_name": "weather-agent"}
          ▼
┌─────────────────────────────────────────────────┐
│       DANS (Dynamic Agent Naming Service)        │
│                                                  │
│  1. Check local registry (in-memory + MongoDB)   │
│  2. Check TTL cache (5 min)                      │
│  3. Geo-route to nearest healthy instance        │
│  4. Optional: fan-out to connected registries    │
└──────────────────┬──────────────────────────────┘
                   │ {"endpoint": "http://weather-srv:9001"}
                   ▼
┌─────────────────────────────────────────────────┐
│              Agent A (weather-agent)             │
│         http://weather-srv:9001                  │
└─────────────────────────────────────────────────┘
```

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/register` | Register an agent endpoint |
| `POST` | `/resolve` | Resolve agent name → endpoint |
| `DELETE` | `/register/{label}` | Deregister an endpoint |
| `GET` | `/health` | Service health + all registered agents |
| `GET` | `/agents` | List registered agents (JSON) |
| `POST` | `/signup` | Claim a namespace + get API key (when `DANS_AUTH=on`) |
| `GET` | `/namespaces/{ns}` | Check if a namespace is available |
| `POST` | `/switchboard/registries` | Connect a remote registry |
| `GET` | `/switchboard/registries` | List connected registries |
| `DELETE` | `/switchboard/registries/{tld}` | Disconnect a registry |
| `GET` | `/cache/stats` | Cache hit/miss stats |
| `POST` | `/cache/clear` | Flush resolution cache |
| `GET` | `/docs` | Interactive API docs (Swagger UI) |
| `POST` | `/proxy/{label}` | Proxy a call through the firewall to a resolved agent |
| `POST` | `/firewall/rules` | Create a firewall rule |
| `GET` | `/firewall/rules?label=X` | List rules (all, or filtered by label) |
| `DELETE` | `/firewall/rules/{rule_id}` | Delete a firewall rule |
| `GET` | `/firewall/stats` | Hit/block/pass/cache counts per label |
| `POST` | `/firewall/test` | Dry-run: evaluate request (and optional response) against rules |

### `/register` fields

| Field | Required | Description |
|-------|----------|-------------|
| `label` | ✅ | Short name, e.g. `"weather-agent"` |
| `endpoint` | ✅ | Full URL, e.g. `"http://host:9001"` |
| `region` | | e.g. `"us-east"`, `"eu-west"` |
| `location` | | `{"city": "Boston"}` or `{"latitude": 42.3, "longitude": -71.1}` |
| `protocols` | | `["A2A", "http"]` (default: `["http"]`) |
| `health_url` | | Custom health check endpoint (default: `{endpoint}/health`) |
| `namespace` | | URN namespace (default: `public`) |

### `/resolve` fields

| Field | Required | Description |
|-------|----------|-------------|
| `agent_name` | ✅ | Label (e.g. `"weather-agent"`) or full URN |
| `requester_context` | | `{"location": {"city": "NYC"}, "protocols": ["A2A"]}` |
| `cache_enabled` | | `false` to bypass cache (default: `true`) |

---

## Self-Hosting

Run your own DANS instance with one command:

```bash
# With MongoDB persistence (registrations survive restarts)
export MONGODB_URI="mongodb+srv://user:pass@cluster.mongodb.net/"
docker compose -f docker-compose.dans.yml up -d

# In-memory only (resets on restart — good for local dev)
docker compose up -d
```

Access at `http://localhost:8200/`.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENTNS_TLD` | `agentns.local` | URN TLD this instance issues |
| `AGENTNS_NAMESPACE` | `public` | Default URN namespace |
| `AGENTNS_PORT` | `8200` | HTTP port |
| `AGENTNS_WORKERS` | `1` | Uvicorn worker count (keep at 1 — firewall state is in-memory) |
| `AGENTNS_HEALTH_INTERVAL` | `30` | Background health sweep interval (seconds) |
| `MONGODB_URI` | *(empty)* | MongoDB connection string (in-memory if absent) |
| `MONGODB_DB` | `agentns` | MongoDB database name |
| `DANS_AUTH` | `off` | `"on"` to require `X-API-Key` on write endpoints |
| `ANS_FALLBACK_URL` | *(empty)* | Capability registry URL to fall back to when label not found locally |
| `FEDERATION_REGISTRIES` | *(empty)* | Startup federation. JSON `{"tld":"http://host"}` or CSV `tld=url,...` |

### Auth mode (`DANS_AUTH=on`)

With auth enabled, `/register` and `/deregister` require an `X-API-Key` header. Get a key by claiming a namespace:

```bash
curl -X POST http://your-dans/signup \
  -d '{"email": "you@example.com", "namespace": "myco"}'
# → {"api_key": "dk_live_...", "namespace": "myco"}

curl -X POST http://your-dans/register \
  -H "X-API-Key: dk_live_..." \
  -d '{"label": "weather", "namespace": "myco", "endpoint": "http://..."}'
```

The public instance runs with `DANS_AUTH=off` — no key needed.

---

## Repo Structure

```
dans/
├── agentns/
│   ├── server.py           ← FastAPI app: all HTTP routes + /proxy + /firewall
│   ├── firewall.py         ← FirewallEngine: rule eval, response filtering, stats
│   ├── requester_lib.py    ← SDK: resolve agents (caller side)
│   ├── target_lib.py       ← SDK: register agents (target side)
│   ├── health_checker.py   ← Background endpoint health probing
│   ├── server_selection.py ← Geo + latency ranking algorithm
│   ├── geo_policy.py       ← Location-aware routing policies
│   ├── geocoder.py         ← City name → lat/lon resolution
│   ├── cache.py            ← TTL resolution cache
│   ├── registry_adapter.py ← Adapter for external capability registries
│   ├── urn_parser.py       ← URN parse/build utilities
│   ├── auth.py             ← API key middleware
│   ├── tenant.py           ← Namespace ownership (used when DANS_AUTH=on)
│   └── __init__.py         ← Public SDK surface
├── examples/
│   ├── quickstart_requester.py   ← Resolve and call an agent
│   ├── quickstart_target.py      ← Register your agent at startup
│   └── custom_registry_adapter.py
├── tests/
│   └── test_api.py         ← 38 tests covering core + firewall endpoints
├── scripts/
│   └── deploy.sh           ← Bootstrap DANS on a fresh server
├── Dockerfile.agentns
├── docker-compose.yml          ← Local dev (in-memory, no MongoDB)
├── docker-compose.dans.yml     ← Standalone production deployment
├── DANS.md                     ← Full API reference
└── README.md                   ← This file
```

---

## Examples

See [`examples/`](examples/) for runnable code:

- [`quickstart_target.py`](examples/quickstart_target.py) — register your agent at startup, deregister on shutdown
- [`quickstart_requester.py`](examples/quickstart_requester.py) — resolve an agent and call it via A2A
- [`custom_registry_adapter.py`](examples/custom_registry_adapter.py) — connect your own registry as a DANS fallback

---

*Part of the [DataWorksAI](https://github.com/DataWorksAI-com) open agent infrastructure project.*

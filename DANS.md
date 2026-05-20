# DANS — Dynamic Agent Naming Service

**DNS for AI agents.** Register your agent endpoint once — resolve it from anywhere by name.

```
Akamai: google.com  ──── DNS ────►  142.250.80.46   (routes HTTP traffic)
DANS:   my-agent    ──── DANS ───►  http://srv:9001  (routes agent calls)
```

---

## Why DANS?

In a multi-agent system, Agent B needs to call Agent A. Without DANS:
- B hardcodes A's IP address. A moves servers → B breaks.
- B queries a capability registry. Gets 5 matching agents, has to pick one, no health info.

With DANS:
- A registers once: `"my-agent" → http://server:9001`
- B resolves once: `"my-agent"` → gets the live, healthy endpoint
- A moves servers → just re-registers. B's code never changes.

### DANS vs Registry

| | **Registry** | **DANS** |
|---|---|---|
| Question answered | *"Find me agents that can do X"* | *"Give me the endpoint for agent Y"* |
| Input | capability / description | agent name (label or URN) |
| Output | list of matching agents | single resolved endpoint URL |
| Analogy | Google Search | DNS lookup |

They're complementary, not competing. Use the registry for discovery, DANS for routing.

---

## Public Service

Live at: **`http://97.107.132.213/dans/`**

No signup required. No API key. Open to anyone.

---

## Quickstart (3 commands)

### 1. Register your agent

```bash
curl -X POST http://97.107.132.213/dans/register \
  -H "Content-Type: application/json" \
  -d '{
    "label": "my-weather-agent",
    "endpoint": "http://your-server:9001"
  }'
```

Response:
```json
{
  "status": "registered",
  "label": "my-weather-agent",
  "endpoint": "http://your-server:9001",
  "agent_name": "urn:agents.dataworksai.com:public:my-weather-agent"
}
```

### 2. Resolve from anywhere

```bash
# By label
curl -X POST http://97.107.132.213/dans/resolve \
  -H "Content-Type: application/json" \
  -d '{"agent_name": "my-weather-agent"}'

# By full URN
curl -X POST http://97.107.132.213/dans/resolve \
  -H "Content-Type: application/json" \
  -d '{"agent_name": "urn:agents.dataworksai.com:public:my-weather-agent"}'
```

Response:
```json
{
  "endpoint": "http://your-server:9001",
  "protocol": "http",
  "ttl": 300,
  "cached": false,
  "selected_by": "only_available",
  "resolution_time_ms": 1.8
}
```

### 3. See all registered agents

```bash
curl http://97.107.132.213/dans/health
```

---

## Advanced Registration

### Multiple instances with geo-routing

```bash
# Register US East instance
curl -X POST http://97.107.132.213/dans/register \
  -d '{
    "label": "my-agent",
    "endpoint": "http://us-east-server:9001",
    "region": "us-east",
    "location": {"city": "Boston"}
  }'

# Register EU instance
curl -X POST http://97.107.132.213/dans/register \
  -d '{
    "label": "my-agent",
    "endpoint": "http://eu-server:9001",
    "region": "eu-west",
    "location": {"city": "London"}
  }'

# DANS automatically routes callers to nearest healthy instance
```

### Protocol declaration

```bash
curl -X POST http://97.107.132.213/dans/register \
  -d '{
    "label": "my-a2a-agent",
    "endpoint": "http://my-server:9001",
    "protocols": ["A2A", "http"]
  }'
```

### Connect your private registry (federation)

If you run your own registry, you can connect it to the public DANS so agents registered there are also resolvable:

```bash
curl -X POST http://97.107.132.213/dans/switchboard/registries \
  -H "Content-Type: application/json" \
  -d '{
    "tld": "mycompany.agents.io",
    "url": "http://my-registry:8200"
  }'
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
| `POST` | `/switchboard/registries` | Connect a remote registry |
| `GET` | `/switchboard/registries` | List connected registries |
| `GET` | `/docs` | Interactive API docs (Swagger UI) |

### `/register` fields

| Field | Required | Description |
|-------|----------|-------------|
| `label` | ✅ | Short name, e.g. `"weather-agent"` |
| `endpoint` | ✅ | Full URL, e.g. `"http://host:9001"` |
| `region` | | e.g. `"us-east"`, `"eu-west"` |
| `location` | | `{"city": "Boston"}` or `{"latitude": 42.3, "longitude": -71.1}` |
| `protocols` | | `["A2A", "http"]` (default: `["http"]`) |
| `health_check_url` | | Custom health check endpoint |
| `namespace` | | URN namespace override (default: `public`) |

### `/resolve` fields

| Field | Required | Description |
|-------|----------|-------------|
| `agent_name` | ✅ | Label or full URN |
| `requester_context` | | `{"location": {"city": "NYC"}, "protocols": ["A2A"]}` |
| `cache_enabled` | | `false` to bypass cache (default: `true`) |

---

## Self-Hosting

Run your own DANS instance with one command:

```bash
# With MongoDB persistence
export MONGODB_URI="mongodb+srv://user:pass@cluster.mongodb.net/"
docker compose -f docker-compose.dans.yml up -d

# In-memory only (resets on restart)
docker compose -f docker-compose.dans.yml up -d
```

Then access it at `http://localhost:8200/`.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENTNS_TLD` | `agents.dataworksai.com` | URN TLD this instance issues |
| `AGENTNS_NAMESPACE` | `public` | Default URN namespace |
| `MONGODB_URI` | *(empty)* | MongoDB connection string |
| `MONGODB_DB` | `ans_public` | MongoDB database name |
| `ANS_FALLBACK_URL` | *(empty)* | Registry URL to try as fallback |
| `FEDERATION_REGISTRIES` | *(empty)* | Comma-separated remote DANS instances |
| `AGENTNS_AUTH` | `off` | `"key"` to require X-API-Key |

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
│         http://97.107.132.213/dans/              │
│                                                  │
│  1. Check local store (registered via /register) │
│  2. Check cache (5-min TTL)                      │
│  3. Geo-route to nearest healthy instance        │
│  4. Optional: fallback to connected registry     │
└──────────────────┬──────────────────────────────┘
                   │ {"endpoint": "http://weather-srv:9001"}
                   ▼
┌─────────────────────────────────────────────────┐
│              Agent A (weather-agent)             │
│         http://weather-srv:9001                  │
└─────────────────────────────────────────────────┘
```

---

## Python SDK (agentns)

```python
# Register your agent (run once at startup)
from agentns import TargetAgent

agent = TargetAgent(
    label="my-weather-agent",
    endpoint="http://my-server:9001",
    ans_url="http://97.107.132.213/dans",
)
await agent.register()

# Resolve another agent (requester side)
from agentns import RequesterAgent

requester = RequesterAgent(ans_url="http://97.107.132.213/dans")
endpoint = await requester.resolve("my-weather-agent")
# → "http://weather-server:9001"
```

---

*Built by [DataWorksAI](https://github.com/dataworksai/agent-registry). Part of the open agent infrastructure project.*

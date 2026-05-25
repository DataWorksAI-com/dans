"""
agentns — DANS (Dynamic Agent Naming Service)
==============================================
DNS for AI agents. Register your agent endpoint once, resolve it from anywhere by name.

Quick start — requester agent (resolve other agents)
-----------------------------------------------------
    import agentns

    client = agentns.requester_lib.connect()
    endpoint = await client.resolve(agentns.Query.from_label("alerts"))
    if endpoint:
        # POST to endpoint.url using your preferred HTTP client
        ...

Quick start — target agent (register yourself)
----------------------------------------------
    import agentns

    client = agentns.target_lib.connect()
    await client.record(agentns.DeploymentSpec(
        leaf_name = "alerts",
        a2a_url   = "http://myhost:9001",
    ))

Start the server
----------------
    # Via CLI:
    python -m agentns

    # Via Python:
    import uvicorn
    from agentns.server import app
    uvicorn.run(app, host="0.0.0.0", port=8200)

    # Public instance (no setup needed — use your own or the hosted one):
    AGENTNS_URL=http://your-dans-host/dans

Environment variables
---------------------
    AGENTNS_PORT             Server port                   (default: 8200)
    AGENTNS_NAMESPACE        Default URN namespace         (default: "agents.local")
    AGENTNS_TLD              URN TLD                       (default: "agentns.local")
    AGENTNS_URL              Client → server URL           (default: http://localhost:8200)
    AGENTNS_RESOLVER_URL     Requester client resolver URL (defaults to AGENTNS_URL)
    AGENTNS_API_KEY          API key for authenticated resolvers
    DANS_AUTH                "off" (default) | "on" — require X-API-Key on writes
    ANS_FALLBACK_URL         Optional fallback registry URL
    MONGODB_URI              MongoDB connection string     (optional; in-memory if absent)
    MONGODB_DB               MongoDB database name         (default: ans_public)
"""

__version__ = "3.1.0"
__author__  = "DataWorksAI"  # Original authors — see LICENSE
__license__ = "MIT"

# ── Protocol vocabulary ────────────────────────────────────────────────────────
# Canonical set of protocol identifiers DANS understands.
# Agents register using these names; DANS negotiates the best match on resolve.
#   a2a   — Google A2A / JSON-RPC over HTTP (agent-to-agent)
#   mcp   — Anthropic Model Context Protocol (LLM-to-tool, stdio or HTTP)
#   slim  — Cisco AGNTCY secure messaging bus (identity-routed)
#   grpc  — gRPC binary protocol (strongly typed, many enterprise agents)
#   sse   — HTTP Server-Sent Events (streaming responses)
#   acp   — IBM BeeAgent ACP (REST-based agent communication)
#   http  — Plain HTTP REST (universal fallback)
SUPPORTED_PROTOCOLS: frozenset = frozenset({"a2a", "mcp", "slim", "grpc", "http", "sse", "acp"})

# ── Firewall ───────────────────────────────────────────────────────────────────
from agentns.firewall import FirewallRule

# ── Requester side ─────────────────────────────────────────────────────────────
from agentns.requester_lib import (
    connect as resolve_connect,
    AgentName,
    RequesterContext,
    Query,
    TailoredEndpoint,
    RequesterAgentClient,
)

# ── Target side ────────────────────────────────────────────────────────────────
from agentns.target_lib import (
    connect as record_connect,
    DeploymentSpec,
    TargetAgentClient,
)

# ── Sub-module aliases (agentns.requester_lib.connect() etc.) ─────────────────
from agentns import requester_lib, target_lib

__all__ = [
    # Protocol vocabulary
    "SUPPORTED_PROTOCOLS",
    # Firewall
    "FirewallRule",
    # Requester side
    "resolve_connect",
    "AgentName",
    "RequesterContext",
    "Query",
    "TailoredEndpoint",
    "RequesterAgentClient",
    "requester_lib",
    # Target side
    "record_connect",
    "DeploymentSpec",
    "TargetAgentClient",
    "target_lib",
]

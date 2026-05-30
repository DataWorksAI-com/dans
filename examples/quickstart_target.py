"""
quickstart_target.py
====================
Minimal example: register your agent with DANS at startup.

Prerequisites:
    pip install "git+https://github.com/DataWorksAI-com/dans.git"   # or: pip install agentns (once on PyPI)

Start DANS first:
    docker compose -f docker-compose.dans.yml up -d --build
    export AGENTNS_URL=http://localhost:8200

If DANS_AUTH=on, also set:
    export AGENTNS_API_KEY=dk_live_your_key_here
"""

import asyncio
import agentns

MY_LABEL    = "my-agent"
MY_ENDPOINT = "http://localhost:9000"


async def register():
    client = agentns.target_lib.connect()

    result = await client.record(agentns.DeploymentSpec(
        leaf_name  = MY_LABEL,
        a2a_url    = MY_ENDPOINT,
        health_url = f"{MY_ENDPOINT}/health",
        region     = "us-east",
        location   = {"city": "New York"},
        protocols  = ["A2A"],
    ))

    print(f"Registered: {result}")
    return client


async def deregister(client: agentns.TargetAgentClient):
    result = await client.deregister(MY_LABEL, MY_ENDPOINT)
    print(f"Deregistered: {result}")


async def main():
    client = await register()

    print(f"\n'{MY_LABEL}' is now discoverable at {MY_ENDPOINT}")
    print("Other agents can resolve it with:")
    print(f"  endpoint = await resolver_client.resolve(Query.from_label('{MY_LABEL}'))")
    print("\nPress Ctrl+C to deregister and exit.\n")

    try:
        await asyncio.sleep(3600)   # keep alive; replace with your real agent loop
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await deregister(client)


asyncio.run(main())

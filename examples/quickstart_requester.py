"""
quickstart_requester.py
=======================
Minimal example: resolve an agent via DANS and call it.

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


async def main():
    # Connect to DANS (reads AGENTNS_URL + AGENTNS_API_KEY from env)
    client = agentns.requester_lib.connect()

    # Resolve by label
    endpoint = await client.resolve(agentns.Query.from_label("my-agent"))

    if endpoint is None:
        print("Could not resolve 'my-agent' — is it registered?")
        return

    print(f"Resolved to: {endpoint.url}")
    print(f"  Protocol:   {endpoint.protocol}")
    print(f"  TTL:        {endpoint.ttl}s")
    print(f"  Region:     {endpoint.region or 'unknown'}")
    print(f"  Selected by:{endpoint.selected_by}")

    # Call the agent via A2A
    import httpx
    async with httpx.AsyncClient() as c:
        resp = await c.post(
            endpoint.url,
            json={
                "jsonrpc": "2.0",
                "method":  "message/send",
                "id":      "1",
                "params": {
                    "message": {
                        "messageId": "quickstart-1",
                        "role":      "user",
                        "parts":     [{"kind": "text", "text": "Hello, agent!"}],
                    }
                },
            },
            timeout=30.0,
        )
        print(f"\nAgent response ({resp.status_code}):")
        print(resp.text[:500])


asyncio.run(main())

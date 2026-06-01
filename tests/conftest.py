"""
pytest configuration for agentns tests.
"""
import os

# Disable rate limiting for the test suite *before* importing the server — the
# tests fire many requests in quick succession, which would otherwise trip
# slowapi's limiter (429s) whenever slowapi is installed (e.g. in CI).
os.environ.setdefault("AGENTNS_RATE_LIMIT", "off")

import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from agentns.server import app, _registry, _health_cache, _cache


@pytest_asyncio.fixture(autouse=True)
async def clear_state():
    """Reset global state between tests."""
    _registry.clear()
    _health_cache.clear()
    await _cache.clear()
    yield
    _registry.clear()
    _health_cache.clear()
    await _cache.clear()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

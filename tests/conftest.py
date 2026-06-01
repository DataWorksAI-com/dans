"""
pytest configuration for agentns tests.
"""
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

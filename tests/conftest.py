"""
pytest configuration for agentns tests.
"""
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from agentns.server import app, _registry, _health_cache, _cache, _firewall


@pytest_asyncio.fixture(autouse=True)
async def clear_state():
    """Reset global state between tests."""
    _registry.clear()
    _health_cache.clear()
    await _cache.clear()
    # Clear firewall rules and stats
    _firewall._rules.clear()
    _firewall._cache.clear()
    _firewall._stats.clear()
    _firewall._rate_windows.clear()
    yield
    _registry.clear()
    _health_cache.clear()
    await _cache.clear()
    _firewall._rules.clear()
    _firewall._cache.clear()
    _firewall._stats.clear()
    _firewall._rate_windows.clear()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

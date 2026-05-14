"""Tests for agentns.server_selection"""
import math
import pytest
from agentns.server_selection import rank_servers, select_protocol, calculate_ttl, _haversine


SERVERS = [
    {
        "server_id":    "nyc",
        "endpoint":     "http://nyc:9001",
        "protocols":    ["A2A", "http"],
        "region":       "us-east",
        "region_label": "New York, NY",
        "location":     {"latitude": 40.7128, "longitude": -74.0060},
    },
    {
        "server_id":    "lon",
        "endpoint":     "http://lon:9001",
        "protocols":    ["A2A", "http"],
        "region":       "eu-west",
        "region_label": "London, UK",
        "location":     {"latitude": 51.5074, "longitude": -0.1278},
    },
]

HEALTH = {
    "nyc": {"status": "healthy", "load": 30.0, "response_time_ms": 45.0},
    "lon": {"status": "healthy", "load": 20.0, "response_time_ms": 210.0},
}


def test_geo_routing_boston_prefers_nyc():
    ctx = {"location": {"city": "Boston"}, "protocols": ["A2A"]}
    ranked = rank_servers(SERVERS, HEALTH, ctx)
    assert ranked[0][0]["server_id"] == "nyc"


def test_geo_routing_paris_prefers_london():
    ctx = {"location": {"city": "Paris"}, "protocols": ["A2A"]}
    ranked = rank_servers(SERVERS, HEALTH, ctx)
    assert ranked[0][0]["server_id"] == "lon"


def test_latency_wins_when_no_location():
    ctx = {}
    ranked = rank_servers(SERVERS, HEALTH, ctx)
    # NYC has lower latency (45ms vs 210ms)
    assert ranked[0][0]["server_id"] == "nyc"


def test_unhealthy_excluded():
    health = {
        "nyc": {"status": "unhealthy", "load": 100.0, "response_time_ms": 0.0},
        "lon": {"status": "healthy",   "load": 20.0,  "response_time_ms": 210.0},
    }
    ranked = rank_servers(SERVERS, health, {})
    assert len(ranked) == 1
    assert ranked[0][0]["server_id"] == "lon"


def test_unhealthy_included_flag():
    health = {
        "nyc": {"status": "unhealthy", "load": 100.0, "response_time_ms": 0.0},
        "lon": {"status": "healthy",   "load": 20.0,  "response_time_ms": 210.0},
    }
    ranked = rank_servers(SERVERS, health, {}, include_unhealthy=True)
    assert len(ranked) == 2
    # healthy server should still be ranked first
    assert ranked[0][0]["server_id"] == "lon"


def test_select_protocol_preferred():
    assert select_protocol(["http", "A2A"], ["A2A"]) == "A2A"


def test_select_protocol_fallback():
    assert select_protocol(["http"], ["A2A"]) == "http"


def test_select_protocol_empty_preferred():
    assert select_protocol(["http", "A2A"], []) == "http"


def test_calculate_ttl():
    assert calculate_ttl({"status": "healthy"})  == 60
    assert calculate_ttl({"status": "degraded"}) == 15
    assert calculate_ttl({"status": "unknown"})  == 10
    assert calculate_ttl({"status": "unhealthy"}) == 5


def test_haversine_boston_nyc():
    # Boston to NYC is ~306 km
    dist = _haversine(42.3601, -71.0589, 40.7128, -74.0060)
    assert 290 < dist < 330


def test_haversine_same_point():
    assert _haversine(0, 0, 0, 0) == pytest.approx(0.0, abs=0.001)

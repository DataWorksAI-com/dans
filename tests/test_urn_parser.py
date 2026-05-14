"""Tests for agentns.urn_parser"""
import pytest
from agentns.urn_parser import parse_urn, build_urn, extract_label


def test_full_urn():
    p = parse_urn("urn:acme.com:sales:emailer")
    assert p.tld       == "acme.com"
    assert p.namespace == "sales"
    assert p.label     == "emailer"


def test_full_urn_mbta():
    p = parse_urn("urn:agents.dataworksai.com:mbta-transit-ci:alerts")
    assert p.tld       == "agents.dataworksai.com"
    assert p.namespace == "mbta-transit-ci"
    assert p.label     == "alerts"


def test_two_part_urn():
    p = parse_urn("urn:agentns.local:emailer")
    assert p.tld       == "agentns.local"
    assert p.namespace == ""
    assert p.label     == "emailer"


def test_plain_label():
    p = parse_urn("emailer")
    assert p.tld       == ""
    assert p.namespace == ""
    assert p.label     == "emailer"


def test_build_urn():
    assert build_urn("acme.com", "sales", "emailer") == "urn:acme.com:sales:emailer"


def test_extract_label():
    assert extract_label("urn:acme.com:sales:emailer") == "emailer"
    assert extract_label("emailer") == "emailer"


def test_full_property():
    p = parse_urn("urn:acme.com:sales:emailer")
    assert p.full == "urn:acme.com:sales:emailer"


def test_matches_namespace():
    p = parse_urn("urn:acme.com:sales:emailer")
    assert p.matches_namespace("acme.com", "sales")
    assert not p.matches_namespace("acme.com", "hr")

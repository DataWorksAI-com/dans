"""
agentns.urn_parser
==================
Parse and build Agent URNs in the format:

    urn:<tld>:<namespace>:<label>

Examples
--------
    urn:agents.dataworksai.com:mbta-transit-ci:alerts
    urn:acme.com:sales:emailer
    urn:my-project.io:payments:invoicer

Short URNs (no TLD) are also accepted:

    acme.sales:emailer          → tld=acme.sales, namespace="", label=emailer
    emailer                     → tld="", namespace="", label=emailer
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class ParsedURN:
    tld: str          # e.g. "agents.dataworksai.com"
    namespace: str    # e.g. "mbta-transit-ci"
    label: str        # e.g. "alerts"
    raw: str          # original string

    @property
    def full(self) -> str:
        parts = [p for p in ["urn", self.tld, self.namespace, self.label] if p]
        return ":".join(parts)

    def matches_namespace(self, tld: str, namespace: str) -> bool:
        return self.tld == tld and self.namespace == namespace


def parse_urn(value: str) -> ParsedURN:
    """
    Parse any of these forms:

        urn:tld:namespace:label     → (tld, namespace, label)
        urn:tld:label               → (tld, "",        label)
        namespace:label             → ("",  namespace, label)
        label                       → ("",  "",        label)

    Never raises — always returns a ParsedURN (missing parts become empty string).
    """
    raw = value.strip()
    s   = raw

    # strip leading "urn:" scheme
    if s.lower().startswith("urn:"):
        s = s[4:]

    parts = s.split(":")
    if len(parts) >= 3:
        tld, namespace, label = parts[0], parts[1], ":".join(parts[2:])
    elif len(parts) == 2:
        # Could be "tld:label" or "namespace:label" — treat first as tld
        tld, namespace, label = parts[0], "", parts[1]
    else:
        tld, namespace, label = "", "", parts[0]

    return ParsedURN(tld=tld, namespace=namespace, label=label, raw=raw)


def build_urn(tld: str, namespace: str, label: str) -> str:
    """Build a canonical URN string."""
    return f"urn:{tld}:{namespace}:{label}"


def extract_label(value: str) -> str:
    """Quick helper — returns just the label portion from any URN or plain string."""
    return parse_urn(value).label or value

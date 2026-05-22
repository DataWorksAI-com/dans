"""
agentns.firewall
================
Prompt Firewall — A2A Delegate middleware for DANS.

Intercepts every call through /proxy/{label} and applies configurable rules
before forwarding to the target agent.  
traffic: health-routing and geo-routing were already in DANS; this adds
security, caching, rate-limiting, and rerouting — all via a simple REST API
with no extra infrastructure required.

Rule actions
------------
    block          — deny the request (returns 403 to caller)
    allow          — explicit allowlist; anything not matched is blocked
    reroute        — forward to a different agent label
    cache          — return cached response for identical prompt (TTL from params)
    rate_limit     — max N calls per minute from the same IP
    short_circuit  — return a static response without forwarding at all

Match types
-----------
    contains   — match_value appears anywhere in the request body (case-insensitive)
    regex      — re.search(match_value, body_str, re.IGNORECASE)
    method     — A2A method field equals match_value (e.g. "message/send")
    always     — matches every request (useful for global cache/rate-limit rules)

Evaluation order (first match wins)
------------------------------------
    1. rate_limit  — fail fast before any inspection
    2. block       — deny matching prompts
    3. allow       — if any allow rules exist for this label, deny non-matching
    4. cache       — check response cache
    5. reroute     — swap destination label
    6. short_circuit — return static payload
    (no match)  →  pass

Label "*" applies a rule to ALL agents (evaluated before label-specific rules).
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agentns.firewall")

# ── Data structures ────────────────────────────────────────────────────────────

VALID_ACTIONS     = {"block", "allow", "reroute", "cache", "rate_limit", "short_circuit"}
VALID_MATCH_TYPES = {"contains", "regex", "method", "always"}
ACTION_ORDER      = ["rate_limit", "block", "allow", "cache", "reroute", "short_circuit"]


@dataclass
class FirewallRule:
    label:       str            # agent label or "*" for all
    action:      str            # one of VALID_ACTIONS
    match_type:  str            # one of VALID_MATCH_TYPES
    match_value: str            # string / regex / method name to match
    params:      Dict[str, Any] = field(default_factory=dict)
    priority:    int            = 100
    rule_id:     str            = field(default_factory=lambda: str(uuid.uuid4())[:8])
    created_at:  datetime       = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "rule_id":     self.rule_id,
            "label":       self.label,
            "action":      self.action,
            "match_type":  self.match_type,
            "match_value": self.match_value,
            "params":      self.params,
            "priority":    self.priority,
            "created_at":  self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FirewallRule":
        created = d.get("created_at")
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        elif not isinstance(created, datetime):
            created = datetime.now(timezone.utc)
        return cls(
            rule_id     = d.get("rule_id", str(uuid.uuid4())[:8]),
            label       = d["label"],
            action      = d["action"],
            match_type  = d["match_type"],
            match_value = d.get("match_value", ""),
            params      = d.get("params", {}),
            priority    = d.get("priority", 100),
            created_at  = created,
        )


@dataclass
class FirewallDecision:
    action:        str                  # "pass" | "block" | "reroute" | "cache_hit" | "short_circuit"
    reason:        str   = ""           # rule_id that triggered, or descriptive string
    payload:       Any   = None         # reroute→new label; cache_hit/short_circuit→response dict
    modified_body: Optional[bytes] = None  # mutated body (future: PII strip)


_PASS = FirewallDecision(action="pass")


# ── Rate-limit state ───────────────────────────────────────────────────────────

@dataclass
class _RateWindow:
    count:     int   = 0
    window_ts: float = field(default_factory=time.monotonic)


class FirewallEngine:
    """
    Stateful firewall engine.  One instance per DANS process.
    Thread-safe for asyncio (single-threaded event loop); no locks needed.
    """

    def __init__(self) -> None:
        # label → list[FirewallRule], kept sorted by priority
        self._rules: Dict[str, List[FirewallRule]] = defaultdict(list)
        # Response body cache: cache_key → (response_dict, expiry_ts)
        self._cache: Dict[str, tuple] = {}
        # Rate-limit windows: (label, ip) → _RateWindow
        self._rate_windows: Dict[tuple, _RateWindow] = {}
        # Stats counters: label → action → count
        self._stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"pass": 0, "block": 0, "reroute": 0, "cache_hit": 0, "short_circuit": 0, "rate_limited": 0}
        )
        self._mongo_col = None

    # ── Rule management ────────────────────────────────────────────────────────

    async def add_rule(self, rule: FirewallRule) -> FirewallRule:
        self._rules[rule.label].append(rule)
        self._rules[rule.label].sort(key=lambda r: r.priority)
        if self._mongo_col is not None:
            await self._save_rule(rule)
        logger.info(f"firewall: rule added — {rule.rule_id} label={rule.label!r} action={rule.action} match={rule.match_type}:{rule.match_value!r}")
        return rule

    async def remove_rule(self, rule_id: str) -> bool:
        removed = False
        for label_rules in self._rules.values():
            before = len(label_rules)
            label_rules[:] = [r for r in label_rules if r.rule_id != rule_id]
            if len(label_rules) < before:
                removed = True
        if removed and self._mongo_col is not None:
            try:
                await self._mongo_col.delete_one({"rule_id": rule_id})
            except Exception as exc:
                logger.error(f"firewall: MongoDB delete failed ({rule_id}): {exc}")
        return removed

    def list_rules(self, label: Optional[str] = None) -> List[FirewallRule]:
        if label:
            # return global rules + label-specific rules, sorted by priority
            combined = self._rules.get("*", []) + self._rules.get(label, [])
            return sorted(combined, key=lambda r: r.priority)
        all_rules = []
        for rules in self._rules.values():
            all_rules.extend(rules)
        return sorted(all_rules, key=lambda r: (r.label, r.priority))

    # ── Core evaluation ────────────────────────────────────────────────────────

    async def evaluate(
        self,
        label:         str,
        body_bytes:    bytes,
        a2a_method:    str,
        requester_ip:  str,
    ) -> FirewallDecision:
        """
        Evaluate all applicable rules for this request and return a decision.
        Rules for label "*" are merged with label-specific rules and sorted by priority.
        """
        # Gather rules: global ("*") + label-specific, sorted by priority
        rules: List[FirewallRule] = []
        for bucket in ("*", label):
            rules.extend(self._rules.get(bucket, []))
        if not rules:
            return _PASS

        rules.sort(key=lambda r: (r.priority, r.action))

        body_str = body_bytes.decode("utf-8", errors="replace") if body_bytes else ""

        # Evaluate in action-type order within the sorted list
        for action_type in ACTION_ORDER:
            action_rules = [r for r in rules if r.action == action_type]
            for rule in action_rules:
                if not self._matches(rule, body_str, a2a_method):
                    continue

                # ── rate_limit ─────────────────────────────────────────────────
                if rule.action == "rate_limit":
                    max_rpm = int(rule.params.get("max_per_minute", 60))
                    key = (label, requester_ip)
                    win = self._rate_windows.get(key)
                    now = time.monotonic()
                    if win is None or now - win.window_ts > 60:
                        self._rate_windows[key] = _RateWindow(count=1, window_ts=now)
                    else:
                        win.count += 1
                        if win.count > max_rpm:
                            self._record(label, "rate_limited")
                            return FirewallDecision(
                                action="block",
                                reason=f"rate_limit:{rule.rule_id}:{win.count}/{max_rpm}rpm",
                            )
                    continue  # rate_limit rule doesn't stop evaluation by itself

                # ── block ──────────────────────────────────────────────────────
                if rule.action == "block":
                    self._record(label, "block")
                    return FirewallDecision(action="block", reason=f"rule:{rule.rule_id}")

                # ── allow (allowlist mode) ────────────────────────────────────
                # allow rules are handled as a group below
                break

            # Allow-list logic: if ANY allow rules exist for this label/global,
            # a request that didn't match any of them is denied.
            if action_type == "allow":
                allow_rules = [r for r in rules if r.action == "allow"]
                if allow_rules:
                    matched_any = any(self._matches(r, body_str, a2a_method) for r in allow_rules)
                    if not matched_any:
                        self._record(label, "block")
                        return FirewallDecision(action="block", reason="allowlist:no_match")

            # ── cache ──────────────────────────────────────────────────────────
            if action_type == "cache":
                for rule in action_rules:
                    if not self._matches(rule, body_str, a2a_method):
                        continue
                    hit = self._cache_get(label, body_bytes)
                    if hit is not None:
                        self._record(label, "cache_hit")
                        return FirewallDecision(action="cache_hit", reason=f"rule:{rule.rule_id}", payload=hit)
                    # no hit yet — mark the TTL so the proxy can call cache_set after forwarding
                    # (we store the TTL as a signal; actual set happens in server.py after response)
                    break  # only one cache rule needed to activate caching

            # ── reroute ────────────────────────────────────────────────────────
            if action_type == "reroute":
                for rule in action_rules:
                    if not self._matches(rule, body_str, a2a_method):
                        continue
                    new_label = rule.params.get("to", "")
                    if new_label:
                        self._record(label, "reroute")
                        return FirewallDecision(action="reroute", reason=f"rule:{rule.rule_id}", payload=new_label)

            # ── short_circuit ──────────────────────────────────────────────────
            if action_type == "short_circuit":
                for rule in action_rules:
                    if not self._matches(rule, body_str, a2a_method):
                        continue
                    static_response = rule.params.get("response", {"message": "short-circuited by firewall"})
                    self._record(label, "short_circuit")
                    return FirewallDecision(action="short_circuit", reason=f"rule:{rule.rule_id}", payload=static_response)

        self._record(label, "pass")
        return _PASS

    # ── Match helper ───────────────────────────────────────────────────────────

    def _matches(self, rule: FirewallRule, body_str: str, a2a_method: str) -> bool:
        mt = rule.match_type
        mv = rule.match_value
        if mt == "always":
            return True
        if mt == "method":
            return a2a_method == mv
        if mt == "contains":
            return mv.lower() in body_str.lower()
        if mt == "regex":
            try:
                return bool(re.search(mv, body_str, re.IGNORECASE))
            except re.error:
                logger.warning(f"firewall: invalid regex in rule {rule.rule_id!r}: {mv!r}")
                return False
        return False

    # ── Response cache ─────────────────────────────────────────────────────────

    def _cache_key(self, label: str, body: bytes) -> str:
        return hashlib.sha256(f"{label}:".encode() + body).hexdigest()[:16]

    def _cache_get(self, label: str, body: bytes) -> Optional[dict]:
        key = self._cache_key(label, body)
        entry = self._cache.get(key)
        if entry is None:
            return None
        payload, expiry = entry
        if time.monotonic() > expiry:
            del self._cache[key]
            return None
        return payload

    def cache_set(self, label: str, body: bytes, response: dict, ttl: int) -> None:
        key = self._cache_key(label, body)
        self._cache[key] = (response, time.monotonic() + ttl)

    def get_cache_ttl_for(self, label: str, body_str: str, a2a_method: str) -> Optional[int]:
        """Return cache TTL (seconds) if a cache rule matches, else None."""
        for bucket in ("*", label):
            for rule in self._rules.get(bucket, []):
                if rule.action == "cache" and self._matches(rule, body_str, a2a_method):
                    return int(rule.params.get("ttl", 300))
        return None

    # ── Stats ──────────────────────────────────────────────────────────────────

    def _record(self, label: str, action: str) -> None:
        self._stats[label][action] = self._stats[label].get(action, 0) + 1

    def get_stats(self) -> dict:
        return {label: dict(counts) for label, counts in self._stats.items()}

    # ── MongoDB persistence ────────────────────────────────────────────────────

    async def load_from_mongo(self, col) -> None:
        self._mongo_col = col
        count = 0
        try:
            async for doc in col.find({}):
                doc.pop("_id", None)
                rule = FirewallRule.from_dict(doc)
                self._rules[rule.label].append(rule)
                count += 1
            for label_rules in self._rules.values():
                label_rules.sort(key=lambda r: r.priority)
            logger.info(f"firewall: loaded {count} rule(s) from MongoDB")
        except Exception as exc:
            logger.error(f"firewall: MongoDB load failed: {exc}")

    async def _save_rule(self, rule: FirewallRule) -> None:
        if self._mongo_col is None:
            return
        try:
            doc = rule.to_dict()
            doc["created_at"] = rule.created_at  # keep datetime object for Mongo
            await self._mongo_col.update_one(
                {"rule_id": rule.rule_id},
                {"$set": doc},
                upsert=True,
            )
        except Exception as exc:
            logger.error(f"firewall: MongoDB save failed ({rule.rule_id}): {exc}")

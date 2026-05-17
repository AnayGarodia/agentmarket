"""sandbox_inject_failure — HTTP-layer chaos for ``sandbox_http_request``.

# OWNS: per-sandbox toxic-rule state and the helper functions
#       ``sandbox_http_request`` consults before issuing the underlying
#       curl call. NET_ADMIN-level packet-loss / latency at the kernel
#       layer remains a follow-up (it needs a privileged sidecar).
# NOT OWNS: anything outside HTTP — outbound TCP/UDP from non-HTTP
#           protocols stays unaffected by these rules in v0.
# INVARIANTS:
#   * Rules persist in-memory only; restart wipes them (matches the
#     "single-session demo" use case the chaos primitives serve).
#   * A rule's ``target`` is matched as a substring against the request
#     URL. Empty target matches every URL.
"""

from __future__ import annotations

import random
import secrets
import threading
import time
from typing import Any

from core.sandbox.models import SandboxInvalidInput, SandboxNotFound
from core.sandbox.state import get

_RULE_KINDS = ("latency", "loss", "abort", "off")
_RULES: dict[str, list[dict[str, Any]]] = {}
_RULES_LOCK = threading.RLock()


def inject_failure(payload: dict[str, Any]) -> dict[str, Any]:
    """Add (or clear) a chaos rule for this sandbox's HTTP-helper layer.

    Input:
        ``{sandbox_id, kind: latency|loss|abort|off, target: <url substring>,
          value: <ms for latency, 0..1 for loss, ignored for abort/off>}``

    Why: deterministic-enough chaos for "does the retry actually work?"
    drills, without privileged kernel-level tc / qdisc. NET_ADMIN-class
    packet manipulation lives in the follow-up sidecar.
    """
    sandbox_id = str((payload or {}).get("sandbox_id") or "").strip()
    if not sandbox_id:
        raise SandboxInvalidInput("sandbox_id is required")
    state = get(sandbox_id)
    if state is None:
        raise SandboxNotFound(f"sandbox '{sandbox_id}' is not active on this host")
    kind = str(payload.get("kind") or "").strip().lower()
    if kind not in _RULE_KINDS:
        raise SandboxInvalidInput(
            f"kind must be one of {_RULE_KINDS}; got {kind!r}"
        )
    target = str(payload.get("target") or "").strip()
    if kind == "off":
        cleared = _clear_rules(sandbox_id)
        state.touch()
        return {
            "sandbox_id": sandbox_id,
            "rules_cleared": cleared,
            "rules": [],
        }
    rule = _build_rule(kind, target, payload.get("value"))
    with _RULES_LOCK:
        _RULES.setdefault(sandbox_id, []).append(rule)
    state.touch()
    return {
        "sandbox_id": sandbox_id,
        "rule": rule,
        "rules": list(_RULES.get(sandbox_id, [])),
        "note": (
            "Rules apply to sandbox_http_request only — TCP/UDP traffic "
            "from in-sandbox processes outside the HTTP helper is "
            "unaffected. NET_ADMIN-class kernel chaos is the follow-up."
        ),
    }


def apply_to_url(sandbox_id: str, url: str) -> dict[str, Any]:
    """Pure-ish: return ``{action, delay_ms?, status?}`` for ``url``.

    Called by :mod:`core.sandbox.http_ops` before each request. The
    returned ``action`` is one of:

      * ``"allow"`` — proceed normally.
      * ``"delay"`` — sleep ``delay_ms`` then proceed normally.
      * ``"loss"`` — drop the request; report a synthetic timeout to
        the caller.
      * ``"abort"`` — return a synthetic 503 without making the call.

    Multiple rules: the first matching rule wins. Rules with no target
    match every URL. ``loss`` is sampled per call against its value.
    """
    rules = _RULES.get(sandbox_id) or []
    for rule in rules:
        target = rule.get("target") or ""
        if target and target not in url:
            continue
        kind = rule.get("kind")
        if kind == "latency":
            return {"action": "delay", "delay_ms": int(rule.get("value") or 0)}
        if kind == "loss":
            if random.random() < float(rule.get("value") or 0):  # noqa: S311 — chaos
                return {"action": "loss"}
            return {"action": "allow"}
        if kind == "abort":
            return {
                "action": "abort",
                "status": 503,
                "synthetic_body": "aztea-chaos: aborted by sandbox_inject_failure",
            }
    return {"action": "allow"}


def list_rules(sandbox_id: str) -> list[dict[str, Any]]:
    """Pure: snapshot copy of the current rule list for ``sandbox_id``."""
    return list(_RULES.get(sandbox_id) or [])


def _build_rule(kind: str, target: str, value: Any) -> dict[str, Any]:
    """Pure: validate the rule shape per kind and stamp an ID + created_at."""
    rule_id = f"chaos_{secrets.token_hex(4)}"
    if kind == "latency":
        try:
            delay = int(value)
        except (TypeError, ValueError) as exc:
            raise SandboxInvalidInput(
                "latency rule requires integer ms in 'value'"
            ) from exc
        if delay <= 0 or delay > 60_000:
            raise SandboxInvalidInput(
                "latency.value must be between 1 and 60000 ms"
            )
        return {
            "rule_id": rule_id,
            "kind": "latency",
            "target": target,
            "value": delay,
            "created_at": int(time.time()),
        }
    if kind == "loss":
        try:
            ratio = float(value)
        except (TypeError, ValueError) as exc:
            raise SandboxInvalidInput(
                "loss rule requires float (0..1) in 'value'"
            ) from exc
        if not 0.0 < ratio <= 1.0:
            raise SandboxInvalidInput("loss.value must be > 0 and <= 1.0")
        return {
            "rule_id": rule_id,
            "kind": "loss",
            "target": target,
            "value": ratio,
            "created_at": int(time.time()),
        }
    return {
        "rule_id": rule_id,
        "kind": "abort",
        "target": target,
        "created_at": int(time.time()),
    }


def _clear_rules(sandbox_id: str) -> int:
    """Side-effect: drop every rule for ``sandbox_id``; returns the count cleared."""
    with _RULES_LOCK:
        existing = _RULES.pop(sandbox_id, []) or []
    return len(existing)

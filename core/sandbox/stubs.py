"""Structured stub envelopes for the v0-deferred surface.

# OWNS: every action listed in the spec that v0 does NOT implement returns a
#       full envelope with ``planned_input_schema`` + ``planned_output_schema``
#       + ``tracking_issue`` — never a bare {"error": "unsupported"}.
# INVARIANTS:
#   * Each entry's planned_input_schema and planned_output_schema must be
#     valid JSON Schema (the test suite parses every entry and validates).
#   * Each entry has a tracking_issue title so the follow-up work is
#     enumerable from the codebase alone.
"""

from __future__ import annotations

from typing import Any


def _browser_stub(description: str) -> dict[str, Any]:
    """Return a generic browser-action stub envelope.

    Why: every browser verb shares the same shape; centralising the
    template means the dozen browser stubs stay in sync.
    """
    return {
        "planned_input_schema": {
            "type": "object",
            "required": ["sandbox_id", "session_id"],
            "properties": {
                "sandbox_id": {"type": "string"},
                "session_id": {"type": "string"},
                "url": {"type": "string"},
                "selector": {"type": "string"},
                "value": {"type": "string"},
                "js": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "planned_output_schema": {
            "type": "object",
            "properties": {
                "result": {"type": "object"},
                "screenshot_b64": {"type": "string"},
                "console_logs": {"type": "array"},
                "network": {"type": "array"},
            },
        },
        "tracking_issue": "live-sandbox: Playwright/CDP browser session pool",
        "description": description,
        "reason": (
            "Browser surface lands as a single follow-up issue covering the "
            "Playwright pool, per-session eviction, cookie isolation, and "
            "PDF/screenshot artefact storage."
        ),
    }


def _simple_stub(
    *,
    issue: str,
    reason: str,
    in_props: dict[str, Any] | None = None,
    out_props: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "planned_input_schema": {
            "type": "object",
            "required": ["sandbox_id"],
            "properties": {
                "sandbox_id": {"type": "string"},
                **(in_props or {}),
            },
            "additionalProperties": False,
        },
        "planned_output_schema": {
            "type": "object",
            "properties": out_props or {},
        },
        "tracking_issue": issue,
        "reason": reason,
    }


_STUB_TEMPLATES: dict[str, dict[str, Any]] = {}

# Every browser verb is now a real implementation in core.sandbox.browser.
# The browser-stub template helper above is retained only for symmetry with
# the other stub categories and as the template a future "browser_pdf" /
# new verb would adopt.

_STUB_TEMPLATES["sandbox_tunnel_open"] = {
    "planned_input_schema": {
        "type": "object",
        "required": ["sandbox_id", "service", "port"],
        "properties": {
            "sandbox_id": {"type": "string"},
            "service": {"type": "string", "description": "Compose service to expose"},
            "port": {"type": "integer", "description": "Container port to publish"},
            "auth": {
                "type": "string",
                "enum": ["bearer", "none"],
                "description": "Edge auth — bearer token issued by Aztea, or open",
            },
            "hostname_hint": {
                "type": "string",
                "description": "Optional human-readable hostname prefix (e.g. 'checkout-fix')",
            },
            "ttl_minutes": {
                "type": "integer",
                "description": "Tunnel lifetime; defaults to sandbox lifetime",
            },
        },
        "additionalProperties": False,
    },
    "planned_output_schema": {
        "type": "object",
        "properties": {
            "tunnel_id": {"type": "string"},
            "public_url": {"type": "string", "format": "uri"},
            "auth_token": {"type": "string"},
            "expires_at": {"type": "integer"},
        },
    },
    "tracking_issue": "live-sandbox: public tunnels with TLS + Caddy/Cloudflare edge",
    "reason": (
        "Public tunnels need an always-on edge proxy (Caddy with on-demand "
        "TLS, or Cloudflare Tunnel) that proxies <hostname_hint>-<sandbox_id>."
        "aztea.run to the container's port. The proxy must enforce the "
        "bearer-token auth and expire when the sandbox stops. This is "
        "infra work outside the agent module — tracked as its own rollout "
        "alongside the webhook-inbox follow-up which builds on it."
    ),
}
_STUB_TEMPLATES["sandbox_tunnel_close"] = {
    **_STUB_TEMPLATES["sandbox_tunnel_open"],
    "planned_input_schema": {
        "type": "object",
        "required": ["sandbox_id", "tunnel_id"],
        "properties": {
            "sandbox_id": {"type": "string"},
            "tunnel_id": {"type": "string"},
        },
        "additionalProperties": False,
    },
}

_STUB_TEMPLATES["sandbox_webhook_inbox"] = _simple_stub(
    issue="live-sandbox: webhook inbox + replay (builds on tunnels)",
    reason=(
        "Webhook capture is the proxy in front of sandbox_tunnel_open. It "
        "buffers incoming POSTs (Stripe, GitHub Apps, etc.), signs each "
        "with an Aztea receipt so replays are tamper-evident, and lets the "
        "caller list / replay events at will. Cannot land before tunnels."
    ),
    in_props={
        "tunnel_id": {"type": "string"},
        "since": {"type": "string", "format": "date-time"},
        "limit": {"type": "integer"},
    },
    out_props={
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "received_at": {"type": "integer"},
                    "method": {"type": "string"},
                    "path": {"type": "string"},
                    "headers": {"type": "object"},
                    "body_b64": {"type": "string"},
                    "receipt": {"type": "object"},
                },
            },
        },
        "count": {"type": "integer"},
    },
)
# sandbox_outbound_record / _replay landed as real engine actions in this
# change set (see core.sandbox.vcr). The recorder PROXY itself — the
# in-network HTTP middleware that captures requests — is still a follow-up;
# the cassette format and the record/replay flip are now stable.
# sandbox_inject_failure is now an HTTP-layer chaos implementation in
# core.sandbox.chaos (only sandbox_http_request is affected). The
# NET_ADMIN-class kernel chaos (tc/qdisc, in-container toxiproxy) remains
# the follow-up issue for arbitrary TCP/UDP traffic.
_STUB_TEMPLATES["sandbox_network_capture"] = _simple_stub(
    issue="live-sandbox: tcpdump + PCAP export (NET_RAW sidecar)",
    reason=(
        "Wire-level packet capture needs a privileged sidecar attached to "
        "the sandbox's docker network with NET_RAW. v0 stays default-deny "
        "(no CAP_NET_RAW on any container); the sidecar lands as a separate "
        "infra PR that operators can opt into per-host. HTTP-layer "
        "introspection is already available via sandbox_browser_network "
        "and sandbox_http_request response capture."
    ),
    in_props={
        "service": {"type": "string"},
        "duration_seconds": {"type": "integer"},
        "filter": {"type": "string", "description": "BPF filter expression"},
    },
    out_props={
        "pcap_path": {"type": "string"},
        "packet_count": {"type": "integer"},
        "duration_seconds": {"type": "integer"},
    },
)
_STUB_TEMPLATES["sandbox_trace"] = _simple_stub(
    issue="live-sandbox: strace / py-spy / perf attach (PTRACE sidecar)",
    reason=(
        "Process attach requires SYS_PTRACE on the target container and the "
        "right kernel headers / debug symbols on the host. Both are "
        "host-policy concerns, not agent-module work. Deferred to the "
        "privileged-helper sidecar PR alongside network_capture."
    ),
    in_props={
        "service": {"type": "string"},
        "pid": {"type": "integer"},
        "tool": {
            "type": "string",
            "enum": ["strace", "py-spy", "perf", "async-profiler"],
        },
        "duration_seconds": {"type": "integer"},
    },
    out_props={
        "flamegraph_url": {"type": "string"},
        "summary": {"type": "string"},
        "samples": {"type": "integer"},
    },
)
# sandbox_link landed as a real implementation in core.sandbox.link.
# Multi-host overlay (Docker Swarm / k8s service mesh) is a separate
# infra follow-up.
# sandbox_batch_start landed as a real implementation in this change set —
# see core.sandbox.lifecycle.batch_start. The wallet-hold integration is
# still tracked separately (see batch_start's billing_notice).
_STUB_TEMPLATES["sandbox_share"] = _simple_stub(
    issue="live-sandbox: shared collab sessions (edge multiplexer)",
    reason=(
        "Terminal-share / co-view is a separate product surface. It needs "
        "an edge multiplexer (Caddy + Websocat or a custom relay) plus a "
        "consent model so the inviter can scope access (read / full). "
        "Tracked as its own infra rollout, not engine work."
    ),
    in_props={
        "access": {"type": "string", "enum": ["read", "full"]},
        "ttl_minutes": {"type": "integer"},
        "actor_hint": {"type": "string"},
    },
    out_props={
        "share_id": {"type": "string"},
        "share_url": {"type": "string", "format": "uri"},
        "join_token": {"type": "string"},
        "expires_at": {"type": "integer"},
    },
)
# sandbox_export_snapshot now ships the file:// destination path in
# core.sandbox.export. Cloud-bucket destinations (s3://, gs://, etc.)
# remain the hosted-mode follow-up because they require wallet bucket
# credentials.


def stub_for(action: str) -> dict[str, Any]:
    """Return the canonical stub envelope for ``action``.

    Why: the agent module dispatches deferred actions here so callers see
    a uniform shape regardless of which follow-up issue the action
    belongs to.
    """
    template = _STUB_TEMPLATES.get(action)
    if template is None:
        return {
            "stubbed": True,
            "action": action,
            "tracking_issue": "live-sandbox: unknown deferred action",
            "reason": "Action is reserved in the spec but not yet templated.",
        }
    return {
        "stubbed": True,
        "action": action,
        **template,
    }


def stub_actions() -> list[str]:
    """Pure: list every action verb backed by a stub envelope."""
    return sorted(_STUB_TEMPLATES.keys())

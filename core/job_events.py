"""Fire-and-forget realtime event bridge from Python → Elixir/Phoenix.

# OWNS: best-effort HTTP POST to the Elixir sidecar's `/internal/job-events`
#       endpoint + issuance of short-lived HMAC tokens for the Phoenix socket.
# NOT OWNS: any job state of record (still owned by core/jobs/*), the SSE
#           feed at GET /jobs/events (still owned by part_010), nor the
#           webhook delivery pipeline (still owned by part_003).
# INVARIANTS:
#   - notify_job_event() must NEVER raise. A network blip, a misconfigured
#     env var, or Elixir being down must not affect job lifecycle.
#   - When AZTEA_ELIXIR_EVENTS is unset / != "1", every public function is a
#     no-op fast-path that allocates nothing.
#   - Money paths never call into this module. State transitions do.
# DECISIONS:
#   - Dispatch is a daemon thread, not asyncio: the surrounding code is sync
#     SQLAlchemy/raw SQL; a thread keeps it simple and isolates failures.
#   - One-token-format (v1.<user>.<exp>.<sig>) shared with Elixir. Avoids JSON
#     parsing in the verifier and keeps Phoenix.Token out of the dep loop.
# KNOWN DEBT:
#   - No retry / backoff on the POST. Realtime is opportunistic; the next
#     state transition or the SSE/poll fallback covers brief blips.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

_LOG = logging.getLogger("aztea.job_events")

_FEATURE_FLAG_ENV = "AZTEA_ELIXIR_EVENTS"
_URL_ENV = "ELIXIR_HTTP_URL"
_SECRET_ENV = "ELIXIR_INTERNAL_SHARED_SECRET"

_DEFAULT_URL = "http://127.0.0.1:4000"
_INTERNAL_PATH = "/internal/job-events"

# Hard timeout on the loopback POST. Phoenix should answer in milliseconds;
# anything past 1.5s means the sidecar is wedged and we want the thread freed.
_POST_TIMEOUT_SECONDS = 1.5

_TOKEN_VERSION = "v1"
_TOKEN_TTL_SECONDS = 300

# Module-level cache so threading.Thread spawning doesn't pay an env lookup
# on every event. The cache is refreshed lazily — env changes after start
# require a restart, which matches every other env-driven flag in core/.
_FLAG_LOCK = threading.Lock()
_FLAG_CACHED: bool | None = None


def is_enabled() -> bool:
    """Return True if AZTEA_ELIXIR_EVENTS=1. Cached after first read."""
    global _FLAG_CACHED
    if _FLAG_CACHED is not None:
        return _FLAG_CACHED
    with _FLAG_LOCK:
        if _FLAG_CACHED is None:
            _FLAG_CACHED = os.environ.get(_FEATURE_FLAG_ENV, "").strip() == "1"
    return _FLAG_CACHED


def reset_cache_for_tests() -> None:
    """Test helper: clear the cached feature flag so per-test env tweaks land."""
    global _FLAG_CACHED
    with _FLAG_LOCK:
        _FLAG_CACHED = None


def notify_job_event(
    user_id: str | None,
    job_id: str | None,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Best-effort fan-out of a job state transition to the Elixir sidecar.

    Why: the FE polls every 5s; with this hook landed, sub-second updates
    arrive over Phoenix Channels instead. Failure here is benign — polling
    + SSE remain in place as the safety net.

    Contract: must never raise. Returns immediately when the feature flag
    is off; otherwise spawns a daemon thread that POSTs JSON and logs a
    structured warning on any error.
    """
    if not is_enabled():
        return
    if not user_id or not job_id or not event_type:
        return
    body = {
        "user_id": str(user_id),
        "job_id": str(job_id),
        "event_type": str(event_type),
        "payload": payload or {},
    }
    threading.Thread(
        target=_post_event_safely,
        args=(body,),
        name="aztea-elixir-event-dispatch",
        daemon=True,
    ).start()


def issue_socket_token(user_id: str, ttl_seconds: int = _TOKEN_TTL_SECONDS) -> dict[str, Any]:
    """Mint a short-lived HMAC token the FE uses to open the Phoenix socket.

    Returned shape::

        {"token": "v1.<user>.<exp>.<sig>", "expires_at": <unix-epoch-seconds>}

    Token is verified server-side by Elixir's ``AzteaWeb.Token.verify/2``.
    """
    secret = _shared_secret()
    if not secret:
        raise SocketTokenError("ELIXIR_INTERNAL_SHARED_SECRET is not configured.")
    if not user_id:
        raise SocketTokenError("user_id must be non-empty.")
    if ttl_seconds <= 0:
        raise SocketTokenError("ttl_seconds must be positive.")
    exp = int(time.time()) + ttl_seconds
    sig = _sign_token(user_id=user_id, exp=exp, secret=secret)
    return {
        "token": f"{_TOKEN_VERSION}.{user_id}.{exp}.{sig}",
        "expires_at": exp,
    }


class SocketTokenError(RuntimeError):
    """Raised when /auth/socket-token is hit without the required secret configured."""


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _shared_secret() -> str | None:
    raw = os.environ.get(_SECRET_ENV, "").strip()
    return raw or None


def _elixir_url() -> str:
    return os.environ.get(_URL_ENV, _DEFAULT_URL).rstrip("/") + _INTERNAL_PATH


def _sign_token(*, user_id: str, exp: int, secret: str) -> str:
    payload = f"{_TOKEN_VERSION}|{user_id}|{exp}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _post_event_safely(body: dict[str, Any]) -> None:
    """Background thread target. Owns its own error handling — never re-raises."""
    secret = _shared_secret()
    if not secret:
        _LOG.warning(
            "elixir.notify_skipped: ELIXIR_INTERNAL_SHARED_SECRET unset",
            extra={"job_id": body.get("job_id"), "event_type": body.get("event_type")},
        )
        return

    url = _elixir_url()
    try:
        data = json.dumps(body).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _LOG.warning(
            "elixir.notify_serialize_failed",
            extra={"err": str(exc), "event_type": body.get("event_type")},
        )
        return

    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {secret}",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=_POST_TIMEOUT_SECONDS) as resp:
            status = getattr(resp, "status", 0) or 0
            if status >= 400:
                _LOG.warning(
                    "elixir.notify_non_2xx",
                    extra={"status": status, "event_type": body.get("event_type")},
                )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _LOG.warning(
            "elixir.notify_post_failed",
            extra={"err": str(exc), "event_type": body.get("event_type")},
        )

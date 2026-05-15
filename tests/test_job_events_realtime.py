"""Tests for the realtime job-event bridge (core/job_events.py)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from unittest.mock import patch

import pytest

from core import job_events


_SECRET = "test-shared-secret-please-do-not-use-in-prod"


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test sees a fresh feature-flag cache so env tweaks land immediately."""
    job_events.reset_cache_for_tests()
    yield
    job_events.reset_cache_for_tests()


def _enable_realtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZTEA_ELIXIR_EVENTS", "1")
    monkeypatch.setenv("ELIXIR_INTERNAL_SHARED_SECRET", _SECRET)
    job_events.reset_cache_for_tests()


class _Capture:
    """Records urlopen invocations + spans on the dispatch thread."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.received = threading.Event()
        self.raise_on_call = False

    def __call__(self, request, timeout=None):  # noqa: D401 — urlopen signature
        if self.raise_on_call:
            raise OSError("simulated transient failure")
        data = request.data.decode("utf-8") if request.data else ""
        self.calls.append(
            {
                "url": request.full_url,
                "headers": dict(request.headers),
                "json": json.loads(data) if data else None,
                "timeout": timeout,
            }
        )
        self.received.set()

        class _Resp:
            status = 204

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *_):
                return False

        return _Resp()


def _wait_for_thread(capture: _Capture, timeout: float = 2.0) -> None:
    """Block until the daemon dispatch thread completes its POST."""
    assert capture.received.wait(timeout), "dispatch thread never fired"


# ---------------------------------------------------------------------------
# Feature-flag gating
# ---------------------------------------------------------------------------


def test_notify_no_op_when_flag_unset(monkeypatch):
    monkeypatch.delenv("AZTEA_ELIXIR_EVENTS", raising=False)
    monkeypatch.setenv("ELIXIR_INTERNAL_SHARED_SECRET", _SECRET)
    job_events.reset_cache_for_tests()

    capture = _Capture()
    with patch.object(job_events.urllib.request, "urlopen", capture):
        job_events.notify_job_event("user_x", "job_1", "job.created", {"ok": True})
        # Give the (unspawned) thread a moment in case the no-op contract regressed.
        time.sleep(0.05)
    assert capture.calls == []


def test_notify_no_op_when_flag_zero(monkeypatch):
    monkeypatch.setenv("AZTEA_ELIXIR_EVENTS", "0")
    monkeypatch.setenv("ELIXIR_INTERNAL_SHARED_SECRET", _SECRET)
    job_events.reset_cache_for_tests()

    capture = _Capture()
    with patch.object(job_events.urllib.request, "urlopen", capture):
        job_events.notify_job_event("user_x", "job_1", "job.created", {})
        time.sleep(0.05)
    assert capture.calls == []


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_notify_posts_when_enabled(monkeypatch):
    _enable_realtime(monkeypatch)
    capture = _Capture()

    with patch.object(job_events.urllib.request, "urlopen", capture):
        job_events.notify_job_event(
            "user_alpha", "job_42", "job.complete", {"status": "complete"}
        )
        _wait_for_thread(capture)

    assert len(capture.calls) == 1
    call = capture.calls[0]
    assert call["url"].endswith("/internal/job-events")
    # urllib normalises headers to Title-Case; check both forms.
    assert call["headers"].get("Authorization") == f"Bearer {_SECRET}"
    assert call["headers"].get("Content-type") == "application/json"
    assert call["json"] == {
        "user_id": "user_alpha",
        "job_id": "job_42",
        "event_type": "job.complete",
        "payload": {"status": "complete"},
    }


def test_notify_skips_when_secret_missing(monkeypatch, caplog):
    monkeypatch.setenv("AZTEA_ELIXIR_EVENTS", "1")
    monkeypatch.delenv("ELIXIR_INTERNAL_SHARED_SECRET", raising=False)
    job_events.reset_cache_for_tests()

    capture = _Capture()
    with patch.object(job_events.urllib.request, "urlopen", capture):
        job_events.notify_job_event("user_a", "job_a", "job.created", {})
        time.sleep(0.05)

    assert capture.calls == []


def test_notify_swallows_post_errors(monkeypatch):
    """If Elixir is unreachable the lifecycle code must NOT see an exception."""
    _enable_realtime(monkeypatch)
    capture = _Capture()
    capture.raise_on_call = True

    finished = threading.Event()

    def _runner():
        # If notify_job_event ever raises, this thread dies before setting finished.
        job_events.notify_job_event("user_a", "job_b", "job.failed", {})
        finished.set()

    with patch.object(job_events.urllib.request, "urlopen", capture):
        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join(timeout=2.0)

    assert finished.is_set(), "notify_job_event must never raise"


def test_notify_skips_when_required_fields_blank(monkeypatch):
    _enable_realtime(monkeypatch)
    capture = _Capture()

    with patch.object(job_events.urllib.request, "urlopen", capture):
        job_events.notify_job_event("", "job_a", "job.created", {})
        job_events.notify_job_event("user_a", "", "job.created", {})
        job_events.notify_job_event("user_a", "job_a", "", {})
        time.sleep(0.1)

    assert capture.calls == []


# ---------------------------------------------------------------------------
# Socket token
# ---------------------------------------------------------------------------


def test_issue_socket_token_round_trip(monkeypatch):
    monkeypatch.setenv("ELIXIR_INTERNAL_SHARED_SECRET", _SECRET)

    out = job_events.issue_socket_token("user_id_1", ttl_seconds=60)
    assert out["expires_at"] > time.time() + 30
    assert out["expires_at"] <= time.time() + 60 + 1

    token = out["token"]
    parts = token.split(".")
    assert parts[0] == "v1"
    assert parts[1] == "user_id_1"
    assert int(parts[2]) == out["expires_at"]

    # Verify the signature matches the documented HMAC scheme so the Elixir
    # verifier (AzteaWeb.Token) is guaranteed to accept it.
    expected_sig = hmac.new(
        _SECRET.encode("utf-8"),
        f"v1|user_id_1|{out['expires_at']}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert parts[3] == expected_sig


def test_issue_socket_token_requires_secret(monkeypatch):
    monkeypatch.delenv("ELIXIR_INTERNAL_SHARED_SECRET", raising=False)
    with pytest.raises(job_events.SocketTokenError):
        job_events.issue_socket_token("user_a")


def test_issue_socket_token_rejects_blank_user(monkeypatch):
    monkeypatch.setenv("ELIXIR_INTERNAL_SHARED_SECRET", _SECRET)
    with pytest.raises(job_events.SocketTokenError):
        job_events.issue_socket_token("")


def test_issue_socket_token_rejects_zero_ttl(monkeypatch):
    monkeypatch.setenv("ELIXIR_INTERNAL_SHARED_SECRET", _SECRET)
    with pytest.raises(job_events.SocketTokenError):
        job_events.issue_socket_token("user_a", ttl_seconds=0)

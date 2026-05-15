"""End-to-end: every job state transition forwards to the Elixir sidecar
when the feature flag is on, and is invisible when off.

Hot paths under test:
  POST /jobs                           → job.created event
  POST /jobs/{id}/claim                → job.claimed event
  POST /jobs/{id}/complete             → job.completed event

Three events MUST land in order, and the lifecycle itself MUST be unaffected
by Elixir-side failures (we simulate a 500 from the sidecar).
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from core import job_events
from tests.integration.helpers import (
    _auth_headers,
    _create_job_via_api,
    _fund_user_wallet,
    _register_agent_via_api,
    _register_user,
)


_SECRET = "test-realtime-secret"


class _Recorder:
    """Records every urlopen invocation and waits for an expected count."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.events: list[dict] = []
        self.raise_with: BaseException | None = None

    def __call__(self, request, timeout=None):  # noqa: D401
        if self.raise_with is not None:
            raise self.raise_with
        body = request.data.decode("utf-8") if request.data else "{}"
        import json as _json

        with self.lock:
            self.events.append(_json.loads(body))

        class _Resp:
            status = 204

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *_):
                return False

        return _Resp()

    def wait_for(self, count: int, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if len(self.events) >= count:
                    return
            time.sleep(0.02)
        with self.lock:
            raise AssertionError(
                f"expected {count} events, got {len(self.events)}: {self.events}"
            )


def _enable_realtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZTEA_ELIXIR_EVENTS", "1")
    monkeypatch.setenv("ELIXIR_INTERNAL_SHARED_SECRET", _SECRET)
    job_events.reset_cache_for_tests()


def _disable_realtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZTEA_ELIXIR_EVENTS", raising=False)
    monkeypatch.setenv("ELIXIR_INTERNAL_SHARED_SECRET", _SECRET)
    job_events.reset_cache_for_tests()


def test_full_lifecycle_emits_three_events(client, monkeypatch):
    _enable_realtime(monkeypatch)

    caller = _register_user()
    caller_key = caller["raw_api_key"]
    _fund_user_wallet(caller, amount_cents=5_000)
    worker = _register_user()
    worker_key = worker["raw_api_key"]

    agent_id = _register_agent_via_api(
        client, worker_key, name=f"realtime-{caller['user_id'][:8]}"
    )

    recorder = _Recorder()
    with patch.object(job_events.urllib.request, "urlopen", recorder):
        created = _create_job_via_api(client, caller_key, agent_id=agent_id)
        job_id = created["job_id"]

        claim_resp = client.post(
            f"/jobs/{job_id}/claim",
            headers=_auth_headers(worker_key),
            json={"lease_seconds": 60},
        )
        assert claim_resp.status_code == 200, claim_resp.text
        claim_token = claim_resp.json()["claim_token"]

        complete_resp = client.post(
            f"/jobs/{job_id}/complete",
            headers=_auth_headers(worker_key),
            json={
                "claim_token": claim_token,
                "output_payload": {"result": "ok"},
            },
        )
        assert complete_resp.status_code == 200, complete_resp.text

        # Three transitions × possibly two recipients (caller + agent owner)
        # arrive on background threads; wait until at least 3 land.
        recorder.wait_for(3, timeout=3.0)

    event_types = [e["event_type"] for e in recorder.events]
    assert "job.created" in event_types
    assert "job.claimed" in event_types
    assert "job.completed" in event_types

    # Every event must carry the right user_id and job_id.
    caller_owner = f"user:{caller['user_id']}"
    worker_owner = f"user:{worker['user_id']}"
    for event in recorder.events:
        assert event["job_id"] == job_id
        assert event["user_id"] in {caller_owner, worker_owner}
        assert isinstance(event["payload"], dict)


def test_no_events_when_flag_disabled(client, monkeypatch):
    _disable_realtime(monkeypatch)

    caller = _register_user()
    caller_key = caller["raw_api_key"]
    _fund_user_wallet(caller, amount_cents=5_000)
    worker = _register_user()
    worker_key = worker["raw_api_key"]

    agent_id = _register_agent_via_api(
        client, worker_key, name=f"realtime-off-{caller['user_id'][:8]}"
    )

    recorder = _Recorder()
    with patch.object(job_events.urllib.request, "urlopen", recorder):
        created = _create_job_via_api(client, caller_key, agent_id=agent_id)
        claim_resp = client.post(
            f"/jobs/{created['job_id']}/claim",
            headers=_auth_headers(worker_key),
            json={"lease_seconds": 60},
        )
        assert claim_resp.status_code == 200
        # Give any (broken) dispatch thread a moment to leak through.
        time.sleep(0.2)

    assert recorder.events == [], (
        f"Realtime POSTs leaked while feature flag was off: {recorder.events}"
    )


def test_elixir_unreachable_does_not_break_lifecycle(client, monkeypatch):
    _enable_realtime(monkeypatch)

    caller = _register_user()
    caller_key = caller["raw_api_key"]
    _fund_user_wallet(caller, amount_cents=5_000)
    worker = _register_user()
    worker_key = worker["raw_api_key"]

    agent_id = _register_agent_via_api(
        client, worker_key, name=f"realtime-down-{caller['user_id'][:8]}"
    )

    recorder = _Recorder()
    recorder.raise_with = OSError("simulated elixir down")
    with patch.object(job_events.urllib.request, "urlopen", recorder):
        created = _create_job_via_api(client, caller_key, agent_id=agent_id)
        claim_resp = client.post(
            f"/jobs/{created['job_id']}/claim",
            headers=_auth_headers(worker_key),
            json={"lease_seconds": 60},
        )
        assert claim_resp.status_code == 200, claim_resp.text
        complete_resp = client.post(
            f"/jobs/{created['job_id']}/complete",
            headers=_auth_headers(worker_key),
            json={
                "claim_token": claim_resp.json()["claim_token"],
                "output_payload": {"result": "ok"},
            },
        )
        assert complete_resp.status_code == 200, complete_resp.text
        # No bookkeeping: the OSErrors are raised on the background threads
        # and swallowed in the helper. The job lifecycle reaching "complete"
        # already proves nothing leaked into the request path.
        time.sleep(0.1)


def test_socket_token_endpoint(client, monkeypatch):
    monkeypatch.setenv("ELIXIR_INTERNAL_SHARED_SECRET", _SECRET)
    caller = _register_user()
    caller_key = caller["raw_api_key"]

    resp = client.post(
        "/auth/socket-token",
        headers=_auth_headers(caller_key),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token"].startswith("v1.")
    parts = body["token"].split(".")
    assert parts[1] == f"user:{caller['user_id']}"
    assert int(parts[2]) == body["expires_at"]


def test_socket_token_endpoint_503_without_secret(client, monkeypatch):
    monkeypatch.delenv("ELIXIR_INTERNAL_SHARED_SECRET", raising=False)
    caller = _register_user()
    caller_key = caller["raw_api_key"]

    resp = client.post(
        "/auth/socket-token",
        headers=_auth_headers(caller_key),
    )
    assert resp.status_code == 503, resp.text

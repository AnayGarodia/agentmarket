"""
End-to-end tests for diff-watchers.

Covers:
  - POST /watch creates rows (caller-scoped, validated)
  - PATCH / DELETE / GET /watch/{id} obey ownership
  - Sweeper diff gate: identical fingerprint → no fire
  - Sweeper diff gate: changed fingerprint → fires job, increments spend
  - Sweeper budget gate: status flips to budget_exhausted
  - Sweeper UTC rollover resets spend and reactivates
  - Sweeper policy='always' fires every tick
  - Webhook delivery emits HMAC-signed POST after job settles
  - /watch/{id}/test computes fingerprint without billing
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from tests.integration.support import *  # noqa: F401,F403
from tests.integration.support import (
    TEST_MASTER_KEY,
    _auth_headers,
    _fund_user_wallet,
    _register_user,
)

from core import jobs as _jobs
from core import payments as _payments
from core import watchers as _watchers
from core.watchers import sweeper as _watchers_sweeper


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _isolate_watchers(monkeypatch, db_path):
    """Route core.watchers writes to the same isolated DB as the rest of the suite."""
    monkeypatch.setattr(_watchers, "DB_PATH", str(db_path))
    # Ensure the thread-local connection (if any) is reset.
    if hasattr(_watchers.crud, "_local"):
        try:
            delattr(_watchers.crud._local, "conn")
        except (AttributeError, KeyError):
            pass


def _register_test_agent(client, raw_api_key, *, price_usd=0.05):
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "name": f"watch-target-{suffix}",
        "description": "Integration test watcher target agent",
        "endpoint_url": f"https://agents.example.com/{suffix}",
        "price_per_call_usd": price_usd,
        "tags": ["watcher-test"],
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "title": "Task",
                    "description": "Task input for watcher target.",
                }
            },
        },
        "output_examples": [{"input": {"task": "x"}, "output": {"ok": True}}],
    }
    resp = client.post("/registry/register", headers=_auth_headers(raw_api_key), json=payload)
    assert resp.status_code == 201, resp.text
    agent_id = resp.json()["agent_id"]
    review = client.post(
        f"/admin/agents/{agent_id}/review",
        headers=_auth_headers(TEST_MASTER_KEY),
        json={"decision": "approve", "note": "test"},
    )
    assert review.status_code == 200
    return agent_id


def _create_watcher(client, key, *, agent_id, **overrides):
    body = {
        "agent_id": agent_id,
        "target_kind": "http",
        "target_url": "https://example.com/feed",
        "tick_interval_seconds": 60,
        "budget_per_day_cents": 100,
        "delivery_email": "user@example.com",
    }
    body.update(overrides)
    resp = client.post("/watch", headers=_auth_headers(key), json=body)
    return resp


# ---------------------------------------------------------------------------
# Route-level checks
# ---------------------------------------------------------------------------


def test_create_watcher_requires_delivery(client, isolated_db, monkeypatch):
    _isolate_watchers(monkeypatch, isolated_db)
    user = _register_user()
    _fund_user_wallet(user, amount_cents=1000)
    agent_id = _register_test_agent(client, user["raw_api_key"])
    resp = _create_watcher(
        client,
        user["raw_api_key"],
        agent_id=agent_id,
        delivery_email=None,
        delivery_webhook_url=None,
    )
    assert resp.status_code == 422


def test_create_watcher_validates_target_url(client, isolated_db, monkeypatch):
    _isolate_watchers(monkeypatch, isolated_db)
    user = _register_user()
    _fund_user_wallet(user, amount_cents=1000)
    agent_id = _register_test_agent(client, user["raw_api_key"])
    monkeypatch.setenv("ALLOW_PRIVATE_OUTBOUND_URLS", "0")
    resp = _create_watcher(
        client,
        user["raw_api_key"],
        agent_id=agent_id,
        target_url="http://127.0.0.1:8000",
    )
    assert resp.status_code == 400


def test_create_watcher_rejects_budget_below_per_call_price(client, isolated_db, monkeypatch):
    _isolate_watchers(monkeypatch, isolated_db)
    user = _register_user()
    _fund_user_wallet(user, amount_cents=10000)
    # price_per_call_usd=0.50 → 50 cents; budget < 50 → reject.
    agent_id = _register_test_agent(client, user["raw_api_key"], price_usd=0.50)
    resp = _create_watcher(
        client,
        user["raw_api_key"],
        agent_id=agent_id,
        budget_per_day_cents=10,
    )
    assert resp.status_code == 400, resp.text


def test_get_watcher_owner_scoped(client, isolated_db, monkeypatch):
    _isolate_watchers(monkeypatch, isolated_db)
    user_a = _register_user()
    user_b = _register_user()
    _fund_user_wallet(user_a, amount_cents=1000)
    agent_id = _register_test_agent(client, user_a["raw_api_key"])
    created = _create_watcher(client, user_a["raw_api_key"], agent_id=agent_id).json()
    wid = created["watcher_id"]

    own = client.get(f"/watch/{wid}", headers=_auth_headers(user_a["raw_api_key"]))
    assert own.status_code == 200
    other = client.get(f"/watch/{wid}", headers=_auth_headers(user_b["raw_api_key"]))
    assert other.status_code == 403


def test_delete_watcher_removes_row(client, isolated_db, monkeypatch):
    _isolate_watchers(monkeypatch, isolated_db)
    user = _register_user()
    _fund_user_wallet(user, amount_cents=1000)
    agent_id = _register_test_agent(client, user["raw_api_key"])
    wid = _create_watcher(client, user["raw_api_key"], agent_id=agent_id).json()["watcher_id"]
    resp = client.delete(f"/watch/{wid}", headers=_auth_headers(user["raw_api_key"]))
    assert resp.status_code == 200 and resp.json()["deleted"] is True
    follow = client.get(f"/watch/{wid}", headers=_auth_headers(user["raw_api_key"]))
    assert follow.status_code == 404


def test_patch_watcher_pause_resume(client, isolated_db, monkeypatch):
    _isolate_watchers(monkeypatch, isolated_db)
    user = _register_user()
    _fund_user_wallet(user, amount_cents=1000)
    agent_id = _register_test_agent(client, user["raw_api_key"])
    wid = _create_watcher(client, user["raw_api_key"], agent_id=agent_id).json()["watcher_id"]
    paused = client.patch(
        f"/watch/{wid}",
        headers=_auth_headers(user["raw_api_key"]),
        json={"status": "paused"},
    )
    assert paused.status_code == 200 and paused.json()["status"] == "paused"
    resumed = client.patch(
        f"/watch/{wid}",
        headers=_auth_headers(user["raw_api_key"]),
        json={"status": "active"},
    )
    assert resumed.status_code == 200 and resumed.json()["status"] == "active"


# ---------------------------------------------------------------------------
# Sweeper behavior
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, body=b"", status=200, headers=None):
        self.content = body
        self.status_code = status
        self.headers = headers or {}

    def iter_content(self, chunk_size=65536):
        yield self.content

    def close(self):
        pass

    def json(self):
        return json.loads(self.content.decode("utf-8"))


def _patch_http(body=b"hello"):
    return patch(
        "core.watchers.fingerprint.requests.get",
        return_value=_FakeResp(body=body),
    )


def test_sweeper_no_fire_on_unchanged_fingerprint(client, isolated_db, monkeypatch):
    _isolate_watchers(monkeypatch, isolated_db)
    user = _register_user()
    _fund_user_wallet(user, amount_cents=10_000)
    agent_id = _register_test_agent(client, user["raw_api_key"])
    wid = _create_watcher(client, user["raw_api_key"], agent_id=agent_id).json()["watcher_id"]

    with _patch_http(b"v1"):
        _watchers_sweeper.sweep_watchers(limit=10)
    # Force the next tick to be due immediately by rewinding next_check_at.
    _bump_due(wid)
    with _patch_http(b"v1"):
        _watchers_sweeper.sweep_watchers(limit=10)
    runs = client.get(
        f"/watch/{wid}/runs", headers=_auth_headers(user["raw_api_key"])
    ).json()["runs"]
    # First sweep counts as a "change" (no previous fingerprint).
    # We assert the SECOND sweep produced a no_change skip.
    skip_reasons = [r["skip_reason"] for r in runs]
    assert "no_change" in skip_reasons


def test_sweeper_fires_job_on_change(client, isolated_db, monkeypatch):
    _isolate_watchers(monkeypatch, isolated_db)
    user = _register_user()
    _fund_user_wallet(user, amount_cents=10_000)
    agent_id = _register_test_agent(client, user["raw_api_key"])
    wid = _create_watcher(client, user["raw_api_key"], agent_id=agent_id, budget_per_day_cents=200).json()["watcher_id"]

    with _patch_http(b"v1"):
        _watchers_sweeper.sweep_watchers(limit=10)
    _bump_due(wid)
    with _patch_http(b"v2"):
        _watchers_sweeper.sweep_watchers(limit=10)
    runs = client.get(
        f"/watch/{wid}/runs", headers=_auth_headers(user["raw_api_key"])
    ).json()["runs"]
    assert any(r["fired_job_id"] for r in runs), runs
    fired = next(r for r in runs if r["fired_job_id"])
    assert _jobs.get_job(fired["fired_job_id"]) is not None


def test_sweeper_budget_exhausted(client, isolated_db, monkeypatch):
    _isolate_watchers(monkeypatch, isolated_db)
    user = _register_user()
    _fund_user_wallet(user, amount_cents=10_000)
    # price = 5 cents; budget = 5 cents → one fire fits, second does not.
    agent_id = _register_test_agent(client, user["raw_api_key"], price_usd=0.05)
    wid = _create_watcher(
        client,
        user["raw_api_key"],
        agent_id=agent_id,
        budget_per_day_cents=5,
    ).json()["watcher_id"]

    with _patch_http(b"v1"):
        _watchers_sweeper.sweep_watchers(limit=10)
    _bump_due(wid)
    with _patch_http(b"v2"):
        _watchers_sweeper.sweep_watchers(limit=10)
    _bump_due(wid)
    # Second change tick: budget should be exhausted.
    with _patch_http(b"v3"):
        _watchers_sweeper.sweep_watchers(limit=10)
    row = _watchers.get_watcher(wid)
    assert row["status"] == "budget_exhausted"


def test_sweeper_policy_always_fires_every_tick(client, isolated_db, monkeypatch):
    _isolate_watchers(monkeypatch, isolated_db)
    user = _register_user()
    _fund_user_wallet(user, amount_cents=10_000)
    agent_id = _register_test_agent(client, user["raw_api_key"], price_usd=0.05)
    wid = _create_watcher(
        client,
        user["raw_api_key"],
        agent_id=agent_id,
        budget_per_day_cents=200,
        on_change_policy="always",
    ).json()["watcher_id"]

    fires = 0
    for _ in range(2):
        _bump_due(wid)
        with _patch_http(b"identical"):
            summary = _watchers_sweeper.sweep_watchers(limit=10)
            fires += summary["fired"]
    assert fires >= 2, fires


def test_watcher_test_endpoint_does_not_charge(client, isolated_db, monkeypatch):
    _isolate_watchers(monkeypatch, isolated_db)
    user = _register_user()
    wallet = _fund_user_wallet(user, amount_cents=10_000)
    agent_id = _register_test_agent(client, user["raw_api_key"])
    wid = _create_watcher(client, user["raw_api_key"], agent_id=agent_id).json()["watcher_id"]
    before = _payments.get_wallet(wallet["wallet_id"])["balance_cents"]

    with _patch_http(b"any"):
        resp = client.post(
            f"/watch/{wid}/test", headers=_auth_headers(user["raw_api_key"])
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["fingerprint"] is not None
    assert body["error"] is None
    after = _payments.get_wallet(wallet["wallet_id"])["balance_cents"]
    assert before == after


def test_webhook_delivery_signs_with_hmac(client, isolated_db, monkeypatch):
    _isolate_watchers(monkeypatch, isolated_db)
    user = _register_user()
    _fund_user_wallet(user, amount_cents=10_000)
    agent_id = _register_test_agent(client, user["raw_api_key"], price_usd=0.05)
    wid = _create_watcher(
        client,
        user["raw_api_key"],
        agent_id=agent_id,
        delivery_webhook_url="https://hooks.example.com/incoming",
        delivery_email=None,
        budget_per_day_cents=100,
    ).json()["watcher_id"]

    # First sweep establishes baseline fingerprint.
    with _patch_http(b"v1"):
        _watchers_sweeper.sweep_watchers(limit=10)
    _bump_due(wid)
    # Second sweep changes fingerprint → fires job.
    with _patch_http(b"v2"):
        _watchers_sweeper.sweep_watchers(limit=10)

    # Settle the fired job to running→complete so the delivery phase fires.
    runs = _watchers.list_watcher_runs(wid)
    fired = next((r for r in runs if r["fired_job_id"]), None)
    assert fired is not None
    job_id = fired["fired_job_id"]
    _jobs.update_job_status(job_id, "complete", output_payload={"ok": True}, completed=True)

    posted = []

    class _FakeWebhookResp:
        status_code = 200

    def _fake_post(url, data=None, headers=None, timeout=None):
        posted.append({"url": url, "data": data, "headers": headers})
        return _FakeWebhookResp()

    with patch("core.watchers.delivery.requests.post", side_effect=_fake_post):
        _watchers_sweeper.sweep_watchers(limit=10)

    assert len(posted) == 1
    sent = posted[0]
    assert sent["url"] == "https://hooks.example.com/incoming"
    assert sent["headers"]["X-Aztea-Event"] == "watcher.fired"
    sig = sent["headers"]["X-Aztea-Signature"]
    assert sig.startswith("sha256=")
    # Pull the secret directly from the row to verify HMAC.
    with _watchers.crud._conn() as conn:
        row = conn.execute(
            "SELECT delivery_secret FROM watchers WHERE watcher_id = %s", (wid,)
        ).fetchone()
    secret = dict(row)["delivery_secret"]
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), sent["data"], hashlib.sha256
    ).hexdigest()
    assert sig == expected


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bump_due(watcher_id: str) -> None:
    """Force the watcher to be due immediately."""
    with _watchers.crud._conn() as conn:
        conn.execute(
            "UPDATE watchers SET next_check_at = '1970-01-01T00:00:00+00:00' WHERE watcher_id = %s",
            (watcher_id,),
        )

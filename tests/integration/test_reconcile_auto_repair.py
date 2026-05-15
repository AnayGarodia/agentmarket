"""Tests for the auto_repair flag on POST /ops/payments/reconcile.

Scenarios covered:
  1. Without ?auto_repair=true the response is detect-only — drifted
     balance_cents on a wallet is reported but the cache is NOT rewritten.
  2. With ?auto_repair=true and below-threshold balance_cents drift the
     cache is rewritten and the wallet appears in wallets_repaired with
     axis="balance_cents".
  3. Same as (2) but for held_cents drift driven from an artificial
     wallet_holds row.
  4. With ?auto_repair=true and above-threshold drift the cache stays
     drifted and the wallet appears in wallets_skipped_above_threshold
     with the configured threshold.
  5. When one wallet's repair raises, the other wallets in the batch
     still repair; the failing wallet shows up in wallets_failed_repair.
  6. Auth still gates the endpoint — a non-admin key gets 403.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

os.environ.setdefault("API_KEY", "test-master-key")
os.environ.setdefault("SERVER_BASE_URL", "http://localhost:8000")

import pytest
from fastapi.testclient import TestClient

from core import auth, disputes, jobs, payments, registry, reputation
from core import cache as result_cache
from core import compare, pipelines
from core.payments import audit, holds
import server.application as server

from tests.integration.helpers import (
    TEST_MASTER_KEY,
    _auth_headers,
    _close_module_conn,
    _register_user,
)


# ---------------------------------------------------------------------------
# Per-test DB isolation — mirrors tests/integration/test_wallet_holds.py
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_db(monkeypatch):
    db_path = (
        Path(__file__).resolve().parent
        / f"test-reconcile-{uuid.uuid4().hex}.db"
    )
    modules = (
        registry, payments, auth, jobs, reputation, disputes,
        result_cache, compare, pipelines,
    )
    for module in modules:
        _close_module_conn(module)
        monkeypatch.setattr(module, "DB_PATH", str(db_path))

    from core import migrate
    migrate.apply_migrations(str(db_path))
    payments.init_payments_db()

    yield db_path

    for module in modules:
        _close_module_conn(module)
    for suffix in ("", "-shm", "-wal"):
        path = Path(f"{db_path}{suffix}")
        if path.exists():
            path.unlink()


@pytest.fixture
def client(isolated_db, monkeypatch):
    monkeypatch.setattr(server, "_MASTER_KEY", TEST_MASTER_KEY)
    with TestClient(server.app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wallet_with_balance(owner_id: str, deposit_cents: int) -> dict:
    """Create a wallet and fund it via the real deposit path so the ledger
    starts in sync. The test then drifts the cache directly to simulate the
    pathology auto_repair must fix.
    """
    wallet = payments.get_or_create_wallet(owner_id)
    payments.deposit(wallet["wallet_id"], deposit_cents, "test deposit")
    return payments.get_or_create_wallet(owner_id)


def _set_cached_balance(wallet_id: str, balance_cents: int) -> None:
    """Drift the cached balance_cents directly via SQL — simulates the bug
    auto_repair is meant to absorb."""
    with payments._conn() as conn:
        conn.execute(
            "UPDATE wallets SET balance_cents = %s WHERE wallet_id = %s",
            (balance_cents, wallet_id),
        )


def _set_cached_held(wallet_id: str, held_cents: int) -> None:
    with payments._conn() as conn:
        conn.execute(
            "UPDATE wallets SET held_cents = %s WHERE wallet_id = %s",
            (held_cents, wallet_id),
        )


def _insert_active_hold(wallet_id: str, amount_cents: int) -> None:
    """Insert an active hold WITHOUT touching wallets.held_cents — drift is
    the divergence between this active SUM and the wallets.held_cents cache."""
    now = _now()
    with payments._conn() as conn:
        conn.execute(
            """
            INSERT INTO wallet_holds (
                hold_id, wallet_id, job_id, amount_cents,
                created_at, hold_until, status
            ) VALUES (%s, %s, %s, %s, %s, %s, 'active')
            """,
            (
                str(uuid.uuid4()), wallet_id, str(uuid.uuid4()),
                amount_cents, now, now,
            ),
        )


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _read_balance(wallet_id: str) -> int:
    return int(payments.get_wallet(wallet_id)["balance_cents"])


def _read_held(wallet_id: str) -> int:
    return int(payments.get_wallet(wallet_id).get("held_cents") or 0)


# ---------------------------------------------------------------------------
# 1. detect-only baseline
# ---------------------------------------------------------------------------


def test_reconcile_without_auto_repair_is_detect_only(client):
    owner_id = f"user:{uuid.uuid4().hex[:8]}"
    wallet = _wallet_with_balance(owner_id, 500)
    # Drift the cache by 200 (overstated) without touching the ledger.
    _set_cached_balance(wallet["wallet_id"], 700)

    resp = client.post("/ops/payments/reconcile", headers=_auth_headers(TEST_MASTER_KEY))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["mismatch_count"] >= 1
    assert "wallets_repaired" not in body
    assert "wallets_skipped_above_threshold" not in body
    # Cache MUST NOT have been rewritten.
    assert _read_balance(wallet["wallet_id"]) == 700


# ---------------------------------------------------------------------------
# 2. below-threshold balance repair
# ---------------------------------------------------------------------------


def test_reconcile_with_auto_repair_repairs_below_threshold_balance(client):
    owner_id = f"user:{uuid.uuid4().hex[:8]}"
    wallet = _wallet_with_balance(owner_id, 500)
    # Drift by 500 cents — well below the 10000 default threshold.
    _set_cached_balance(wallet["wallet_id"], 1000)

    resp = client.post(
        "/ops/payments/reconcile?auto_repair=true",
        headers=_auth_headers(TEST_MASTER_KEY),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    repaired = body["wallets_repaired"]
    assert any(
        item["wallet_id"] == wallet["wallet_id"]
        and item["axis"] == audit.AXIS_BALANCE
        and item["drift_cents"] == 500
        for item in repaired
    ), repaired

    # The cache has been rewritten to match the ledger SUM (500).
    assert _read_balance(wallet["wallet_id"]) == 500
    # Above-threshold list is empty; threshold is preserved on the response.
    assert body["wallets_skipped_above_threshold"] == []
    assert body["threshold_cents"] == 10000


# ---------------------------------------------------------------------------
# 3. below-threshold held repair
# ---------------------------------------------------------------------------


def test_reconcile_with_auto_repair_repairs_below_threshold_held(client):
    owner_id = f"user:{uuid.uuid4().hex[:8]}"
    wallet = _wallet_with_balance(owner_id, 1000)
    # Insert an active hold of 250 cents; wallets.held_cents is still 0.
    _insert_active_hold(wallet["wallet_id"], 250)
    assert _read_held(wallet["wallet_id"]) == 0  # confirm drift

    resp = client.post(
        "/ops/payments/reconcile?auto_repair=true",
        headers=_auth_headers(TEST_MASTER_KEY),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    repaired = body["wallets_repaired"]
    assert any(
        item["wallet_id"] == wallet["wallet_id"]
        and item["axis"] == audit.AXIS_HELD
        and item["drift_cents"] == -250  # cached(0) - active(250)
        for item in repaired
    ), repaired

    # Cache should now equal SUM(active_holds) = 250.
    assert _read_held(wallet["wallet_id"]) == 250


# ---------------------------------------------------------------------------
# 4. above-threshold skip
# ---------------------------------------------------------------------------


def test_reconcile_with_auto_repair_skips_above_threshold(client):
    owner_id = f"user:{uuid.uuid4().hex[:8]}"
    wallet = _wallet_with_balance(owner_id, 500)
    # Drift by 50_000 cents — far above the 10_000 threshold.
    _set_cached_balance(wallet["wallet_id"], 50_500)

    resp = client.post(
        "/ops/payments/reconcile?auto_repair=true",
        headers=_auth_headers(TEST_MASTER_KEY),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    skipped = body["wallets_skipped_above_threshold"]
    target = next(
        (item for item in skipped if item["wallet_id"] == wallet["wallet_id"]),
        None,
    )
    assert target is not None, skipped
    assert target["axis"] == audit.AXIS_BALANCE
    assert target["drift_cents"] == 50_000
    assert target["threshold_cents"] == 10_000
    assert "exceeds AUTO_REPAIR_THRESHOLD_CENTS" in target["reason"]

    # Cache UNCHANGED — must not silently rewrite a >threshold delta.
    assert _read_balance(wallet["wallet_id"]) == 50_500
    # And the wallet must NOT also be in wallets_repaired.
    assert all(item["wallet_id"] != wallet["wallet_id"] for item in body["wallets_repaired"])


# ---------------------------------------------------------------------------
# 5. one failure does not block other repairs
# ---------------------------------------------------------------------------


def test_reconcile_auto_repair_one_failure_does_not_block_others(client, monkeypatch):
    a_owner = f"user:{uuid.uuid4().hex[:8]}"
    b_owner = f"user:{uuid.uuid4().hex[:8]}"
    a_wallet = _wallet_with_balance(a_owner, 500)
    b_wallet = _wallet_with_balance(b_owner, 800)
    _set_cached_balance(a_wallet["wallet_id"], 700)  # drift 200
    _set_cached_balance(b_wallet["wallet_id"], 900)  # drift 100

    # Patch repair so only the FIRST wallet in the batch fails.
    original = audit.repair_wallet_balance_cache
    seen: set[str] = set()

    def _flaky_repair(wallet_id: str) -> dict:
        if a_wallet["wallet_id"] not in seen:
            seen.add(a_wallet["wallet_id"])
            if wallet_id == a_wallet["wallet_id"]:
                raise RuntimeError("simulated repair failure for A")
        return original(wallet_id)

    monkeypatch.setattr(audit, "repair_wallet_balance_cache", _flaky_repair)

    resp = client.post(
        "/ops/payments/reconcile?auto_repair=true",
        headers=_auth_headers(TEST_MASTER_KEY),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    failed_ids = {item["wallet_id"] for item in body["wallets_failed_repair"]}
    repaired_ids = {item["wallet_id"] for item in body["wallets_repaired"]}
    assert a_wallet["wallet_id"] in failed_ids
    assert b_wallet["wallet_id"] in repaired_ids
    # A's failure includes the error message; B is not in the failure list.
    failed_a = next(item for item in body["wallets_failed_repair"]
                    if item["wallet_id"] == a_wallet["wallet_id"])
    assert "simulated repair failure" in failed_a["error"]

    # A's cache is still drifted; B's was repaired to ledger SUM.
    assert _read_balance(a_wallet["wallet_id"]) == 700
    assert _read_balance(b_wallet["wallet_id"]) == 800


# ---------------------------------------------------------------------------
# 6. auth still required
# ---------------------------------------------------------------------------


def test_reconcile_auto_repair_requires_admin_scope(client):
    caller = _register_user()
    resp = client.post(
        "/ops/payments/reconcile?auto_repair=true",
        headers=_auth_headers(caller["raw_api_key"]),
    )
    assert resp.status_code in (401, 403), resp.text

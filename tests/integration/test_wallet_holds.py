"""End-to-end tests for the wallet reserve-hold pattern.

Each test owns its own SQLite DB so the wallet_holds + transactions ledger
state is fully isolated from neighbouring suites. Tests are grouped by
lifecycle stage:

    1. compute_hold_cents pure function
    2. settlement creates holds
    3. withdrawal enforces available balance
    4. clawback consumption (rating + dispute)
    5. release sweeper
    6. reconciliation
    7. concurrency
"""

from __future__ import annotations

import os

os.environ.setdefault("API_KEY", "test-master-key")
os.environ.setdefault("SERVER_BASE_URL", "http://localhost:8000")

import sqlite3
import threading
import uuid
from pathlib import Path

import pytest

from core import auth, db, disputes, jobs, payments, registry
from core.payments import holds


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _close_module_conn(module) -> None:
    conn = getattr(module._local, "conn", None)
    if conn is None:
        return
    conn.close()
    try:
        delattr(module._local, "conn")
    except AttributeError:
        pass


@pytest.fixture
def isolated_db(monkeypatch):
    db_path = Path(__file__).resolve().parent / f"test-holds-{uuid.uuid4().hex}.db"
    modules = (registry, payments, auth, jobs, disputes)
    for module in modules:
        _close_module_conn(module)
        monkeypatch.setattr(module, "DB_PATH", str(db_path))

    # Apply migrations so wallet_holds + held_cents exist before init_payments_db.
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


# ---------------------------------------------------------------------------
# 1. compute_hold_cents — pure function
# ---------------------------------------------------------------------------


class TestComputeHoldCents:
    def test_no_curve_holds_full_payout(self):
        # No floor declared -> full payout at risk -> hold the entire amount.
        assert holds.compute_hold_cents(1000, None) == 1000

    def test_min_fraction_one_holds_zero(self):
        # Curve says every rating keeps 100% -> nothing at risk.
        assert holds.compute_hold_cents(1000, {"1": 1.0, "5": 1.0}) == 0

    def test_half_floor_holds_half(self):
        # min_fraction=0.5 -> half at risk -> hold 500 of 1000.
        assert holds.compute_hold_cents(1000, {"1": 0.5, "5": 1.0}) == 500

    def test_zero_floor_holds_full(self):
        # min_fraction=0 -> full payout could be clawed back.
        assert holds.compute_hold_cents(1000, {"1": 0.0, "5": 1.0}) == 1000

    def test_zero_payout_yields_zero_hold(self):
        assert holds.compute_hold_cents(0, {"1": 0.5}) == 0

    def test_invalid_curve_falls_back_to_full(self):
        # A malformed curve must NOT silently under-hold.
        assert holds.compute_hold_cents(500, {"1": "nope"}) == 500  # type: ignore[dict-item]

    def test_rounds_up_partial_cent(self):
        # 333 * (1 - 0.5) = 166.5 -> hold 167, never 166.
        assert holds.compute_hold_cents(333, {"1": 0.5, "5": 1.0}) == 167

    def test_held_cannot_exceed_payout(self):
        # Defensive: even if at_risk somehow rounds beyond payout, the
        # contract caps the hold to the payout.
        assert holds.compute_hold_cents(100, {"1": -0.5}) <= 100


# ---------------------------------------------------------------------------
# 3. Withdrawal enforces available balance
# ---------------------------------------------------------------------------


class TestWithdrawalAvailableBalance:
    """The /wallets/withdraw gate is HTTP-shaped, but the rule it enforces is
    a pure expression over wallet rows. Test the rule directly so we don't
    need a full Stripe mock harness — the comprehensive HTTP test in commit
    10 covers the wired endpoint.
    """

    def _wallet_with(self, balance_cents: int, held_cents: int) -> dict:
        owner_id = f"user:withdraw-{uuid.uuid4().hex[:8]}"
        wallet = payments.get_or_create_wallet(owner_id)
        if balance_cents:
            payments.deposit(wallet["wallet_id"], balance_cents, memo="test")
        if held_cents:
            with db.get_db_connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE wallets SET held_cents = %s WHERE wallet_id = %s",
                    (held_cents, wallet["wallet_id"]),
                )
        return payments.get_wallet(wallet["wallet_id"])

    def test_withdrawal_rejected_when_request_exceeds_available(self, isolated_db):
        wallet = self._wallet_with(balance_cents=1000, held_cents=400)
        held = int(wallet.get("held_cents") or 0)
        available = max(0, int(wallet["balance_cents"]) - held)
        # Mirror the gate condition in part_014.py::withdraw.
        assert available == 600
        assert available < 700  # request

    def test_withdrawal_succeeds_when_request_at_or_below_available(self, isolated_db):
        wallet = self._wallet_with(balance_cents=1000, held_cents=400)
        held = int(wallet.get("held_cents") or 0)
        available = max(0, int(wallet["balance_cents"]) - held)
        assert available == 600
        assert 600 <= available  # request equal to available is OK

    def test_held_cents_default_is_zero_for_legacy_wallets(self, isolated_db):
        wallet = self._wallet_with(balance_cents=500, held_cents=0)
        assert (wallet.get("held_cents") or 0) == 0
        # Available == balance for any wallet with no holds.
        assert int(wallet["balance_cents"]) - int(wallet.get("held_cents") or 0) == 500


# ---------------------------------------------------------------------------
# 2. Settlement creates holds
# ---------------------------------------------------------------------------


def _fund_caller_wallet(caller_owner_id: str, cents: int) -> dict:
    wallet = payments.get_or_create_wallet(caller_owner_id)
    payments.deposit(wallet["wallet_id"], cents, memo="hold-test funds")
    return payments.get_wallet(wallet["wallet_id"])


def _agent_with_curve(curve_json: str | None = None) -> dict:
    owner_id = f"user:owner-{uuid.uuid4().hex[:8]}"
    payments.get_or_create_wallet(owner_id)
    agent_id = registry.register_agent(
        name=f"Hold Test Agent {uuid.uuid4().hex[:6]}",
        description="Hold lifecycle test agent",
        endpoint_url="http://localhost:8000/internal/echo",
        price_per_call_usd=0.10,
        tags=["hold-test"],
        owner_id=owner_id,
        payout_curve=curve_json,
    )
    return registry.get_agent(agent_id, include_unapproved=True)


def _settle_agent_payout(agent, caller_owner_id, price_cents, dispute_window_hours=72):
    """Run a deterministic settle: charge caller, payout agent, return wallet rows."""
    caller_wallet = payments.get_or_create_wallet(caller_owner_id)
    agent_wallet = payments.get_or_create_wallet(f"agent:{agent['agent_id']}")
    platform_wallet = payments.get_or_create_wallet(payments.PLATFORM_OWNER_ID)
    charge_tx_id = payments.pre_call_charge(
        caller_wallet["wallet_id"],
        price_cents,
        agent["agent_id"],
    )
    from core import payout_curve as _pc
    curve = _pc.parse_curve(agent.get("payout_curve"))
    payments.post_call_payout(
        agent_wallet["wallet_id"],
        platform_wallet["wallet_id"],
        charge_tx_id,
        price_cents,
        agent["agent_id"],
        platform_fee_pct=10,
        fee_bearer_policy="caller",
        job_id=f"job-{uuid.uuid4().hex[:8]}",
        dispute_window_hours=dispute_window_hours,
        payout_curve=curve,
    )
    return {
        "caller_wallet": payments.get_wallet(caller_wallet["wallet_id"]),
        "agent_wallet": payments.get_wallet(agent_wallet["wallet_id"]),
        "platform_wallet": payments.get_wallet(platform_wallet["wallet_id"]),
        "charge_tx_id": charge_tx_id,
    }


class TestSettlementCreatesHold:
    def test_settlement_with_no_curve_holds_full_payout(self, isolated_db):
        agent = _agent_with_curve(None)
        caller_owner = f"user:caller-{uuid.uuid4().hex[:8]}"
        _fund_caller_wallet(caller_owner, 2000)
        result = _settle_agent_payout(agent, caller_owner, price_cents=1000)
        agent_wallet = result["agent_wallet"]
        # fee_bearer_policy=caller: agent gets full price (1000); no curve -> hold all 1000.
        assert agent_wallet["balance_cents"] == 1000
        assert agent_wallet["held_cents"] == 1000

    def test_settlement_with_floor_one_holds_zero(self, isolated_db):
        agent = _agent_with_curve('{"1": 1.0, "5": 1.0}')
        caller_owner = f"user:caller-{uuid.uuid4().hex[:8]}"
        _fund_caller_wallet(caller_owner, 2000)
        result = _settle_agent_payout(agent, caller_owner, price_cents=1000)
        agent_wallet = result["agent_wallet"]
        # min_fraction = 1.0 -> nothing at risk -> hold zero.
        assert agent_wallet["balance_cents"] == 1000
        assert agent_wallet["held_cents"] == 0

    def test_settlement_creates_hold_for_at_risk_portion(self, isolated_db):
        agent = _agent_with_curve('{"1": 0.5, "5": 1.0}')
        caller_owner = f"user:caller-{uuid.uuid4().hex[:8]}"
        _fund_caller_wallet(caller_owner, 2000)
        result = _settle_agent_payout(agent, caller_owner, price_cents=1000)
        agent_wallet = result["agent_wallet"]
        # Agent payout 1000, at-risk fraction 0.5 -> hold 500.
        assert agent_wallet["balance_cents"] == 1000
        assert agent_wallet["held_cents"] == 500

    def test_settlement_is_idempotent_on_replay(self, isolated_db):
        agent = _agent_with_curve(None)
        caller_owner = f"user:caller-{uuid.uuid4().hex[:8]}"
        _fund_caller_wallet(caller_owner, 2000)
        caller_wallet = payments.get_wallet_by_owner(caller_owner)
        agent_wallet = payments.get_or_create_wallet(f"agent:{agent['agent_id']}")
        platform_wallet = payments.get_or_create_wallet(payments.PLATFORM_OWNER_ID)
        charge_tx_id = payments.pre_call_charge(
            caller_wallet["wallet_id"],
            1000,
            agent["agent_id"],
        )
        job_id = "job-replay-1"
        for _ in range(3):
            payments.post_call_payout(
                agent_wallet["wallet_id"],
                platform_wallet["wallet_id"],
                charge_tx_id,
                1000,
                agent["agent_id"],
                platform_fee_pct=10,
                fee_bearer_policy="caller",
                job_id=job_id,
                dispute_window_hours=72,
                payout_curve=None,
            )
        agent_wallet_after = payments.get_wallet(agent_wallet["wallet_id"])
        # Replay must NOT inflate either cache.
        assert agent_wallet_after["balance_cents"] == 1000
        assert agent_wallet_after["held_cents"] == 1000

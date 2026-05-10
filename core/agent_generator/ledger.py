"""Money helpers for the vibe-an-agent generation flow.

# OWNS: pre-charge of the per-request budget cap; refund-on-failure;
#       compensating refund of unused budget after a successful run.
# NOT OWNS: settlement of the agent's normal call lifecycle (that's the
#       existing core.payments.base.post_call_payout path), or the
#       agents/hosted_skills tables.
# INVARIANTS:
#   - Integer cents only.  No float() in this file.
#   - Charge agent_id is a synthetic constant ('platform:agent_generation')
#     because the platform itself is the seller of the generation service.
#     This keeps reconciliation tidy: every charge has a consistent
#     `agent_id` for filtering.
"""

from __future__ import annotations

from core.payments import base as _payments

# Synthetic agent_id used in transactions.agent_id for generation charges.
# Keeps `SELECT * FROM transactions WHERE agent_id = 'platform:agent_generation'`
# a one-line audit query for the abuse runbook.
GENERATION_CHARGE_AGENT_ID = "platform:agent_generation"


def precharge_for_generation(
    *,
    caller_wallet_id: str,
    max_cents: int,
    charged_by_key_id: str | None,
) -> str:
    """Debit the caller's wallet by ``max_cents`` and return the charge_tx_id.

    Using the existing pre_call_charge primitive keeps the double-settlement
    guard, balance check, and audit trail consistent with the rest of the
    money path.  No new ledger row types are introduced.
    """
    if max_cents <= 0:
        raise ValueError("max_cents must be positive.")
    return _payments.pre_call_charge(
        caller_wallet_id,
        int(max_cents),
        GENERATION_CHARGE_AGENT_ID,
        charged_by_key_id=charged_by_key_id,
    )


def refund_full(
    *,
    caller_wallet_id: str,
    charge_tx_id: str,
    max_cents: int,
) -> None:
    """Refund the entire pre-charge — used on terminal failure paths
    (safety_block, near_clone, self_test_exhausted, internal_error).
    """
    if max_cents <= 0:
        return
    _payments.post_call_refund(
        caller_wallet_id,
        charge_tx_id,
        int(max_cents),
        GENERATION_CHARGE_AGENT_ID,
    )


def refund_unused(
    *,
    caller_wallet_id: str,
    charge_tx_id: str,
    max_cents: int,
    actual_cents: int,
) -> int:
    """On success, post a compensating refund for ``max_cents - actual_cents``.

    Returns the cents actually refunded (0 when actual >= max). The refund is
    idempotent at the ``charge_tx_id`` level because ``post_call_refund``
    skips if a payout already exists; for a generation charge there is never
    an offsetting payout, so this is the only compensating entry.
    """
    if actual_cents < 0 or max_cents < 0:
        raise ValueError("actual_cents and max_cents must be non-negative.")
    delta = max(0, int(max_cents) - int(actual_cents))
    if delta == 0:
        return 0
    _payments.post_call_refund(
        caller_wallet_id,
        charge_tx_id,
        delta,
        GENERATION_CHARGE_AGENT_ID,
    )
    return delta

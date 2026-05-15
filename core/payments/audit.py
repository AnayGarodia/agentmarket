"""Ledger audit helpers.

These utilities are intentionally narrow: they expose the wallet-balance-cache
invariant directly so tests, ops scripts, and repair tooling can reason about
drift without duplicating SQL in multiple places.
"""

from __future__ import annotations

import logging
from typing import Callable

from core import db as _db
from core.logging_utils import log_event as _log_event

from .base import _conn
from .holds import repair_wallet_held_cache as _repair_held

_LOG = logging.getLogger(__name__)

# Axis tags used in the auto-repair response so an operator can tell at a
# glance which cache moved. Kept as module-level constants so tests and
# log assertions consume the same strings.
AXIS_BALANCE = "balance_cents"
AXIS_HELD = "held_cents"


def _wallet_balance_snapshot_conn(conn: _db.DbConnection, wallet_id: str) -> dict:
    row = conn.execute(
        """
        SELECT
            w.wallet_id,
            w.owner_id,
            w.balance_cents AS cached_balance_cents,
            COALESCE(w.held_cents, 0) AS cached_held_cents,
            COALESCE(SUM(t.amount_cents), 0) AS ledger_balance_cents
        FROM wallets w
        LEFT JOIN transactions t ON t.wallet_id = w.wallet_id
        WHERE w.wallet_id = %s
        GROUP BY w.wallet_id
        """,
        (wallet_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Wallet '{wallet_id}' not found.")
    cached = int(row["cached_balance_cents"] or 0)
    ledger = int(row["ledger_balance_cents"] or 0)
    cached_held = int(row["cached_held_cents"] or 0)
    held_row = conn.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0) AS active_held_cents
        FROM wallet_holds
        WHERE wallet_id = %s AND status = 'active'
        """,
        (wallet_id,),
    ).fetchone()
    active_held = int(held_row["active_held_cents"] or 0) if held_row is not None else 0
    held_drift = cached_held - active_held
    return {
        "wallet_id": str(row["wallet_id"]),
        "owner_id": str(row["owner_id"]),
        "cached_balance_cents": cached,
        "ledger_balance_cents": ledger,
        "drift_cents": cached - ledger,
        "invariant_ok": cached == ledger and held_drift == 0,
        # Reserve-hold pattern (PR #wallet_holds): the wallets.held_cents
        # cache must match SUM(amount_cents WHERE status='active') for the
        # wallet. Drift on this axis indicates a hold lifecycle bug or a
        # manual UPDATE that bypassed holds.py.
        "cached_held_cents": cached_held,
        "active_held_cents": active_held,
        "held_drift_cents": held_drift,
    }


def get_wallet_balance_snapshot(wallet_id: str) -> dict:
    """Return cached-vs-ledger balance information for one wallet."""
    with _conn() as conn:
        return _wallet_balance_snapshot_conn(conn, wallet_id)


def repair_wallet_balance_cache(wallet_id: str) -> dict:
    """Rewrite one wallet's cached balance from the ledger-derived total.

    This is a repair tool, not a normal write path. We keep it explicit and
    narrow so operator tooling can fix a drifted cache without touching the
    insert-only transaction ledger.
    """
    with _conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        snapshot = _wallet_balance_snapshot_conn(conn, wallet_id)
        if snapshot["drift_cents"] != 0:
            conn.execute(
                "UPDATE wallets SET balance_cents = %s WHERE wallet_id = %s",
                (snapshot["ledger_balance_cents"], wallet_id),
            )
        return _wallet_balance_snapshot_conn(conn, wallet_id)


# ---------------------------------------------------------------------------
# Auto-repair driver — called by POST /ops/payments/reconcile?auto_repair=1
# ---------------------------------------------------------------------------


def _drift_from_balance_row(row: dict) -> int:
    """Cached - ledger drift for a balance_cents mismatch row."""
    return int(row.get("balance_cents") or 0) - int(row.get("ledger_balance_cents") or 0)


def _drift_from_held_row(row: dict) -> int:
    """Cached - active drift for a held_cents mismatch row."""
    return int(row.get("held_cents") or 0) - int(row.get("active_held_cents") or 0)


def _partition_mismatches(
    rows: list[dict],
    *,
    axis: str,
    threshold_cents: int,
    drift_fn: Callable[[dict], int],
) -> tuple[list[dict], list[dict]]:
    """Pure: split a mismatch list into (below_threshold, above_threshold) tuples.

    Each output item is normalised to ``{wallet_id, axis, drift_cents}`` so
    downstream consumers don't need to remember the original mismatch shape.
    """
    below: list[dict] = []
    above: list[dict] = []
    for row in rows:
        wallet_id = str(row.get("wallet_id") or "")
        if not wallet_id:
            continue
        drift = drift_fn(row)
        item = {"wallet_id": wallet_id, "axis": axis, "drift_cents": drift}
        if abs(drift) <= threshold_cents:
            below.append(item)
        else:
            above.append({**item, "threshold_cents": threshold_cents,
                          "reason": "drift exceeds AUTO_REPAIR_THRESHOLD_CENTS"})
    return below, above


def _apply_repair(
    target: dict,
    repair_fn: Callable[[str], dict],
) -> tuple[bool, str | None]:
    """Run one repair function in its own transaction. Returns (ok, err_msg).

    Per-wallet atomicity comes from the repair helpers themselves
    (each opens its own BEGIN IMMEDIATE). On exception we log a structured
    failed event and let the caller surface the error in wallets_failed_repair
    without aborting the rest of the batch.
    """
    wallet_id = target["wallet_id"]
    try:
        repair_fn(wallet_id)
    except Exception as exc:  # noqa: BLE001 — per-wallet isolation by design
        _log_event(_LOG, logging.WARNING, "reconcile.auto_repair.failed", {
            "wallet_id": wallet_id,
            "axis": target["axis"],
            "drift_cents": target["drift_cents"],
            "err": str(exc),
        })
        return False, str(exc)
    _log_event(_LOG, logging.INFO, "reconcile.auto_repair.applied", {
        "wallet_id": wallet_id,
        "axis": target["axis"],
        "drift_cents": target["drift_cents"],
    })
    return True, None


def auto_repair_reconciliation_summary(
    summary: dict,
    *,
    threshold_cents: int,
    balance_repair_fn: Callable[[str], dict] | None = None,
    held_repair_fn: Callable[[str], dict] | None = None,
) -> dict:
    """Repair below-threshold drift; report skipped/failed cases per wallet.

    Why a helper instead of inlining in the route: keeps the FastAPI shard
    short, lets tests monkey-patch the repair functions to simulate failures,
    and gives parallel future sessions (other axes) a clear extension point.

    Args:
        summary: the dict returned by ``record_reconciliation_run``.
        threshold_cents: max |drift| auto-fixed (above-threshold drift is
            skipped and reported for human review).
        balance_repair_fn / held_repair_fn: optional injectables so tests can
            simulate a partial failure mid-batch. Default to the module's
            production helpers.

    Returns a dict with ``wallets_checked``, ``wallets_drifted``,
    ``wallets_repaired``, ``wallets_skipped_above_threshold``, and
    ``wallets_failed_repair``. The route merges this with the existing
    summary so the existing ``mismatches`` / ``held_mismatches`` fields stay
    backwards-compatible.
    """
    balance_repair_fn = balance_repair_fn or repair_wallet_balance_cache
    held_repair_fn = held_repair_fn or _repair_held

    balance_below, balance_above = _partition_mismatches(
        summary.get("mismatches") or [],
        axis=AXIS_BALANCE,
        threshold_cents=threshold_cents,
        drift_fn=_drift_from_balance_row,
    )
    held_below, held_above = _partition_mismatches(
        summary.get("held_mismatches") or [],
        axis=AXIS_HELD,
        threshold_cents=threshold_cents,
        drift_fn=_drift_from_held_row,
    )

    repaired: list[dict] = []
    failed: list[dict] = []
    for target in balance_below:
        ok, err = _apply_repair(target, balance_repair_fn)
        if ok:
            repaired.append(target)
        else:
            failed.append({**target, "error": err})
    for target in held_below:
        ok, err = _apply_repair(target, held_repair_fn)
        if ok:
            repaired.append(target)
        else:
            failed.append({**target, "error": err})

    skipped = balance_above + held_above
    for target in skipped:
        _log_event(_LOG, logging.WARNING, "reconcile.auto_repair.skipped", {
            "wallet_id": target["wallet_id"],
            "axis": target["axis"],
            "drift_cents": target["drift_cents"],
            "threshold_cents": threshold_cents,
        })

    drifted_wallets = {
        item["wallet_id"]
        for item in (balance_below + balance_above + held_below + held_above)
    }
    return {
        "wallets_checked": int(summary.get("wallet_count") or 0),
        "wallets_drifted": len(drifted_wallets),
        "wallets_repaired": repaired,
        "wallets_skipped_above_threshold": skipped,
        "wallets_failed_repair": failed,
        "threshold_cents": threshold_cents,
    }

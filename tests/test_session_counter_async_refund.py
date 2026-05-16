"""Unit tests for the session-counter async refund reconciliation helper.

Audit 2026-05-16 #19: refunds triggered after the inline call returns (by
the sweeper or dispute lifecycle) did not decrement the MCP session
counter, so it lagged the wallet-ledger total. The helper added here is
idempotent per job_id so polling cannot double-decrement.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SDK_ROOT = Path(__file__).resolve().parent.parent / "sdks" / "python-sdk"
if str(_SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(_SDK_ROOT))


def test_session_reconcile_async_refund_decrements_once_per_job_id():
    from aztea.mcp.server import _session_reconcile_async_refund

    state = {"spent_cents": 51}
    _session_reconcile_async_refund(state, "job-abc", 16)
    assert state["spent_cents"] == 35

    # Polling the same job again must not double-decrement.
    _session_reconcile_async_refund(state, "job-abc", 16)
    assert state["spent_cents"] == 35


def test_session_reconcile_async_refund_handles_distinct_jobs():
    from aztea.mcp.server import _session_reconcile_async_refund

    state = {"spent_cents": 50}
    _session_reconcile_async_refund(state, "job-1", 10)
    _session_reconcile_async_refund(state, "job-2", 5)
    assert state["spent_cents"] == 35


def test_session_reconcile_async_refund_clamps_at_zero():
    from aztea.mcp.server import _session_reconcile_async_refund

    state = {"spent_cents": 3}
    _session_reconcile_async_refund(state, "job-x", 100)
    assert state["spent_cents"] == 0


def test_session_reconcile_async_refund_noop_on_missing_args():
    from aztea.mcp.server import _session_reconcile_async_refund

    state = {"spent_cents": 10}
    _session_reconcile_async_refund(state, None, 5)
    _session_reconcile_async_refund(state, "job-y", None)
    _session_reconcile_async_refund(state, "job-y", "not-a-number")
    assert state["spent_cents"] == 10

"""Regression tests for the 2026-05-18 cleanup sprint (P0/P1 fixes).

Each test pins a behaviour that previously regressed or was incomplete.
Tests are deliberately small and code-source-anchored where the full
runtime is too heavy to spin up.

NOTE: server/application_parts/part_*.py are shards that share one
namespace assembled by server/application.py — they CANNOT be imported
standalone. Tests import the assembled module via ``server.application``.
"""

from __future__ import annotations

import re
from pathlib import Path


# ---------------------------------------------------------------------------
# C3 — hire_async wall-clock budget is separate from the sync 8s budget.
# Previously regressed: the async worker dispatched through the sync budget
# table, so hire_async inherited the 8s sync cap and refunded long jobs.
# ---------------------------------------------------------------------------


def test_c3_async_default_budget_is_at_least_300s():
    """The async tier must be measured in minutes, not seconds."""
    from server import application as server

    assert server._AGENT_WALL_BUDGET_ASYNC_DEFAULT_SECONDS >= 300.0, (
        f"async default budget is "
        f"{server._AGENT_WALL_BUDGET_ASYNC_DEFAULT_SECONDS}s — must be "
        "at least 300s per the work order (suggested minimum)"
    )


def test_c3_async_budget_distinct_from_sync_budget():
    """The two budget tables must not share the same default constant."""
    from server import application as server

    assert (
        server._AGENT_WALL_BUDGET_ASYNC_DEFAULT_SECONDS
        > server._AGENT_WALL_BUDGET_DEFAULT_SECONDS
    ), "async budget must exceed sync budget"
    assert server._AGENT_WALL_BUDGET_ASYNC_OVERRIDES is not (
        server._AGENT_WALL_BUDGET_OVERRIDES
    ), "override tables must be separate dicts"


def test_c3_resolve_wall_budget_picks_async_table_for_async_mode():
    """Helper must return the async table value when execution_mode='async'."""
    from server import application as server

    fake_agent = "00000000-0000-0000-0000-deadbeef0001"
    sync = server._resolve_wall_budget(fake_agent, "sync")
    asyn = server._resolve_wall_budget(fake_agent, "async")
    assert sync == server._AGENT_WALL_BUDGET_DEFAULT_SECONDS
    assert asyn == server._AGENT_WALL_BUDGET_ASYNC_DEFAULT_SECONDS
    assert asyn > sync


def test_c3_resolve_wall_budget_honors_overrides_per_mode():
    """Per-agent overrides must apply within their own mode's table."""
    from server import application as server

    overridden = next(iter(server._AGENT_WALL_BUDGET_OVERRIDES))
    sync = server._resolve_wall_budget(overridden, "sync")
    asyn = server._resolve_wall_budget(overridden, "async")
    assert sync == server._AGENT_WALL_BUDGET_OVERRIDES[overridden]
    # If the agent has an async override, it should be honored; otherwise
    # it falls back to the async default — either way, never less than sync.
    assert asyn >= sync


def test_c3_async_worker_passes_execution_mode_async():
    """The async worker loop must thread execution_mode='async' through."""
    src = Path("server/application_parts/part_004.py").read_text()
    # The call site inside the worker loop should pass
    # execution_mode="async". Match across newlines.
    pattern = re.compile(
        r"_execute_builtin_agent\(.*?execution_mode\s*=\s*[\"']async[\"']",
        re.DOTALL,
    )
    assert pattern.search(src), (
        "async worker loop must pass execution_mode='async' to "
        "_execute_builtin_agent — otherwise the sync budget bleeds in"
    )


def test_c3_hire_async_tool_description_documents_async_budget():
    """The hire_async tool description must state the actual async budget."""
    src = Path("sdks/python-sdk/aztea/mcp/meta_tools.py").read_text()
    # Locate the aztea_hire_async tool block.
    idx = src.find('"name": "aztea_hire_async"')
    assert idx >= 0, "aztea_hire_async tool block must exist"
    block = src[idx : idx + 4000]
    assert "minutes" in block, (
        "hire_async description must explicitly mention the budget in minutes"
    )
    assert "600" in block or "10 minutes" in block, (
        "hire_async description must reference the async default budget"
    )


# ---------------------------------------------------------------------------
# A1 / A2 — cold-start agents (zero traffic) no longer report
# success_rate=1.0. Previously misleading: broken endpoints with
# success_rate=1.0 / 0 calls outranked battle-tested agents.
# ---------------------------------------------------------------------------


def test_a1_a2_cold_start_success_rate_is_none_not_one():
    """A zero-traffic agent must surface success_rate=None, not 1.0."""
    from core.registry import core_schema

    row = {
        "total_calls": 0,
        "successful_calls": 0,
        "healthcheck_url": None,
        "output_verifier_url": None,
        "verified": 0,
        "internal_only": 0,
        "review_note": None,
        "reviewed_at": None,
        "reviewed_by": None,
        "trust_decay_multiplier": 1.0,
        "last_decay_at": "1970-01-01",
        "model_provider": None,
        "model_id": None,
        "pii_safe": 0,
        "outputs_not_stored": 0,
        "audit_logged": 0,
        "region_locked": None,
    }
    out = core_schema._row_to_dict(row)
    assert out["success_rate"] is None, (
        "cold-start agents must surface success_rate=None — 1.0 with zero "
        "calls misled buyers comparing trust signals"
    )
    assert out["has_call_history"] is False


def test_a1_a2_warm_agent_success_rate_still_computed():
    """Warm agents (total_calls>0) must still report a real success_rate."""
    from core.registry import core_schema

    row = {
        "total_calls": 10,
        "successful_calls": 9,
        "healthcheck_url": None,
        "output_verifier_url": None,
        "verified": 0,
        "internal_only": 0,
        "review_note": None,
        "reviewed_at": None,
        "reviewed_by": None,
        "trust_decay_multiplier": 1.0,
        "last_decay_at": "1970-01-01",
        "model_provider": None,
        "model_id": None,
        "pii_safe": 0,
        "outputs_not_stored": 0,
        "audit_logged": 0,
        "region_locked": None,
    }
    out = core_schema._row_to_dict(row)
    assert out["success_rate"] == 0.9
    assert out["has_call_history"] is True

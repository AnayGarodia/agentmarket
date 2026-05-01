"""Unit tests for the pure threshold-evaluation rules in scripts/launch_alerts.

These tests do not hit the network — they cover the synthesis logic that turns
metrics dicts into Alert records. The HTTP collector is verified by the
production smoke harness instead.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest


def _load_alerts_module():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(here, "scripts", "launch_alerts.py")
    spec = importlib.util.spec_from_file_location("launch_alerts", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["launch_alerts"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def alerts_mod():
    return _load_alerts_module()


def test_failed_call_spike_below_warn_returns_empty(alerts_mod):
    out = alerts_mod.evaluate_failed_call_spike(
        {"total_today": 200, "failed_today": 4}, alerts_mod.Thresholds()
    )
    assert out == []


def test_failed_call_spike_warn(alerts_mod):
    out = alerts_mod.evaluate_failed_call_spike(
        {"total_today": 100, "failed_today": 10}, alerts_mod.Thresholds()
    )
    assert len(out) == 1
    assert out[0].name == "failed_call_spike"
    assert out[0].severity == "warn"


def test_failed_call_spike_critical(alerts_mod):
    out = alerts_mod.evaluate_failed_call_spike(
        {"total_today": 100, "failed_today": 30}, alerts_mod.Thresholds()
    )
    assert len(out) == 1
    assert out[0].severity == "critical"
    assert out[0].detail["pct"] == 0.30


def test_failed_call_spike_skips_small_samples(alerts_mod):
    out = alerts_mod.evaluate_failed_call_spike(
        {"total_today": 5, "failed_today": 5}, alerts_mod.Thresholds()
    )
    assert out == []


def test_refund_spike_warn_and_critical(alerts_mod):
    t = alerts_mod.Thresholds()
    warn = alerts_mod.evaluate_refund_spike({"charges_cents_today": 1000, "refunds_cents_today": 150}, t)
    crit = alerts_mod.evaluate_refund_spike({"charges_cents_today": 1000, "refunds_cents_today": 300}, t)
    assert len(warn) == 1 and warn[0].severity == "warn"
    assert len(crit) == 1 and crit[0].severity == "critical"


def test_ledger_drift_critical_on_mismatch(alerts_mod):
    out = alerts_mod.evaluate_ledger_drift({"drift_cents": 0, "mismatch_count": 2}, alerts_mod.Thresholds())
    assert len(out) == 1 and out[0].severity == "critical"


def test_ledger_drift_clean(alerts_mod):
    out = alerts_mod.evaluate_ledger_drift({"drift_cents": 0, "mismatch_count": 0}, alerts_mod.Thresholds())
    assert out == []


def test_degraded_agents_filters_by_volume(alerts_mod):
    agents = [
        {"name": "low_volume", "success_rate": 0.20, "total_calls": 3},
        {"name": "bad_agent", "success_rate": 0.40, "total_calls": 50},
        {"name": "warn_agent", "success_rate": 0.70, "total_calls": 50},
        {"name": "ok_agent", "success_rate": 0.99, "total_calls": 100},
    ]
    out = alerts_mod.evaluate_degraded_agents(agents, alerts_mod.Thresholds())
    by_name = {a.detail["agent"]: a for a in out}
    assert "low_volume" not in by_name  # below min sample
    assert by_name["bad_agent"].severity == "critical"
    assert by_name["warn_agent"].severity == "warn"
    assert "ok_agent" not in by_name


def test_search_empty_flags_zero_result_queries(alerts_mod):
    out = alerts_mod.evaluate_search_empty({"good_query": 5, "broken_query": 0})
    assert len(out) == 1
    assert out[0].detail["query"] == "broken_query"


def test_worker_backlog_thresholds(alerts_mod):
    t = alerts_mod.Thresholds()
    assert alerts_mod.evaluate_worker_backlog({"pending": 10}, t) == []
    warn = alerts_mod.evaluate_worker_backlog({"pending": 30}, t)
    crit = alerts_mod.evaluate_worker_backlog({"pending": 200}, t)
    assert len(warn) == 1 and warn[0].severity == "warn"
    assert len(crit) == 1 and crit[0].severity == "critical"


def test_evaluate_all_aggregates_alerts(alerts_mod):
    metrics = {
        "jobs": {"total_today": 200, "failed_today": 50, "pending": 200},
        "payments": {"charges_cents_today": 10_000, "refunds_cents_today": 3_000},
        "reconcile": {"drift_cents": 500, "mismatch_count": 0},
        "agents": [{"name": "broken", "success_rate": 0.30, "total_calls": 50}],
        "search_probes": {"lint python code": 0},
    }
    out = alerts_mod.evaluate_all(metrics)
    severities = {a.name: a.severity for a in out}
    assert severities["failed_call_spike"] == "critical"
    assert severities["refund_spike"] == "critical"
    assert severities["ledger_drift"] == "critical"
    assert severities["degraded_agent"] == "critical"
    assert severities["search_empty"] == "warn"
    assert severities["worker_backlog"] == "critical"

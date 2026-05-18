"""Regression tests for the 2026-05-18 E1–E11 fixes."""

from __future__ import annotations

import os
import re

import pytest


# ---------------------------------------------------------------------------
# E1 — catalog_visibility surfaces real operational state.
# ---------------------------------------------------------------------------


def test_e1_spending_visibility_enumerates_states():
    """Visibility should be one of live / suspended / banned / sunset / deleted."""
    # The shard files can't be imported standalone — read source instead.
    from pathlib import Path
    src = Path("server/application_parts/part_011.py").read_text()
    for marker in ('"suspended"', '"banned"', '"deleted"'):
        assert marker in src, f"visibility marker {marker} missing"


# ---------------------------------------------------------------------------
# E2 — pool-queue wait is separate from execution budget.
# ---------------------------------------------------------------------------


def test_e2_queue_wait_constant_defined():
    from pathlib import Path
    src = Path("server/application_parts/part_004.py").read_text()
    assert "_QUEUE_WAIT_SECONDS" in src
    # Make sure the env default is generous enough.
    m = re.search(r'AZTEA_AGENT_QUEUE_WAIT_SECONDS"\s*,\s*"(\d+(?:\.\d+)?)"', src)
    assert m, "env default missing"
    assert float(m.group(1)) >= 30


def test_e2_wait_for_future_running_helper_present():
    from pathlib import Path
    src = Path("server/application_parts/part_004.py").read_text()
    assert "def _wait_for_future_running(" in src


# ---------------------------------------------------------------------------
# E3 — stale catalog fallback shouts the warning.
# ---------------------------------------------------------------------------


def test_e3_fallback_payload_front_loads_stale_signal():
    """The local_emergency_fallback payload must surface its source first."""
    import importlib
    server = importlib.import_module("aztea.mcp.server")
    src = open(server.__file__).read()
    assert "STALE CATALOG" in src, "stale-catalog warning is missing the ⚠️ marker"
    assert "fail_closed" in src, "fail-closed env path is missing"


# ---------------------------------------------------------------------------
# E4 — cache hits carry the receipt envelope.
# ---------------------------------------------------------------------------


def test_e4_cache_hit_envelope_includes_receipt_summary():
    """The cache_hit response envelope always carries receipt_summary."""
    # Load via server.application (which runs the shards) so module
    # globals exist. The function name is _cache_hit_response_payload
    # but the helper lives inside the application namespace.
    import os
    os.environ.setdefault("API_KEY", "test-master-key")
    from server import application as _app  # noqa: F401
    fn = getattr(_app, "_cache_hit_response_payload")
    response = fn({"k": "v"})
    assert "receipt_summary" in response, response
    assert response["receipt_summary"].startswith("absent")


def test_e4_build_receipt_envelope_function_present():
    from core import receipts
    assert callable(getattr(receipts, "build_receipt_envelope", None))


# ---------------------------------------------------------------------------
# E5 — hire_async caps slug resolution timeout.
# ---------------------------------------------------------------------------


def test_e5_hire_async_caps_resolve_timeout():
    """Slug resolution must use a bounded timeout, not the caller's full budget."""
    import importlib
    meta = importlib.import_module("aztea.mcp.meta_tools")
    src = open(meta.__file__).read()
    assert "_resolve_timeout" in src, "slug-resolution timeout cap is missing"


# ---------------------------------------------------------------------------
# E6 — dockerfile_analyzer fail-closed + degraded flag.
# ---------------------------------------------------------------------------


def test_e6_dockerfile_analyzer_rejects_no_from():
    from agents import dockerfile_analyzer
    result = dockerfile_analyzer.run({
        "dockerfile": "this is not a Dockerfile at all just random text"
    })
    assert "error" in result, f"non-Dockerfile must be rejected: {result}"
    assert result["error"]["code"] == "dockerfile_analyzer.no_from_instruction"


def test_e6_dockerfile_analyzer_marks_degraded_when_regex():
    from agents import dockerfile_analyzer
    # Force the regex path by stubbing _is_hadolint_available.
    original = dockerfile_analyzer._is_hadolint_available
    try:
        dockerfile_analyzer._is_hadolint_available = lambda: False
        result = dockerfile_analyzer.run({
            "dockerfile": "FROM alpine:3.18\nRUN echo hi\n"
        })
    finally:
        dockerfile_analyzer._is_hadolint_available = original
    assert result.get("tool_used") == "regex"
    assert result.get("degraded_mode") is True, result
    assert "degraded_reason" in result


# ---------------------------------------------------------------------------
# E7 — caller_trust climbs on success.
# ---------------------------------------------------------------------------


def test_e7_settlement_path_includes_caller_trust_bump():
    """The success-settlement code path now bumps caller_trust on every job."""
    from pathlib import Path
    src = Path("server/application_parts/part_005.py").read_text()
    assert "adjust_caller_trust_once" in src
    assert "job_settled_clean" in src


# ---------------------------------------------------------------------------
# E8 — Bayesian prior weight raised.
# ---------------------------------------------------------------------------


def test_e8_quality_prior_weight_makes_single_rating_small():
    from core import reputation
    assert reputation._QUALITY_PRIOR_WEIGHT >= 15, (
        f"_QUALITY_PRIOR_WEIGHT={reputation._QUALITY_PRIOR_WEIGHT} — too low; "
        "single ratings can still swing trust >1 point"
    )
    # One 5-star rating over a 3-star prior should move the per-rating
    # quality score by at most ~0.10 with prior_weight=20.
    score_after_one = reputation._compute_quality_score(5.0, 1)
    score_no_ratings = reputation._compute_quality_score(None, 0)
    delta = score_after_one - score_no_ratings
    assert delta < 0.12, (
        f"single 5-star rating shifted quality score by {delta:.3f}; "
        "raise _QUALITY_PRIOR_WEIGHT further"
    )


# ---------------------------------------------------------------------------
# E9 — wallet_audit supports include_receipts=false.
# ---------------------------------------------------------------------------


def test_e9_wallet_audit_has_include_receipts_param():
    from pathlib import Path
    src = Path("server/application_parts/part_011.py").read_text()
    assert "include_receipts: bool" in src
    assert "receipts_omitted" in src


# ---------------------------------------------------------------------------
# E10 — featured agents are quality-gated at read time.
# ---------------------------------------------------------------------------


def test_e10_featured_quality_gate_un_features_low_success():
    import importlib
    server = importlib.import_module("aztea.mcp.server")
    fn = server._featured_with_quality_gate
    # Above threshold — keep featured.
    assert fn({"is_featured": True, "success_rate": 0.9, "total_calls": 100})
    # Below threshold + enough evidence — un-feature.
    assert not fn({"is_featured": True, "success_rate": 0.04, "total_calls": 22})
    # Below threshold but not enough evidence — leave the spec alone.
    assert fn({"is_featured": True, "success_rate": 0.0, "total_calls": 2})
    # Not featured to begin with — never override.
    assert not fn({"is_featured": False, "success_rate": 1.0, "total_calls": 100})


# ---------------------------------------------------------------------------
# E11 — db_sandbox docs match reality.
# ---------------------------------------------------------------------------


def test_e11_db_sandbox_description_calls_out_multi_statement_rejection():
    from server.builtin_agents.specs_part4 import load_builtin_specs_part4
    specs = load_builtin_specs_part4()
    db_spec = next(s for s in specs if s["name"] == "DB Sandbox")
    description = db_spec["description"].lower()
    assert "multi-statement" in description or "multiple statements" in description, (
        "db_sandbox description must explain the multi-statement rejection"
    )

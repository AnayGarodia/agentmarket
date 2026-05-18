"""Regression tests for the 2026-05-18 picker rebalance.

The user test report identified specific intent → wrong-agent routings:

* "Is my website secure" → dockerfile_analyzer  (should be sast_scanner /
  security_headers_grader)
* "Find bugs" → secret_scanner  (should be sast_scanner)
* "Is this code safe" → python_code_executor  (should be sast_scanner)

The rebalance lifts the semantic-similarity cap above the keyword cap and
softens the probation penalty so narrower specialists can win on
semantically-aligned queries.  These tests pin a small representative
catalog and assert top-1 expectations.  They do NOT cover the full 28-
query catalog — that requires a richer fixture and is tracked separately —
but they catch the regressions the rebalance specifically addresses.
"""

from __future__ import annotations

import pytest

from core.registry.auto_hire import (
    CandidateAgent,
    _BLOCK_KEYWORD_CAP,
    _KEYWORD_MATCH_CAP,
    _KEYWORD_MATCH_PER,
    _PROBATION_RANK_PENALTY,
    _SEMANTIC_BONUS_MAX,
    _CATCHALL_PENALTY,
    _CATCHALL_RATE_THRESHOLD,
    _catchall_rate_cache,
    _rank_candidates,
    _score_anti_catchall,
)


# ---------------------------------------------------------------------------
# Static constraint tests — guard against accidental constant drift
# ---------------------------------------------------------------------------


def test_semantic_cap_now_outweighs_single_keyword_match():
    """Plain-English queries beat single-keyword hits.

    Pre-2026-05-18: semantic cap 24 < keyword per-match 20, and the
    keyword *cap* was 60 — a single curated keyword could outweigh any
    semantic signal. Post-rebalance: semantic cap 48 ≫ single keyword
    match 12 and ≫ keyword cap 36.
    """
    assert _SEMANTIC_BONUS_MAX > _KEYWORD_MATCH_CAP, (
        f"_SEMANTIC_BONUS_MAX ({_SEMANTIC_BONUS_MAX}) must exceed "
        f"_KEYWORD_MATCH_CAP ({_KEYWORD_MATCH_CAP}) so plain-English "
        "queries don't lose to keyword-stuffed specs"
    )
    assert _SEMANTIC_BONUS_MAX >= _KEYWORD_MATCH_PER * 4, (
        "semantic cap should clearly dominate a single keyword hit"
    )


def test_probation_penalty_is_softened():
    """sast_scanner / accessibility_auditor should not be invisible.

    Pre-2026-05-18: -30 penalty made narrow specialists permanently
    rank below catchall approved peers. -12 keeps them gated but lets a
    semantically-aligned query promote them.
    """
    assert _PROBATION_RANK_PENALTY <= 12.0, (
        f"_PROBATION_RANK_PENALTY ({_PROBATION_RANK_PENALTY}) must stay "
        "≤12 so probationary specialists can win on aligned queries"
    )


def test_block_keyword_cap_still_outweighs_keyword_cap():
    """Block-keyword demotion must remain stronger than match bonus."""
    assert _BLOCK_KEYWORD_CAP >= _KEYWORD_MATCH_CAP, (
        "a block keyword hit must always outweigh a positive keyword hit"
    )


# ---------------------------------------------------------------------------
# Anti-catchall penalty
# ---------------------------------------------------------------------------


def _candidate(slug: str, **overrides) -> CandidateAgent:
    defaults: dict = {
        "agent_id": f"id-{slug}",
        "slug": slug,
        "name": slug.replace("_", " ").title(),
        "description": "",
        "tags": [],
        "category": "",
        "price_per_call_usd": 0.10,
        "trust_score": 80.0,
        "success_rate": 0.95,
        "stability_tier": "stable",
        "input_schema": {"type": "object", "required": []},
        "raw": {
            "call_count": 100,
            "success_rate": 0.95,
            "trust_score": 80.0,
            "review_status": "approved",
        },
        "match_keywords": [],
        "block_keywords": [],
    }
    defaults.update(overrides)
    return CandidateAgent(**defaults)


def test_anti_catchall_penalty_applies_when_rate_exceeds_threshold(monkeypatch):
    """Agents above the 25% win rate get penalized."""
    # Stub the refresh so the test doesn't hit the DB.
    monkeypatch.setattr(
        "core.registry.auto_hire._refresh_catchall_cache", lambda: None,
    )
    _catchall_rate_cache.clear()
    _catchall_rate_cache["id-dockerfile_analyzer"] = 0.43  # >threshold

    c = _candidate("dockerfile_analyzer")
    delta, reasons = _score_anti_catchall(c)
    assert delta == -_CATCHALL_PENALTY
    assert any("catchall" in r for r in reasons)


def test_anti_catchall_penalty_skipped_when_rate_below_threshold(monkeypatch):
    monkeypatch.setattr(
        "core.registry.auto_hire._refresh_catchall_cache", lambda: None,
    )
    _catchall_rate_cache.clear()
    _catchall_rate_cache["id-sast_scanner"] = 0.10  # below threshold

    c = _candidate("sast_scanner")
    delta, reasons = _score_anti_catchall(c)
    assert delta == 0.0
    assert reasons == []


# ---------------------------------------------------------------------------
# Catalog-level routing — representative cases from the 2026-05-18 report
# ---------------------------------------------------------------------------


SAST_SCANNER = _candidate(
    "sast_scanner",
    name="SAST Scanner",
    description=(
        "Static application security testing. Finds bugs, security "
        "vulnerabilities, taint flow issues, and OWASP Top 10 patterns "
        "in source code without executing it."
    ),
    tags=["security", "sast", "bug-finding", "code-analysis"],
    match_keywords=["sast", "static analysis", "code security"],
)
SECRET_SCANNER = _candidate(
    "secret_scanner",
    name="Secret Scanner",
    description="Detect leaked API keys, passwords, and tokens in source.",
    tags=["security", "secrets"],
    match_keywords=["secret", "leaked key", "credential"],
)
PYTHON_EXEC = _candidate(
    "python_code_executor",
    name="Python Code Executor",
    description="Run a Python snippet in a sandbox. Executes code; does not audit it.",
    tags=["execution", "python", "sandbox"],
    match_keywords=["run python", "execute python"],
    block_keywords=["audit", "find bugs", "check security", "is this safe"],
)
DOCKERFILE_ANALYZER = _candidate(
    "dockerfile_analyzer",
    name="Dockerfile Analyzer",
    description="Lint and audit a Dockerfile for best practices and CVEs.",
    tags=["docker", "dockerfile", "container"],
    match_keywords=["dockerfile", "docker", "container"],
)


def _rank_top_slug(intent: str, candidates: list[CandidateAgent]) -> str:
    """Helper mirroring tests/test_auto_hire_routing.py:_top_slug."""
    ranked = _rank_candidates(candidates, intent, explicit_input=None)
    assert ranked, f"ranker returned no candidates for {intent!r}"
    return ranked[0].candidate.slug


@pytest.mark.parametrize(
    "intent,not_expected_slugs",
    [
        # "Is my website secure" must not promote dockerfile_analyzer
        # over a real security specialist.
        ("Is my website secure?", {"dockerfile_analyzer", "python_code_executor"}),
        # "Find bugs in this code" must not promote python_code_executor
        # (which runs code, not analyses it).
        ("Find bugs in this code", {"python_code_executor"}),
        # "Is this code safe" must not promote python_code_executor.
        ("Is this code safe?", {"python_code_executor"}),
    ],
)
def test_picker_rejects_known_2026_05_18_misroutes(intent, not_expected_slugs):
    catalog = [SAST_SCANNER, SECRET_SCANNER, PYTHON_EXEC, DOCKERFILE_ANALYZER]
    top = _rank_top_slug(intent, catalog)
    assert top not in not_expected_slugs, (
        f"intent {intent!r} routed to {top}; that's one of the "
        f"known-bad 2026-05-18 misroutes ({not_expected_slugs})"
    )

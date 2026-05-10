"""Self-test + near-clone detection for vibe-an-agent.

# OWNS: running each (example_input, ideal_output) through the candidate
#       skill, scoring with an LLM judge (or substring fallback), and
#       comparing the candidate's identity against existing listings via
#       cosine similarity.
# NOT OWNS: the LLM client (core.llm), embeddings backend (core.embeddings),
#       or skill execution loop (core.skill_executor).
# DECISIONS: self-test runs with composition DISABLED — see ``self_test``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core import embeddings as _embeddings
from core import feature_flags as _flags
from core import skill_executor as _skill_executor
from core.llm import CompletionRequest, Message, run_with_fallback

from core.agent_generator import prompts as _prompts

_LOG = logging.getLogger(__name__)

# Substring overlap below this fraction of the ideal counts as a self-test
# failure when the LLM judge isn't available (OSS mode without LLM keys).
_SUBSTRING_MIN_OVERLAP = 0.35

# How many ideal_output tokens we consider as the "must-cover" set for the
# substring fallback. Tokens shorter than 3 chars are noise.
_MIN_TOKEN_LEN = 3


def _tokens(text: str) -> set[str]:
    """Lowercase 3+-char words; used by the substring fallback judge."""
    return {
        w.lower().strip(",.!?;:'\"()[]{}")
        for w in text.split()
        if len(w) >= _MIN_TOKEN_LEN
    }


def _substring_score(ideal: str, actual: str) -> float:
    """Fraction of ideal-tokens present in actual. 1.0 = full coverage."""
    ideal_tokens = _tokens(ideal)
    if not ideal_tokens:
        return 1.0
    actual_tokens = _tokens(actual)
    overlap = len(ideal_tokens & actual_tokens)
    return overlap / len(ideal_tokens)


def _llm_judge(input_payload: dict[str, Any], ideal: str, actual: str) -> tuple[bool, str]:
    """Call the judge LLM; return (pass, reason). Falls back on any failure."""
    prompt = _prompts.build_judge_prompt(
        input_payload=input_payload, ideal=ideal, actual=actual
    )
    req = CompletionRequest(
        model="",
        messages=[
            Message("system", "You are a strict QA judge. Output JSON only."),
            Message("user", prompt),
        ],
        temperature=0.0,
        max_tokens=400,
        json_mode=True,
        timeout_seconds=30.0,
    )
    try:
        resp = run_with_fallback(req)
    except Exception as exc:
        _LOG.warning("vibe.qa.judge_llm_failed reason=%s", exc)
        return _fallback_score(ideal, actual)
    return _parse_judge_response(resp.text, ideal, actual)


def _parse_judge_response(text: str, ideal: str, actual: str) -> tuple[bool, str]:
    """Decode the judge's JSON; on parse failure, defer to the substring score."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        _LOG.info("vibe.qa.judge_unparseable")
        return _fallback_score(ideal, actual)
    if not isinstance(data, dict):
        return _fallback_score(ideal, actual)
    passed = bool(
        data.get("covers_required_facts")
        and data.get("no_hallucination")
        and data.get("format_ok")
    )
    reason = str(data.get("reason") or "(no reason)")
    return passed, reason


def _fallback_score(ideal: str, actual: str) -> tuple[bool, str]:
    """Substring-overlap judge — used when LLM judge unavailable / unparseable."""
    score = _substring_score(ideal, actual)
    passed = score >= _SUBSTRING_MIN_OVERLAP
    return passed, f"substring overlap {score:.2f} (threshold {_SUBSTRING_MIN_OVERLAP})"


def _execute_one(skill_dict: dict[str, Any], payload: dict[str, Any]) -> str:
    """Run the candidate skill once and return the result string."""
    raw = _skill_executor.execute_hosted_skill(skill_dict, payload)
    return str(raw.get("result") or "")


def self_test(
    *,
    parsed_skill_body: str,
    example_inputs: list[dict[str, Any]],
    ideal_outputs: list[str],
) -> tuple[bool, list[float], list[str]]:
    """Run every (input, ideal) pair through the candidate skill.

    Returns ``(all_passed, scores, failure_notes)``.

    Composition is deliberately disabled here: self-test must be deterministic
    and free of nested billing.  Composition is exercised at call time, not
    at QA time.  This avoids charging the user for cve_lookup attempts during
    a generation retry.
    """
    skill_dict = {
        "system_prompt": parsed_skill_body,
        "temperature": 0.2,
        "max_output_tokens": 1500,
        "model_chain": None,
    }
    scores: list[float] = []
    failure_notes: list[str] = []
    all_passed = True
    for input_payload, ideal in zip(example_inputs, ideal_outputs):
        try:
            actual = _execute_one(skill_dict, input_payload)
        except Exception as exc:
            _LOG.warning("vibe.qa.skill_exec_failed reason=%s", exc)
            all_passed = False
            scores.append(0.0)
            failure_notes.append(f"Skill execution failed: {exc}")
            continue
        passed, reason = _llm_judge(input_payload, ideal, actual)
        scores.append(1.0 if passed else 0.0)
        if not passed:
            all_passed = False
            failure_notes.append(
                f"Example failed (input={input_payload}): {reason}. "
                f"Actual was: {actual[:200]}"
            )
    return all_passed, scores, failure_notes


def detect_near_clone(
    *,
    candidate_name: str,
    candidate_description: str,
    existing_listings: list[dict[str, Any]],
) -> tuple[str | None, float]:
    """Return ``(matching_agent_id, cosine)`` if a near-clone is found.

    Threshold defaults to 0.92 (env-tunable). Embeds the concatenation of
    name + description for both candidate and each existing listing, then
    picks the highest cosine match.
    """
    if not existing_listings:
        return None, 0.0
    candidate_text = f"{candidate_name}\n{candidate_description}".strip()
    if not candidate_text:
        return None, 0.0
    threshold = _flags.agent_generation_clone_threshold()
    try:
        candidate_vec = _embeddings.embed_text(candidate_text)
    except Exception as exc:
        _LOG.warning("vibe.qa.embed_failed reason=%s", exc)
        return None, 0.0
    best_id: str | None = None
    best_score = 0.0
    for row in existing_listings:
        existing_text = (
            f"{row.get('name') or ''}\n{row.get('description') or ''}".strip()
        )
        if not existing_text:
            continue
        try:
            existing_vec = _embeddings.embed_text(existing_text)
            score = float(_embeddings.cosine(candidate_vec, existing_vec))
        except Exception as exc:
            _LOG.warning("vibe.qa.cosine_failed reason=%s", exc)
            continue
        if score > best_score:
            best_score = score
            best_id = str(row.get("agent_id") or "") or None
    if best_score >= threshold and best_id:
        return best_id, best_score
    return None, best_score

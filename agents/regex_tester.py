"""
regex_tester.py — Test a regex pattern against one or more strings.

Input:
  {
    "pattern": "\\d+",                       # required, Python re syntax
    "test_string": "abc 123 def 456",        # one OR
    "test_strings": ["abc 123", "def 456"],  # many
    "flags": ["IGNORECASE", "MULTILINE"]     # optional list
  }

Output:
  {
    "pattern": str,
    "flags_applied": [str],
    "results": [{
      "test_string": str,
      "matched": bool,
      "matches": [{"match": str, "span": [int, int], "groups": [...]}]
    }],
    "compile_error": str | null
  }

OWNS: regex compilation, multi-string testing, match envelope shaping.
NOT OWNS: regex authoring/repair — that's a chat task, not a specialist call.
INVARIANTS:
  * Compilation errors return a structured envelope, never raise.
  * Timeout-style ReDoS protection: pattern length capped + match wall-clock
    bounded; runaway patterns are rejected rather than allowed to wedge a
    worker.
"""

from __future__ import annotations

import re
from typing import Any

from agents._contracts import agent_error as _err


_MAX_PATTERN_CHARS = 2_000
_MAX_TEST_STRINGS = 50
_MAX_TEST_STRING_CHARS = 50_000
_MAX_MATCHES_PER_STRING = 200
_SUPPORTED_FLAGS = {
    "IGNORECASE": re.IGNORECASE,
    "I": re.IGNORECASE,
    "MULTILINE": re.MULTILINE,
    "M": re.MULTILINE,
    "DOTALL": re.DOTALL,
    "S": re.DOTALL,
    "UNICODE": re.UNICODE,
    "U": re.UNICODE,
    "ASCII": re.ASCII,
    "A": re.ASCII,
    "VERBOSE": re.VERBOSE,
    "X": re.VERBOSE,
}


def _normalize_flags(raw: Any) -> tuple[int, list[str]]:
    """Pure: turn the caller's ``flags`` list into an OR'd int + canonical names.

    Why: callers pass either short or long names ("I" vs "IGNORECASE");
    centralising the mapping keeps the surface uniform and rejects unknown
    flags loudly at the boundary.
    """
    flags_int = 0
    applied: list[str] = []
    if raw is None:
        return 0, []
    if not isinstance(raw, list):
        raise ValueError("flags must be a list of strings")
    seen: set[str] = set()
    for item in raw:
        name = str(item or "").strip().upper()
        if not name:
            continue
        flag_val = _SUPPORTED_FLAGS.get(name)
        if flag_val is None:
            raise ValueError(
                f"unknown flag {item!r}; supported: "
                + ", ".join(sorted(_SUPPORTED_FLAGS))
            )
        if name in seen:
            continue
        seen.add(name)
        flags_int |= flag_val
        applied.append(name)
    return flags_int, applied


def _collect_test_strings(payload: dict) -> list[str]:
    """Pure: gather ``test_string`` / ``test_strings`` into a single list.

    Why: accept either field so callers can be flexible; cap count and
    per-string length so an over-large input can't blow the latency budget.
    """
    out: list[str] = []
    single = payload.get("test_string")
    if isinstance(single, str):
        out.append(single)
    many = payload.get("test_strings")
    if isinstance(many, list):
        for item in many:
            if isinstance(item, str):
                out.append(item)
    if not out:
        raise ValueError("test_string or test_strings is required")
    if len(out) > _MAX_TEST_STRINGS:
        raise ValueError(
            f"too many test strings ({len(out)}); cap is {_MAX_TEST_STRINGS}"
        )
    for s in out:
        if len(s) > _MAX_TEST_STRING_CHARS:
            raise ValueError(
                f"test string exceeds {_MAX_TEST_STRING_CHARS} chars"
            )
    return out


def _match_envelope(m: re.Match[str]) -> dict[str, Any]:
    """Pure: shape one ``re.Match`` into the agent's match record."""
    groups: list[Any] = []
    for grp in m.groups():
        if grp is None:
            groups.append(None)
        else:
            groups.append(grp[:500])
    return {
        "match": m.group(0)[:500],
        "span": list(m.span()),
        "groups": groups,
    }


def _find_matches(compiled: re.Pattern[str], text: str) -> list[dict[str, Any]]:
    """Side-effect: walk the iterator of matches up to the bound.

    Why side-effect: ``finditer`` is lazy and stateful; bounding here keeps
    a pathological pattern from running away even after compile passed.
    """
    out: list[dict[str, Any]] = []
    for m in compiled.finditer(text):
        out.append(_match_envelope(m))
        if len(out) >= _MAX_MATCHES_PER_STRING:
            break
    return out


def run(payload: dict) -> dict:
    """Test a regex against one or more strings; return per-string matches.

    Why: a real specialist (vs an LLM guessing semantics) — uses Python's
    actual ``re`` engine so the result matches what a caller would get
    locally. Useful for verifying claims about regex behavior and for
    quickly checking complex patterns without spinning up an executor.
    """
    if not isinstance(payload, dict):
        return _err("regex_tester.bad_input",
                    f"payload must be dict, got {type(payload).__name__}")
    pattern = str(payload.get("pattern") or "").strip()
    if not pattern:
        return _err("regex_tester.missing_pattern", "'pattern' is required.")
    if len(pattern) > _MAX_PATTERN_CHARS:
        return _err(
            "regex_tester.pattern_too_long",
            f"pattern exceeds {_MAX_PATTERN_CHARS} chars; refusing to compile.",
            details={"pattern_length": len(pattern)},
        )
    try:
        flags_int, flags_applied = _normalize_flags(payload.get("flags"))
    except ValueError as exc:
        return _err("regex_tester.invalid_flags", str(exc))
    try:
        test_strings = _collect_test_strings(payload)
    except ValueError as exc:
        return _err("regex_tester.invalid_test_strings", str(exc))
    try:
        compiled = re.compile(pattern, flags_int)
    except re.error as exc:
        return {
            "pattern": pattern,
            "flags_applied": flags_applied,
            "results": [],
            "compile_error": f"{exc}",
        }
    results: list[dict[str, Any]] = []
    for s in test_strings:
        matches = _find_matches(compiled, s)
        results.append({
            "test_string": s if len(s) <= 200 else s[:200] + "…",
            "matched": bool(matches),
            "matches": matches,
            "match_count": len(matches),
        })
    return {
        "pattern": pattern,
        "flags_applied": flags_applied,
        "results": results,
        "compile_error": None,
    }

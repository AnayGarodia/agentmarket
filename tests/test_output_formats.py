"""Unit tests for core.output_formats renderers.

Each format renderer must:
- never raise on unexpected shapes (fall back to generic JSON code-fence)
- produce deterministic output for stable inputs
- recognize the well-known agent output shapes
"""
from __future__ import annotations

import json

from core import output_formats as F


_CODE_REVIEW_SAMPLE = {
    "score": 65,
    "summary": "Two bugs and a missing null check.",
    "severity_counts": {"critical": 0, "high": 1, "medium": 2, "low": 0},
    "issues": [
        {
            "severity": "high",
            "category": "bugs",
            "title": "off-by-one in slicing",
            "file": "core/x.py",
            "line": 42,
            "suggestion": "Use range(n) not range(n-1)",
        },
        {"severity": "medium", "title": "magic number 86400"},
    ],
    "positive_aspects": ["good test coverage on hot paths"],
}

_LINTER_SAMPLE = {
    "findings": [
        {"severity": "warning", "rule": "F401", "file": "x.py", "line": 1, "message": "unused import os"},
        {"severity": "error", "rule": "E501", "file": "x.py", "line": 88, "message": "line too long"},
    ],
    "total": 2,
}

_TYPE_CHECK_SAMPLE = {
    "diagnostics": [
        {"file": "core/x.py", "line": 12, "code": "arg-type", "message": "Argument 1 incompatible"},
    ],
    "passed": False,
    "total": 1,
}

_DEP_AUDIT_SAMPLE = {
    "vulnerabilities": [
        {"severity": "high", "package": "requests", "cve_id": "CVE-2024-0001", "fix_version": "2.32.0"},
    ],
}

_GIT_DIFF_SAMPLE = {
    "file_count": 2,
    "summary": "Two-file change touching auth.",
    "risk_summary": {"auth_changes": 1, "tests_removed": False, "secret_pattern_added": False},
    "files": [
        {"path": "auth/login.py", "change_type": "modified", "added": 12, "removed": 3, "risk_tags": ["auth"]},
    ],
}


def test_normalize_format_recognises_aliases():
    assert F.normalize_format("md") == "markdown"
    assert F.normalize_format("github") == "github_pr_comment"
    assert F.normalize_format("github-pr-comment") == "github_pr_comment"
    assert F.normalize_format("slack") == "slack_blocks"
    assert F.normalize_format("plain") == "text"
    assert F.normalize_format("PLAINTEXT") == "text"
    assert F.normalize_format("") is None
    assert F.normalize_format("   ") is None
    assert F.normalize_format("nonsense") is None
    assert F.normalize_format(None) is None


def test_render_json_is_passthrough():
    assert F.render({"x": 1}, format="json") == {"x": 1}


def test_render_markdown_for_code_review():
    out = F.render(_CODE_REVIEW_SAMPLE, format="markdown")
    assert isinstance(out, str)
    assert "## Code Review" in out
    assert "Score:" in out
    assert "off-by-one" in out
    assert "Use range(n) not range(n-1)" in out


def test_render_github_pr_comment_adds_verdict_header():
    out = F.render(_CODE_REVIEW_SAMPLE, format="github_pr_comment")
    assert isinstance(out, str)
    # Verdict header is the first non-comment line.
    assert "<!-- aztea: high -->" in out
    assert "review before merge" in out.lower()
    # The full markdown body is included.
    assert "## Code Review" in out


def test_render_pr_verdict_clean_when_no_issues():
    out = F.render(
        {"score": 95, "summary": "nothing concerning", "severity_counts": {"critical": 0, "high": 0}, "issues": []},
        format="github_pr_comment",
    )
    assert "Looks good" in out


def test_render_text_strips_markdown_fences_and_separators():
    out = F.render(_CODE_REVIEW_SAMPLE, format="text")
    assert "**" not in out
    # No markdown table separator rows
    assert "| --- |" not in out
    assert "off-by-one" in out


def test_render_slack_returns_blocks_dict():
    out = F.render(_CODE_REVIEW_SAMPLE, format="slack_blocks")
    assert isinstance(out, dict)
    assert "blocks" in out
    assert isinstance(out["blocks"], list)
    types = {b["type"] for b in out["blocks"]}
    # Real Block Kit: header + section/context/divider, NOT one mrkdwn blob.
    assert "header" in types
    assert "section" in types
    # Header text or one of the sections must mention the kind.
    blob = " ".join(
        (b.get("text", {}).get("text") if isinstance(b.get("text"), dict) else "")
        or " ".join(e.get("text", "") for e in b.get("elements", []) if isinstance(e, dict))
        for b in out["blocks"]
    )
    assert "Code Review" in blob


def test_render_linter_shape():
    out = F.render(_LINTER_SAMPLE, format="markdown")
    assert "## Linter" in out
    assert "F401" in out
    assert "unused import os" in out


def test_render_type_check_shape():
    out = F.render(_TYPE_CHECK_SAMPLE, format="markdown")
    assert "## Type Check" in out
    assert "arg-type" in out


def test_render_dep_audit_shape():
    out = F.render(_DEP_AUDIT_SAMPLE, format="markdown")
    assert "## Dependency Audit" in out
    assert "CVE-2024-0001" in out


def test_render_git_diff_shape():
    out = F.render(_GIT_DIFF_SAMPLE, format="markdown")
    assert "## Diff Risk Profile" in out
    assert "auth/login.py" in out
    assert "auth_changes: 1" in out


def test_render_pipeline_combines_stages():
    pipeline_output = {
        "step_results": {
            "analyze": {"output": _GIT_DIFF_SAMPLE},
            "review": {"output": _CODE_REVIEW_SAMPLE},
        }
    }
    out = F.render(pipeline_output, format="markdown")
    assert "## Pipeline Result" in out
    assert "Stage `analyze`" in out
    assert "Stage `review`" in out
    assert "off-by-one" in out
    assert "auth/login.py" in out


def test_render_unknown_shape_falls_back_to_json_block():
    weird = {"totally": "unknown", "shape": [1, 2, 3]}
    out = F.render(weird, format="markdown")
    assert "```json" in out
    parsed = json.loads(out.split("```json")[1].split("```")[0].strip())
    assert parsed == weird


def test_render_never_raises_on_garbage():
    # Strings, ints, lists — none of these should crash the renderer.
    assert isinstance(F.render("plain string", format="markdown"), str)
    assert isinstance(F.render([1, 2, 3], format="markdown"), str)
    assert isinstance(F.render(None, format="markdown"), str)
    assert isinstance(F.render({}, format="markdown"), str)

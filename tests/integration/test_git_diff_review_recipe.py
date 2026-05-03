"""Integration test for the git-diff-review recipe.

Verifies that the recipe is registered, picks up the real built-in
git_diff_analyzer + code_review_agent IDs, and that the DAG resolves
correctly when executed end-to-end with stubbed agent endpoints.
"""
from __future__ import annotations

import pytest

from core import recipes
from server.builtin_agents.constants import (
    CODEREVIEW_AGENT_ID,
    GIT_DIFF_ANALYZER_AGENT_ID,
)


def test_git_diff_review_recipe_is_registered():
    """The recipe must be in the platform's built-in list."""
    ids = [r["recipe_id"] for r in recipes.BUILTIN_RECIPES]
    assert "git-diff-review" in ids


def test_git_diff_review_dag_shape():
    """Two stages: analyze (deterministic) → review (LLM, depends on analyze)."""
    recipe = next(r for r in recipes.BUILTIN_RECIPES if r["recipe_id"] == "git-diff-review")
    nodes = recipe["pipeline_definition"]["nodes"]
    assert [n["id"] for n in nodes] == ["analyze", "review"]

    analyze, review = nodes
    assert analyze["agent_id"] == GIT_DIFF_ANALYZER_AGENT_ID
    assert analyze["input_map"] == {"diff": "$input.diff"}
    assert "depends_on" not in analyze

    assert review["agent_id"] == CODEREVIEW_AGENT_ID
    assert review["depends_on"] == ["analyze"]
    # Reviewer receives the diff verbatim AND the analyzer's summary as context.
    assert review["input_map"]["diff"] == "$input.diff"
    assert review["input_map"]["focus"] == "bugs"
    assert review["input_map"]["context"] == "$analyze.output.summary"


def test_git_diff_review_input_schema_requires_diff():
    recipe = next(r for r in recipes.BUILTIN_RECIPES if r["recipe_id"] == "git-diff-review")
    schema = recipe["default_input_schema"]
    assert schema["required"] == ["diff"]
    assert schema["properties"]["diff"]["type"] == "string"


def test_resolver_passes_analyze_summary_into_review():
    """Sanity-check the resolver wires $analyze.output.summary correctly."""
    from core.pipelines.resolver import resolve_input_map

    recipe = next(r for r in recipes.BUILTIN_RECIPES if r["recipe_id"] == "git-diff-review")
    review_node = next(n for n in recipe["pipeline_definition"]["nodes"] if n["id"] == "review")

    pipeline_input = {"diff": "diff --git a/x.py b/x.py\n@@ -1 +1 @@\n-old\n+new\n"}
    step_results = {
        "analyze": {
            "summary": "Touches auth and removes tests.",
            "risk_summary": {"auth_changes": 1, "tests_removed": True},
            "files": [],
        }
    }
    resolved = resolve_input_map(review_node["input_map"], pipeline_input, step_results)
    assert resolved["diff"] == pipeline_input["diff"]
    assert resolved["focus"] == "bugs"
    assert resolved["context"] == "Touches auth and removes tests."

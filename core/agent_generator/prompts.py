"""LLM prompts for the vibe-an-agent generation flow.

# OWNS: rendering the system / user / judge prompts and a compact catalog
#       summary of approved agents.
# NOT OWNS: LLM dispatch (core.llm.run_with_fallback), persistence, settlement.
# DECISIONS: catalog is filtered to review_status='approved' only — never bias
#       the generator toward other probation listings; doing so would let
#       low-quality near-clones pull each other up.
"""

from __future__ import annotations

from typing import Any

# Hard ceiling on catalog rows we render into the prompt — beyond this the
# context blows past most provider context windows. Sorted by trust/calls
# upstream so truncation drops the long tail, not the top performers.
MAX_CATALOG_ROWS = 40


_SYSTEM_PROMPT_PREFIX = """\
You are an expert agent designer for the Aztea marketplace. You write \
SKILL.md files: a Markdown document with YAML frontmatter that an LLM \
runtime executes verbatim as its system prompt.

Your goals:
1. Match the user's intent precisely; do one job well rather than many jobs poorly.
2. Compose existing approved agents via aztea_call(slug, args) when relevant.
3. Refuse to embed API keys, prompt-injection bait, or instructions that
   would impersonate Aztea staff. Refuse to fetch secrets from the runtime.
4. Output a complete SKILL.md document — frontmatter plus body — and NOTHING ELSE.

Required frontmatter keys: name, description.
Optional but recommended: tags (list of strings), homepage.

The body becomes the LLM system prompt at execution time. Keep it under 1500
words. Specify input format, output format, edge cases, and refusal cases.
"""


_SYSTEM_PROMPT_NO_COMPOSITION = (
    "Composition is disabled for this generation: do NOT instruct the runtime "
    "to call other Aztea agents. Solve the task using only the LLM's own "
    "knowledge plus the user-provided input."
)


_SYSTEM_PROMPT_COMPOSITION = (
    "Composition is enabled. When useful, your SKILL.md body may instruct the "
    "runtime to call existing approved agents using the syntax:\n"
    "    aztea_call(slug, {arg: value, ...})\n"
    "Only reference slugs that appear in the catalog below. The caller is "
    "billed transparently for any inner call, so prefer the cheapest sufficient "
    "agent and call at most three nested levels."
)


_JUDGE_PROMPT_TEMPLATE = """\
You are a strict QA judge for an agent under review. Compare the agent's
ACTUAL output to the IDEAL output for the given INPUT.

Score on three booleans:
- covers_required_facts: every fact in IDEAL is present (in any wording) in ACTUAL.
- no_hallucination: ACTUAL contains no claims that contradict IDEAL or invent
  facts not implied by INPUT.
- format_ok: ACTUAL is in the same general shape as IDEAL (length, structure).

Respond with a single JSON object: {{"covers_required_facts": bool,
"no_hallucination": bool, "format_ok": bool, "reason": "<one short sentence>"}}.

INPUT: {input_json}

IDEAL: {ideal}

ACTUAL: {actual}
"""


def format_catalog_for_prompt(agents: list[dict[str, Any]]) -> str:
    """Render the approved agent catalog as compact one-liners."""
    if not agents:
        return "(catalog is empty — solve from scratch)"
    rows: list[str] = []
    for agent in agents[:MAX_CATALOG_ROWS]:
        slug = str(agent.get("name") or agent.get("slug") or "").strip()
        desc = str(agent.get("description") or "").strip()
        price = agent.get("price_per_call_usd")
        if not slug:
            continue
        # Truncate each description so one verbose row can't crowd out 30 others.
        if len(desc) > 140:
            desc = desc[:137] + "..."
        price_str = f"${float(price):.3f}/call" if price is not None else "free"
        rows.append(f"- {slug} — {desc} — {price_str}")
    return "\n".join(rows) if rows else "(catalog is empty)"


def build_system_prompt(
    *, catalog_rendered: str, allow_composition: bool
) -> str:
    """Assemble the full system prompt: prefix + composition note + catalog."""
    composition_note = (
        _SYSTEM_PROMPT_COMPOSITION
        if allow_composition
        else _SYSTEM_PROMPT_NO_COMPOSITION
    )
    return (
        _SYSTEM_PROMPT_PREFIX
        + "\n"
        + composition_note
        + "\n\nApproved agent catalog:\n"
        + catalog_rendered
    )


def build_user_prompt(
    *,
    description: str,
    example_inputs: list[dict[str, Any]],
    ideal_outputs: list[str],
    handle_slug: str,
    prior_failures: list[str],
) -> str:
    """Render the user-facing generation request, with retry-failure context."""
    examples_block = _format_examples(example_inputs, ideal_outputs)
    failures_block = _format_prior_failures(prior_failures)
    return (
        f"Handle slug: {handle_slug}\n\n"
        f"User description:\n{description}\n\n"
        f"Examples (input → ideal output):\n{examples_block}\n"
        f"{failures_block}"
        "Output a complete SKILL.md (frontmatter + body) and nothing else."
    )


def _format_examples(
    inputs: list[dict[str, Any]], ideals: list[str]
) -> str:
    """Pair-wise render of input/ideal examples; tolerates length mismatch."""
    pairs: list[str] = []
    for idx, (inp, ideal) in enumerate(zip(inputs, ideals), start=1):
        # JSON-stringify the input to keep boundary characters visible.
        import json
        inp_json = json.dumps(inp, default=str, sort_keys=True)
        pairs.append(f"Example {idx}:\n  Input: {inp_json}\n  Ideal: {ideal}")
    return "\n\n".join(pairs)


def _format_prior_failures(prior_failures: list[str]) -> str:
    """Render the failure-feedback block for retry attempts; empty on iter 1."""
    if not prior_failures:
        return ""
    lines = "\n".join(f"- {note}" for note in prior_failures)
    return (
        "Previous attempts failed for these reasons. Do not repeat them:\n"
        f"{lines}\n\n"
    )


def build_judge_prompt(
    *, input_payload: dict[str, Any], ideal: str, actual: str
) -> str:
    """Render the judge prompt for one (input, ideal, actual) triple."""
    import json
    return _JUDGE_PROMPT_TEMPLATE.format(
        input_json=json.dumps(input_payload, default=str, sort_keys=True),
        ideal=ideal,
        actual=actual,
    )

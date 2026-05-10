"""Pydantic contracts for the vibe-an-agent self-serve generation flow.

# OWNS: request/response shapes for POST /agents/generate and
#       GET /agents/generate/{job_id}.
# NOT OWNS: the generation algorithm itself (lives in core.agent_generator),
#           HTTP transport, persistence, or settlement.
# INVARIANTS:
#   - description / example_inputs are user-supplied untrusted data; never
#     interpolated into a system prompt without sanitization in
#     core.agent_generator.prompts.
#   - max_total_cost_cents is integer cents — float is forbidden in money paths.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Reserved handle prefixes — generators may not register agents under these
# names regardless of owner. The leading "@" is implicit.
RESERVED_HANDLE_PREFIXES: tuple[str, ...] = (
    "aztea",
    "system",
    "admin",
    "built-in",
    "platform",
    "official",
)

# Per-call generation budget bounds (cents). Floor is 1 cent; ceiling is $5
# so a runaway request can't drain a wallet by itself.
MIN_GENERATION_BUDGET_CENTS = 1
MAX_GENERATION_BUDGET_CENTS = 500
DEFAULT_GENERATION_BUDGET_CENTS = 50

# Self-test iteration bounds. Three is enough to give the LLM a realistic
# chance to recover from a one-shot judge failure without being a DoS knob.
MIN_SELF_TEST_ITERS = 1
MAX_SELF_TEST_ITERS = 5
DEFAULT_SELF_TEST_ITERS = 3


class GenerateAgentRequest(BaseModel):
    """User input for POST /agents/generate."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=20, max_length=2000)
    example_inputs: list[dict[str, Any]] = Field(min_length=1, max_length=5)
    ideal_outputs: list[str] = Field(min_length=1, max_length=5)
    handle_slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,40}$")
    max_self_test_iters: int = Field(
        default=DEFAULT_SELF_TEST_ITERS,
        ge=MIN_SELF_TEST_ITERS,
        le=MAX_SELF_TEST_ITERS,
    )
    max_total_cost_cents: int = Field(
        default=DEFAULT_GENERATION_BUDGET_CENTS,
        ge=MIN_GENERATION_BUDGET_CENTS,
        le=MAX_GENERATION_BUDGET_CENTS,
    )
    idempotency_key: str = Field(min_length=8, max_length=64)
    # v1: composition-enabled.  When true the prompt encourages the LLM to
    # call existing agents via aztea_call(slug, args). The actual tool
    # plumbing in core.skill_executor is gated by a runtime caller_context.
    allow_composition: bool = True


class GenerateAgentResponse(BaseModel):
    """Response body for POST /agents/generate and the polling GET."""

    model_config = ConfigDict(extra="ignore")

    generation_job_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    agent_id: str | None = None
    handle: str | None = None
    skill_md: str | None = None
    iterations: int = 0
    qa_score: float | None = None
    cost_cents_charged: int = 0
    error: dict[str, Any] | None = None

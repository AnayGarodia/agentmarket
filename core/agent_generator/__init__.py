"""Self-serve agent generation from natural-language description.

# OWNS: end-to-end orchestration for POST /agents/generate — taking a
#       free-form description plus example I/O and producing a SKILL.md,
#       safety-scanning it, self-testing against the user's examples,
#       minting a probation-listed agent, and settling the ledger.
# NOT OWNS: HTTP transport (server.application_parts), payment primitives
#       (core.payments.base), skill execution (core.skill_executor),
#       or LLM provider selection (core.llm).
# INVARIANTS:
#   - All money is integer cents.  Never float() in this package.
#   - Generated agents land at review_status='probation', NEVER 'approved'.
#   - Refund-on-failure: every terminal failure refunds the unused budget.
#   - Idempotency: same (owner_id, idempotency_key) returns the existing job.
"""

from __future__ import annotations

from core.agent_generator.loop import generate_agent
from core.agent_generator.persistence import (
    create_or_get_generation_job,
    get_generation_job,
)

__all__ = [
    "generate_agent",
    "create_or_get_generation_job",
    "get_generation_job",
]

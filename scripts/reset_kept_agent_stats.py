"""Reset rolling-window call stats for the 11 curated public builtins.

Why: success_rate / total_calls / successful_calls / avg_latency_ms are
stored on the `agents` row directly (see core.reputation._load_agent_stats_map)
and accumulate forever. The stats include pre-fix schema rejections (the
secret_scanner protocol-envelope bug, regex_tester subprocess timeout) which
suppressed `aztea_do` and made the catalog look broken in the public-facing
list_agents response. After the 2026-05-07 cleanup we want a clean rolling
window for the kept agents so the displayed success_rate reflects post-fix
behavior.

What it does NOT touch:
- Wallet ledger, escrow, receipts, signed-receipt history.
- caller_ratings, job_quality_ratings (those are user-supplied signal,
  not server-counted).
- Sunset agents — their stats stay frozen as a permanent record.

Idempotent: re-running it on already-zeroed rows is a no-op.

Usage (on prod):
    sudo -u aztea /home/aztea/app/venv/bin/python scripts/reset_kept_agent_stats.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import db as _db  # noqa: E402
from server.builtin_agents.constants import (  # noqa: E402
    CURATED_PUBLIC_BUILTIN_AGENT_IDS,
)


def main() -> int:
    kept = sorted(CURATED_PUBLIC_BUILTIN_AGENT_IDS)
    print(f"Resetting stats for {len(kept)} kept agents…")

    placeholders = ",".join(["%s"] * len(kept))
    with _db.get_db_connection() as conn:
        before = {
            row["agent_id"]: (
                row["total_calls"],
                row["successful_calls"],
                row["avg_latency_ms"],
            )
            for row in conn.execute(
                f"SELECT agent_id, total_calls, successful_calls, avg_latency_ms "
                f"FROM agents WHERE agent_id IN ({placeholders})",
                tuple(kept),
            ).fetchall()
        }
        conn.execute(
            f"""
            UPDATE agents
            SET total_calls = 0,
                successful_calls = 0,
                avg_latency_ms = 0
            WHERE agent_id IN ({placeholders})
            """,
            tuple(kept),
        )

    nonzero_before = sum(1 for v in before.values() if any(v))
    print(f"  agents found: {len(before)} / {len(kept)}")
    print(f"  rows with non-zero stats before reset: {nonzero_before}")
    print("  done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

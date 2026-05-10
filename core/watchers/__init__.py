"""
core.watchers — diff-watchers: register a target + agent + budget; sweeper
periodically fingerprints the target (HTTP body / git ls-remote / package
manifest) and only fires (and bills) the paid agent when the fingerprint
changes. Pure cron is the on_change_policy='always' case.

# OWNS: watcher row CRUD, fingerprint computation, sweeper loop, delivery
# NOT OWNS: job creation rules (delegates to core.jobs), wallet charges
#           (delegates to core.payments), webhook retry/backoff (uses the
#           existing job_event_deliveries pipeline)
# INVARIANTS:
# - A watcher tick that observes no fingerprint change MUST NOT charge.
# - All outbound URLs must pass through core.url_security before any I/O.
# - spend_today_cents resets at the UTC day boundary; cap is enforced
#   before pre_call_charge so we never debit past budget.
# - Watchers never call payments.* directly; they go through fire().
"""

from __future__ import annotations

from core import db as _db

# Re-exported so isolated tests can monkey-patch `core.watchers.DB_PATH`
# the same way they do for core.payments / core.jobs.
DB_PATH: str = _db.DB_PATH

from .crud import (
    create_watcher,
    delete_watcher,
    get_watcher,
    list_watchers_for_owner,
    list_watcher_runs,
    update_watcher,
)
from .fingerprint import fingerprint_target
from .models import (
    DELIVERY_REQUIRED_ERROR,
    ON_CHANGE_POLICIES,
    STATUS_ACTIVE,
    STATUS_BUDGET_EXHAUSTED,
    STATUS_DISABLED,
    STATUS_PAUSED,
    TARGET_KINDS,
    WatcherCreate,
    WatcherUpdate,
    WatcherView,
    watcher_to_view,
)
from .sweeper import sweep_watchers, watchers_sweeper_loop

__all__ = [
    "create_watcher",
    "delete_watcher",
    "get_watcher",
    "list_watcher_runs",
    "list_watchers_for_owner",
    "update_watcher",
    "fingerprint_target",
    "sweep_watchers",
    "watchers_sweeper_loop",
    "DELIVERY_REQUIRED_ERROR",
    "ON_CHANGE_POLICIES",
    "STATUS_ACTIVE",
    "STATUS_BUDGET_EXHAUSTED",
    "STATUS_DISABLED",
    "STATUS_PAUSED",
    "TARGET_KINDS",
    "WatcherCreate",
    "WatcherUpdate",
    "WatcherView",
    "watcher_to_view",
]

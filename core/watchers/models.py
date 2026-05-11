"""Pydantic models + constants for watchers."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

TARGET_KINDS = ("http", "git", "manifest")
ON_CHANGE_POLICIES = ("on_change", "always")
STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"
STATUS_BUDGET_EXHAUSTED = "budget_exhausted"
STATUS_DISABLED = "disabled"
WATCHER_STATUSES = (
    STATUS_ACTIVE,
    STATUS_PAUSED,
    STATUS_BUDGET_EXHAUSTED,
    STATUS_DISABLED,
)

MIN_TICK_SECONDS = 60
MAX_TICK_SECONDS = 24 * 3600
MIN_BUDGET_CENTS = 1
MAX_BUDGET_CENTS = 1_000_000  # $10,000/day per watcher
MAX_CONSECUTIVE_ERRORS_BEFORE_PAUSE = 5

DELIVERY_REQUIRED_ERROR = (
    "At least one of delivery_webhook_url or delivery_email is required."
)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


class WatcherCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., min_length=1, max_length=128)

    # 1.7.5 — "cron" target_kind added: a tick-driven watcher with no
    # external resource to fingerprint. It fires the agent every
    # tick_interval_seconds with on_change_policy="always" semantics.
    # The 1.7.3 CLI shipped a `--cron` flag but the server required
    # target_url unconditionally; eval B-17 reproduced 422 on every
    # cron-only create.
    target_kind: Literal["http", "git", "manifest", "cron"]
    target_url: str | None = Field(default=None, min_length=1, max_length=2048)
    target_meta: dict[str, Any] = Field(default_factory=dict)

    on_change_policy: Literal["on_change", "always"] = "on_change"
    tick_interval_seconds: int = Field(default=900, ge=MIN_TICK_SECONDS, le=MAX_TICK_SECONDS)

    budget_per_day_cents: int = Field(..., ge=MIN_BUDGET_CENTS, le=MAX_BUDGET_CENTS)

    delivery_webhook_url: str | None = Field(default=None, max_length=2048)
    delivery_email: str | None = Field(default=None, max_length=320)

    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("target_meta", "payload")
    @classmethod
    def _validate_dict(cls, v: Any) -> dict[str, Any]:
        if not isinstance(v, dict):
            raise ValueError("must be a JSON object.")
        return v

    @model_validator(mode="after")
    def _check_delivery(self) -> "WatcherCreate":
        if not (self.delivery_webhook_url or self.delivery_email):
            raise ValueError(DELIVERY_REQUIRED_ERROR)
        # 1.7.5 — target_kind-specific validation. http/git/manifest still
        # require target_url; cron explicitly does not.
        if self.target_kind in ("http", "git"):
            if not self.target_url:
                raise ValueError(
                    f"target_url is required when target_kind is '{self.target_kind}'."
                )
        if self.target_kind == "manifest":
            registry = str(self.target_meta.get("registry") or "").strip().lower()
            package = str(self.target_meta.get("package") or "").strip()
            if registry not in ("pypi", "npm"):
                raise ValueError(
                    "manifest target_meta.registry must be 'pypi' or 'npm'."
                )
            if not package:
                raise ValueError("manifest target_meta.package is required.")
        if self.target_kind == "cron":
            # Cron watchers must fire every tick (no fingerprint to compare).
            if self.on_change_policy != "always":
                # Auto-correct rather than reject: cron+on_change makes no
                # sense, and forcing the caller to set on_change_policy
                # explicitly is unnecessary friction.
                object.__setattr__(self, "on_change_policy", "always")
        return self


class WatcherUpdate(BaseModel):
    """Partial update — every field is optional."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["active", "paused"] | None = None
    tick_interval_seconds: int | None = Field(
        default=None, ge=MIN_TICK_SECONDS, le=MAX_TICK_SECONDS
    )
    budget_per_day_cents: int | None = Field(
        default=None, ge=MIN_BUDGET_CENTS, le=MAX_BUDGET_CENTS
    )
    delivery_webhook_url: str | None = Field(default=None, max_length=2048)
    delivery_email: str | None = Field(default=None, max_length=320)
    on_change_policy: Literal["on_change", "always"] | None = None


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


class WatcherView(BaseModel):
    watcher_id: str
    owner_user_id: str
    agent_id: str
    target_kind: str
    target_url: str
    target_meta: dict[str, Any]
    on_change_policy: str
    tick_interval_seconds: int
    budget_per_day_cents: int
    spend_today_cents: int
    spend_window_date: str
    delivery_webhook_url: str | None
    delivery_email: str | None
    payload: dict[str, Any]
    status: str
    last_fingerprint: str | None
    last_fingerprint_at: str | None
    last_fired_job_id: str | None
    last_error: str | None
    next_check_at: str
    created_at: str
    updated_at: str


def _safe_json_load(blob: str | None, default: Any) -> Any:
    if blob is None or blob == "":
        return default
    try:
        return json.loads(blob)
    except (TypeError, ValueError):
        return default


def watcher_to_view(row: dict) -> dict:
    """Project a watchers row dict to the public view dict (delivery_secret hidden)."""
    return {
        "watcher_id": row["watcher_id"],
        "owner_user_id": row["owner_user_id"],
        "agent_id": row["agent_id"],
        "target_kind": row["target_kind"],
        "target_url": row["target_url"],
        "target_meta": _safe_json_load(row.get("target_meta_json"), {}),
        "on_change_policy": row["on_change_policy"],
        "tick_interval_seconds": int(row["tick_interval_seconds"]),
        "budget_per_day_cents": int(row["budget_per_day_cents"]),
        "spend_today_cents": int(row.get("spend_today_cents") or 0),
        "spend_window_date": row.get("spend_window_date") or "",
        "delivery_webhook_url": row.get("delivery_webhook_url"),
        "delivery_email": row.get("delivery_email"),
        "payload": _safe_json_load(row.get("payload_json"), {}),
        "status": row["status"],
        "last_fingerprint": row.get("last_fingerprint"),
        "last_fingerprint_at": row.get("last_fingerprint_at"),
        "last_fired_job_id": row.get("last_fired_job_id"),
        "last_error": row.get("last_error"),
        "next_check_at": row["next_check_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }

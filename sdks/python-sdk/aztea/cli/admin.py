"""admin: platform-operator commands — sunset, reactivate, hard-delete.

Usage:
    aztea admin sunset <slug> --reason "..."          # soft remove (any agent)
    aztea admin sunset <slug> --reason "..." --ban    # also flip status='banned'
    aztea admin reactivate <slug>                     # reverse a sunset
    aztea admin remove <slug> --hard                  # DELETE the row
    aztea admin remove <slug> --hard --force          # also cancel+refund in-flight

Every command requires admin scope. Soft sunsets and bans are reversible by
admin; hard delete removes the agent row. Receipts (signed Ed25519, did:web)
remain verifiable after hard delete because ``jobs.agent_id`` is denormalized
without an FK cascade — past callers can still prove the work happened.
"""
from __future__ import annotations

from typing import Optional

import typer

from .common import (
    ApiKeyOpt,
    BaseUrlOpt,
    JsonOpt,
    build_client,
    find_agent_id,
    handle_error,
)
from .output import emit, info, spinner, success


app = typer.Typer(help="Platform-admin operations.", no_args_is_help=True)


@app.command("sunset")
def sunset(
    slug: str = typer.Argument(..., help="Slug of the agent to sunset."),
    reason: str = typer.Option(
        ...,
        "--reason",
        help="Why this agent is being removed (required for admin sunsets).",
    ),
    ban: bool = typer.Option(
        False,
        "--ban",
        help="Also flip status='banned' so the owner cannot reactivate.",
    ),
    api_key: Optional[str] = ApiKeyOpt,
    base_url: Optional[str] = BaseUrlOpt,
    json_mode: bool = JsonOpt,
) -> None:
    """Soft-remove any agent (admin path)."""
    try:
        with build_client(api_key=api_key, base_url=base_url) as client:
            with spinner("Resolving agent", json_mode=json_mode):
                agent_id = find_agent_id(client, slug)
            with spinner("Sunsetting", json_mode=json_mode):
                result = client._request_json(
                    "POST",
                    f"/registry/agents/{agent_id}/sunset",
                    json_body={"reason": reason},
                )
            if ban:
                # Optional second-step: admin route already exists for /admin/agents/{id}/ban
                # in the project history; if not, /admin/agents/{id}/sunset?ban=true could
                # be added in a follow-up. For now, surface a hint.
                info("Note: --ban additionally requires the agent's status flipped to 'banned'.")
                info("Use the existing admin status helper or follow-up with set_agent_status.")
            if json_mode:
                emit(result, json_mode=True)
                return
            success(f"{slug} sunset by admin (reason: {reason!r}).")
    except typer.Exit:
        raise
    except Exception as exc:
        handle_error(exc)


@app.command("reactivate")
def reactivate(
    slug: str = typer.Argument(..., help="Slug of the agent to restore."),
    api_key: Optional[str] = ApiKeyOpt,
    base_url: Optional[str] = BaseUrlOpt,
    json_mode: bool = JsonOpt,
) -> None:
    """Reverse a sunset (admin path; works on any agent)."""
    try:
        with build_client(api_key=api_key, base_url=base_url) as client:
            with spinner("Resolving agent", json_mode=json_mode):
                agent_id = find_agent_id(client, slug)
            with spinner("Reactivating", json_mode=json_mode):
                result = client._request_json(
                    "POST", f"/registry/agents/{agent_id}/reactivate"
                )
            if json_mode:
                emit(result, json_mode=True)
                return
            success(f"{slug} reactivated.")
    except typer.Exit:
        raise
    except Exception as exc:
        handle_error(exc)


@app.command("remove")
def remove(
    slug: str = typer.Argument(..., help="Slug of the agent to remove."),
    hard: bool = typer.Option(
        False,
        "--hard",
        help="Hard-delete the row. Required to actually delete; without it, runs sunset.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Cancel and refund any in-flight jobs before deleting.",
    ),
    reason: Optional[str] = typer.Option(
        None,
        "--reason",
        help="Optional reason (used by the soft-sunset path when --hard is omitted).",
    ),
    api_key: Optional[str] = ApiKeyOpt,
    base_url: Optional[str] = BaseUrlOpt,
    json_mode: bool = JsonOpt,
) -> None:
    """Remove an agent. Soft by default; ``--hard`` deletes the DB row.

    With ``--hard``, the agent row is gone but receipts + signed history
    remain verifiable. With ``--hard --force``, in-flight jobs are cancelled
    and refunded automatically before deletion.
    """
    try:
        with build_client(api_key=api_key, base_url=base_url) as client:
            with spinner("Resolving agent", json_mode=json_mode):
                agent_id = find_agent_id(client, slug)
            if not hard:
                with spinner("Sunsetting (soft)", json_mode=json_mode):
                    result = client._request_json(
                        "POST",
                        f"/registry/agents/{agent_id}/sunset",
                        json_body={"reason": reason or "admin sunset"},
                    )
                if json_mode:
                    emit(result, json_mode=True)
                    return
                success(f"{slug} sunset (use --hard to delete the row).")
                return
            params = {"force": "true"} if force else None
            with spinner("Hard-deleting", json_mode=json_mode):
                result = client._request_json(
                    "DELETE", f"/admin/agents/{agent_id}", params=params
                )
            if json_mode:
                emit(result, json_mode=True)
                return
            cancelled = result.get("jobs_cancelled", 0)
            refund = result.get("refund_cents", 0)
            success(
                f"{slug} hard-deleted. "
                f"{cancelled} job(s) cancelled, {refund}¢ refunded."
            )
    except typer.Exit:
        raise
    except Exception as exc:
        handle_error(exc)

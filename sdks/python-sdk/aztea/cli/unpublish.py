"""unpublish: retract or restore a listing you own.

Usage:
    aztea unpublish my-skill                        # soft-sunset (reversible)
    aztea unpublish my-skill --reason "deprecated"  # capture a reason
    aztea unpublish my-skill --reactivate           # bring it back

Soft-sunset is reversible: the agent flips to ``review_status='sunset'``,
disappears from the public catalog, and returns HTTP 410 to callers.
Receipts and signed history remain intact. Run with ``--reactivate`` to
restore the listing.

Hard delete (DB row removed) is admin-only — see ``aztea admin remove``.
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
    slugify,
)
from .output import emit, info, spinner, success


def _resolve_via_owner_listings(client, needle: str) -> str:
    """Find an agent_id from the caller's own listings (incl. sunset).

    1.7.0: ``find_agent_id`` resolves against the public catalog only,
    which filters out ``review_status='sunset'`` rows. Reactivating /
    re-sunsetting a previously-sunset listing therefore failed with
    ``Unknown agent '<slug>'``. Hit ``GET /registry/agents/mine`` and
    match by agent_id, slug, or display name.
    """
    needle = (needle or "").strip()
    if not needle:
        raise typer.BadParameter("Slug is required.")
    resp = client._request_json("GET", "/registry/agents/mine")
    agents = resp.get("agents") or []
    for entry in agents:
        if entry.get("agent_id") == needle:
            return entry["agent_id"]
        name = entry.get("name") or ""
        if slugify(name) == needle.replace("_", "-"):
            return entry["agent_id"]
    raise typer.BadParameter(
        f"Unknown agent '{needle}'. It's not in your listings (live or sunset). "
        "Run `aztea agents list` to see your active listings."
    )


def unpublish(
    slug: str = typer.Argument(..., help="Slug of the agent to unpublish (kebab- or snake-case)."),
    reason: Optional[str] = typer.Option(
        None,
        "--reason",
        help="Why you're retracting (stored on the agent row). Defaults to 'self-retracted'.",
    ),
    reactivate: bool = typer.Option(
        False,
        "--reactivate",
        help="Reverse a prior unpublish and restore the agent to the catalog.",
    ),
    api_key: Optional[str] = ApiKeyOpt,
    base_url: Optional[str] = BaseUrlOpt,
    json_mode: bool = JsonOpt,
) -> None:
    """Soft-remove an agent you own from the catalog (reversible).

    The agent stops appearing in ``aztea agents list``, ``aztea agents search``,
    auto-hire, and the MCP catalog. Direct calls return HTTP 410. Existing
    signed receipts continue to verify.
    """
    try:
        with build_client(api_key=api_key, base_url=base_url) as client:
            with spinner("Resolving agent", json_mode=json_mode):
                # Try the catalog (live agents) first.
                try:
                    agent_id = find_agent_id(client, slug)
                except typer.BadParameter:
                    # 1.7.0: when the agent is sunset, find_agent_id can't
                    # resolve it (catalog filters sunset out). For
                    # `--reactivate` and re-sunsetting, fall back to the
                    # caller's own listings (which include sunset). This
                    # closes a previously-noted bug where the only escape
                    # was hitting the REST API directly.
                    agent_id = _resolve_via_owner_listings(client, slug)
            path = (
                f"/registry/agents/{agent_id}/reactivate"
                if reactivate
                else f"/registry/agents/{agent_id}/sunset"
            )
            body = None if reactivate else {"reason": reason} if reason else {}
            verb = "Reactivating" if reactivate else "Unpublishing"
            with spinner(verb, json_mode=json_mode):
                result = client._request_json("POST", path, json_body=body)
            if json_mode:
                emit(result, json_mode=True)
                return
            if reactivate:
                success(f"{slug} is back in the catalog.")
            else:
                success(f"{slug} is unpublished (review_status=sunset).")
                info(f"Run `aztea unpublish {slug} --reactivate` to restore it.")
    except typer.Exit:
        raise
    except Exception as exc:
        handle_error(exc)

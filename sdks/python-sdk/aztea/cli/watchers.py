"""watchers: schedule agent runs by URL fingerprint or tick interval.

A watcher fires an agent when the target_url's fingerprint changes
(http/git/manifest target_kind) or on every tick (cron target_kind).
Each watcher has a per-day spend cap; fires that would exceed it are
skipped without charging.

Usage:
    aztea watchers list
    aztea watchers create --agent <slug> --kind cron --interval 3600 \\
        --budget 100 --webhook https://example.com/hook
    aztea watchers create --agent <slug> --kind http --url https://example.com \\
        --budget 100 --webhook https://example.com/hook
    aztea watchers delete <watcher_id>

1.7.3 added the command family; 1.7.5 aligns the CLI shape with the
actual backend schema. The earlier 1.7.3 CLI shipped flags
(`--cron`, `--daily-cap-cents`, `--payload`) that the server didn't
accept, producing 422 on every create attempt (eval B-17).
"""
from __future__ import annotations

import json as _json
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


app = typer.Typer(help="Manage scheduled / URL-watching agent fires.", no_args_is_help=True)


_TARGET_KINDS = ("http", "git", "manifest", "cron")


@app.command("list")
def list_watchers(
    api_key: Optional[str] = ApiKeyOpt,
    base_url: Optional[str] = BaseUrlOpt,
    json_mode: bool = JsonOpt,
) -> None:
    """List every watcher you own."""
    try:
        with build_client(api_key=api_key, base_url=base_url) as client:
            with spinner("Loading watchers", json_mode=json_mode):
                resp = client._request_json("GET", "/watchers")
        watchers = resp.get("watchers") or resp.get("items") or []
        if json_mode:
            emit({"watchers": watchers, "count": len(watchers)}, json_mode=True)
            return
        if not watchers:
            info("No watchers yet. Create one with `aztea watchers create`.")
            return
        for w in watchers:
            info(
                f"  {w.get('watcher_id', '')[:12]}  "
                f"{w.get('target_kind') or 'http'}  "
                f"{(w.get('target_url') or '—')[:50]}  "
                f"every {w.get('tick_interval_seconds', 900)}s  "
                f"budget=${w.get('budget_per_day_cents', 0)/100:.2f}/day"
            )
        success(f"{len(watchers)} watcher(s).")
    except typer.Exit:
        raise
    except Exception as exc:
        handle_error(exc)


@app.command("create")
def create_watcher(
    agent: str = typer.Option(..., "--agent", help="Slug or agent_id to invoke on each fire."),
    kind: str = typer.Option(
        "http", "--kind",
        help="Target kind: http | git | manifest | cron. Default http.",
    ),
    url: Optional[str] = typer.Option(
        None, "--url",
        help="URL to fingerprint (required for kind=http|git). Omit for kind=cron.",
    ),
    interval: int = typer.Option(
        900, "--interval",
        help="Seconds between checks. Default 900 (15 min). Minimum 60.",
    ),
    budget: int = typer.Option(
        ..., "--budget",
        help="Per-day spend cap in cents. Fires beyond it skip without charge.",
    ),
    webhook: Optional[str] = typer.Option(
        None, "--webhook",
        help="Delivery webhook URL. Either --webhook or --email is required.",
    ),
    email: Optional[str] = typer.Option(
        None, "--email",
        help="Delivery email. Either --webhook or --email is required.",
    ),
    payload: str = typer.Option(
        "{}", "--payload",
        help="JSON input to pass to the agent on each fire. Defaults to `{}`.",
    ),
    api_key: Optional[str] = ApiKeyOpt,
    base_url: Optional[str] = BaseUrlOpt,
    json_mode: bool = JsonOpt,
) -> None:
    """Create a watcher.

    --kind=cron creates a tick-only watcher (fires every --interval).
    --kind=http|git requires --url for fingerprinting.
    --kind=manifest needs target_meta — use the REST API for that.
    """
    if kind not in _TARGET_KINDS:
        raise typer.BadParameter(f"--kind must be one of {_TARGET_KINDS}")
    if kind in ("http", "git") and not url:
        raise typer.BadParameter(f"--url is required when --kind={kind}")
    if not (webhook or email):
        raise typer.BadParameter("Pass one of --webhook or --email for delivery.")
    try:
        parsed_payload = _json.loads(payload)
    except _json.JSONDecodeError as exc:
        raise typer.BadParameter(f"--payload must be valid JSON: {exc}")
    try:
        with build_client(api_key=api_key, base_url=base_url) as client:
            with spinner("Resolving agent", json_mode=json_mode):
                agent_id = find_agent_id(client, agent)
            body: dict = {
                "agent_id": agent_id,
                "target_kind": kind,
                "tick_interval_seconds": int(interval),
                "budget_per_day_cents": int(budget),
                "payload": parsed_payload,
                "on_change_policy": "always" if kind == "cron" else "on_change",
            }
            if url:
                body["target_url"] = url
            if webhook:
                body["delivery_webhook_url"] = webhook
            if email:
                body["delivery_email"] = email
            with spinner("Creating watcher", json_mode=json_mode):
                resp = client._request_json("POST", "/watchers", json_body=body)
        if json_mode:
            emit(resp, json_mode=True)
            return
        success(f"Watcher created: {resp.get('watcher_id', '')[:12]}")
        info(f"  agent={agent_id[:8]}…  kind={kind}  every {interval}s")
    except typer.Exit:
        raise
    except Exception as exc:
        handle_error(exc)


@app.command("delete")
def delete_watcher(
    watcher_id: str = typer.Argument(..., help="The watcher_id from `aztea watchers list`."),
    api_key: Optional[str] = ApiKeyOpt,
    base_url: Optional[str] = BaseUrlOpt,
    json_mode: bool = JsonOpt,
) -> None:
    """Delete a watcher. No future fires."""
    try:
        with build_client(api_key=api_key, base_url=base_url) as client:
            with spinner("Deleting watcher", json_mode=json_mode):
                resp = client._request_json("DELETE", f"/watchers/{watcher_id}")
        if json_mode:
            emit(resp if isinstance(resp, dict) else {"deleted": True}, json_mode=True)
            return
        success(f"Watcher {watcher_id[:12]} deleted.")
    except typer.Exit:
        raise
    except Exception as exc:
        handle_error(exc)

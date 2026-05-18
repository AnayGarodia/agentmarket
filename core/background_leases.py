"""DB-backed leadership leases for background workers.

# OWNS: acquire / renew / release of leadership leases for the dispute
#       judge and any other background worker that wants to be a
#       singleton across workers + restarts.
# NOT OWNS: the work the leaseholder actually does — that's the caller.
# INVARIANTS:
#   * Exactly one holder at a time per (kind) until the lease expires.
#   * A crashed holder cannot keep the lease past expires_at — any other
#     worker can take it once the wall-clock passes that timestamp.
#   * Renew is a no-op if the row doesn't exist (caller should call
#     acquire_or_renew, never bare renew).
# DECISIONS:
#   * SQLite + Postgres compatibility: use core.db's %s placeholders and
#     a conditional INSERT / UPDATE pair guarded by SELECT-FOR-UPDATE
#     semantics on Postgres (BEGIN IMMEDIATE on SQLite via core.db).
#   * Returning False instead of raising lets callers spin without
#     polluting the error budget — losing the election is normal.
"""

from __future__ import annotations

import logging
import os
import socket
from datetime import datetime, timedelta, timezone
from typing import Any

from core import db as _db

_LOG = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _later_iso(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=int(seconds))).isoformat()


def default_holder_id() -> str:
    """Pure: hostname + pid identifier suitable as a lease holder_id."""
    return f"{socket.gethostname()}-{os.getpid()}"


def acquire_or_renew(
    kind: str, holder_id: str, *, lease_seconds: int = 120,
) -> bool:
    """Side-effect: try to acquire the named lease or extend it if we hold it.

    Returns True when this caller holds the lease at the moment of return,
    False when another holder owns a still-valid lease.

    Why: the boot-once fcntl election left disputes wedged forever when
    the leader worker died after a brief tick — surviving workers never
    re-attempted. With this, every tick re-checks and any free / expired
    lease is taken.
    """
    if not kind or not holder_id:
        raise ValueError("kind and holder_id are required")
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    new_expires = (now + timedelta(seconds=int(lease_seconds))).isoformat()
    try:
        with _db.get_raw_connection(_db.DB_PATH) as conn:
            row = conn.execute(
                "SELECT holder_id, expires_at FROM background_worker_leases "
                "WHERE kind = %s",
                (kind,),
            ).fetchone()
            if row is None:
                return _insert_lease(
                    conn, kind=kind, holder_id=holder_id,
                    acquired_at=now_iso, expires_at=new_expires,
                )
            current_holder = str(row["holder_id"] or "").strip()
            current_expires = str(row["expires_at"] or "").strip()
            if current_holder == holder_id:
                return _renew_lease(
                    conn, kind=kind, holder_id=holder_id,
                    expires_at=new_expires, heartbeat_at=now_iso,
                )
            if _is_expired(current_expires, now):
                return _takeover_lease(
                    conn, kind=kind, prev_holder=current_holder,
                    new_holder=holder_id, acquired_at=now_iso,
                    expires_at=new_expires,
                )
            return False
    except _db.OperationalError as exc:
        # Missing-table is acceptable pre-migration; treat as "we lead"
        # so the pre-migration codepath remains the same as today.
        if "no such table" in str(exc).lower():
            return True
        _LOG.warning("lease acquire failed for %s: %s", kind, exc)
        return False


def release(kind: str, holder_id: str) -> bool:
    """Side-effect: release the lease iff this caller still holds it."""
    try:
        with _db.get_raw_connection(_db.DB_PATH) as conn:
            cur = conn.execute(
                "DELETE FROM background_worker_leases "
                "WHERE kind = %s AND holder_id = %s",
                (kind, holder_id),
            )
            conn.commit()
            return getattr(cur, "rowcount", 0) > 0
    except _db.OperationalError:
        return False


def current_holder(kind: str) -> dict[str, Any] | None:
    """Side-effect: return the lease row for ``kind`` or None.

    Why: useful for observability — the admin/usage views show "who's
    judging right now?" without forcing the caller to imitate the SQL.
    """
    try:
        with _db.get_raw_connection(_db.DB_PATH) as conn:
            row = conn.execute(
                "SELECT kind, holder_id, hostname, pid, acquired_at, "
                "expires_at, heartbeat_at FROM background_worker_leases "
                "WHERE kind = %s",
                (kind,),
            ).fetchone()
        if row is None:
            return None
        return {k: row[k] for k in row.keys()}
    except _db.OperationalError:
        return None


def _is_expired(expires_iso: str, now: datetime) -> bool:
    if not expires_iso:
        return True
    try:
        # Accept Z-suffixed or +HH:MM offsets.
        expires = datetime.fromisoformat(expires_iso.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return now >= expires


def _insert_lease(
    conn, *, kind: str, holder_id: str, acquired_at: str, expires_at: str,
) -> bool:
    """Side-effect: first-ever insert. Returns False on a race-loss."""
    try:
        conn.execute(
            "INSERT INTO background_worker_leases "
            "(kind, holder_id, hostname, pid, acquired_at, expires_at, heartbeat_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                kind, holder_id, socket.gethostname(), os.getpid(),
                acquired_at, expires_at, acquired_at,
            ),
        )
        conn.commit()
        return True
    except _db.IntegrityError:
        # Another worker beat us to the insert; lose the election quietly.
        return False


def _renew_lease(
    conn, *, kind: str, holder_id: str, expires_at: str, heartbeat_at: str,
) -> bool:
    """Side-effect: extend our own lease. Always returns True on success."""
    cur = conn.execute(
        "UPDATE background_worker_leases "
        "SET expires_at = %s, heartbeat_at = %s "
        "WHERE kind = %s AND holder_id = %s",
        (expires_at, heartbeat_at, kind, holder_id),
    )
    conn.commit()
    return getattr(cur, "rowcount", 0) > 0


def _takeover_lease(
    conn, *, kind: str, prev_holder: str, new_holder: str,
    acquired_at: str, expires_at: str,
) -> bool:
    """Side-effect: take an expired lease, conditional on the previous holder.

    The conditional ensures we don't trample a holder who renewed
    between our SELECT and our UPDATE.
    """
    cur = conn.execute(
        "UPDATE background_worker_leases "
        "SET holder_id = %s, hostname = %s, pid = %s, "
        "    acquired_at = %s, expires_at = %s, heartbeat_at = %s "
        "WHERE kind = %s AND holder_id = %s AND expires_at < %s",
        (
            new_holder, socket.gethostname(), os.getpid(),
            acquired_at, expires_at, acquired_at, kind, prev_holder,
            acquired_at,
        ),
    )
    conn.commit()
    took_over = getattr(cur, "rowcount", 0) > 0
    if took_over:
        _LOG.info(
            "background_worker_lease.taken_over kind=%s prev=%s new=%s",
            kind, prev_holder, new_holder,
        )
    return took_over

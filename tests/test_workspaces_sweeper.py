"""TTL sweeper for active workspaces past expires_at."""

from __future__ import annotations

import uuid

import pytest


def _close_conn() -> None:
    from core import db as _db
    conn = getattr(_db._local, "conn", None)
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass
    try:
        delattr(_db._local, "conn")
    except AttributeError:
        pass


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    from core import db as _db
    from core import workspaces as _ws
    db_path = str(tmp_path / f"ws-{uuid.uuid4().hex}.db")
    _close_conn()
    monkeypatch.setattr(_db, "DB_PATH", db_path)
    monkeypatch.setattr(_ws, "DB_PATH", db_path)
    monkeypatch.setenv("AZTEA_WORKSPACE_SIGNING_KEY_PATH",
                       str(tmp_path / "key.pem"))
    from core.migrate import apply_migrations
    apply_migrations(db_path)
    yield
    _close_conn()


def _owner() -> str:
    return f"usr_{uuid.uuid4().hex[:12]}"


def _force_expire(ws_id: str, when: str = "2000-01-01T00:00:00+00:00") -> None:
    from core import workspaces
    with workspaces._connect() as conn:
        conn.execute(
            "UPDATE workspaces SET expires_at = %s WHERE workspace_id = %s",
            (when, ws_id),
        )


def test_sweeper_marks_expired_active_workspace():
    from core import workspaces
    ws = workspaces.create_workspace(owner_user_id=_owner(), ttl_seconds=3600)
    workspaces.write_artifact(ws, "a", b"x", "text/plain")
    _force_expire(ws)
    n = workspaces.run_sweeper(now_iso="2030-01-01T00:00:00+00:00")
    assert n >= 1
    row = workspaces.get_workspace(ws)
    assert row["status"] == "expired"


def test_sweeper_does_not_touch_sealed():
    from core import workspaces
    ws = workspaces.create_workspace(owner_user_id=_owner(), ttl_seconds=3600)
    workspaces.write_artifact(ws, "a", b"x", "text/plain")
    workspaces.seal_workspace(ws)
    _force_expire(ws)
    workspaces.run_sweeper(now_iso="2030-01-01T00:00:00+00:00")
    row = workspaces.get_workspace(ws)
    assert row["status"] == "sealed"


def test_sweeper_does_not_touch_active_within_ttl():
    from core import workspaces
    ws = workspaces.create_workspace(owner_user_id=_owner(), ttl_seconds=3600)
    # expires_at is ~1h from now; sweeper at "now" should not touch it.
    workspaces.run_sweeper()
    row = workspaces.get_workspace(ws)
    assert row["status"] == "active"


def test_expired_workspace_reads_404():
    from core import workspaces
    from core import workspaces_errors as wse
    ws = workspaces.create_workspace(owner_user_id=_owner(), ttl_seconds=3600)
    workspaces.write_artifact(ws, "a", b"x", "text/plain")
    _force_expire(ws)
    workspaces.run_sweeper(now_iso="2030-01-01T00:00:00+00:00")
    with pytest.raises(wse.WorkspaceNotFound):
        workspaces.read_artifact(ws, "a")

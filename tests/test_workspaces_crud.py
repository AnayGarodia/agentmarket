"""Unit tests for core/workspaces.py CRUD operations.

Runs against whichever backend core/db.py is configured for. Set
DATABASE_URL=postgresql://... before pytest to exercise Postgres.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest


def _close_conn() -> None:
    """Drop the thread-local DB connection so the new DB_PATH takes effect."""
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
    from core.migrate import apply_migrations
    apply_migrations(db_path)
    yield
    _close_conn()


def _owner() -> str:
    return f"usr_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_create_workspace_returns_prefixed_id():
    from core import workspaces
    ws_id = workspaces.create_workspace(owner_user_id=_owner())
    assert ws_id.startswith("ws_")
    assert len(ws_id) == 3 + 22


def test_create_workspace_persists_row_with_defaults():
    from core import workspaces
    owner = _owner()
    ws_id = workspaces.create_workspace(owner_user_id=owner)
    row = workspaces.get_workspace(ws_id)
    assert row["workspace_id"] == ws_id
    assert row["owner_user_id"] == owner
    assert row["status"] == "active"
    assert row["backing_type"] == "bytea"
    assert row["total_bytes"] == 0
    assert row["artifact_count"] == 0
    assert row["quota_bytes"] == 64 * 1024 * 1024
    assert row["run_id"] is None


def test_create_workspace_rejects_bad_backing():
    from core import workspaces
    with pytest.raises(ValueError):
        workspaces.create_workspace(owner_user_id=_owner(), backing_type="memory")


def test_create_workspace_rejects_sandbox_without_id():
    from core import workspaces
    with pytest.raises(ValueError):
        workspaces.create_workspace(owner_user_id=_owner(), backing_type="sandbox")


def test_create_workspace_rejects_bad_ttl():
    from core import workspaces
    with pytest.raises(ValueError):
        workspaces.create_workspace(owner_user_id=_owner(), ttl_seconds=0)
    with pytest.raises(ValueError):
        workspaces.create_workspace(owner_user_id=_owner(), ttl_seconds=10_000_000)


def test_get_unknown_workspace_raises():
    from core import workspaces
    from core import workspaces_errors as wse
    with pytest.raises(wse.WorkspaceNotFound):
        workspaces.get_workspace("ws_does_not_exist_anywhere_123")


# ---------------------------------------------------------------------------
# Artifact CRUD
# ---------------------------------------------------------------------------


def test_write_artifact_persists_content_and_metadata():
    from core import workspaces
    ws_id = workspaces.create_workspace(owner_user_id=_owner())
    meta = workspaces.write_artifact(
        ws_id, "hello.txt", b"hello world", "text/plain",
        created_by_agent_id="agt_test", created_by_job_id="job_test",
    )
    assert meta["name"] == "hello.txt"
    assert meta["size_bytes"] == 11
    assert meta["sha256"] == hashlib.sha256(b"hello world").hexdigest()
    assert meta["content_type"] == "text/plain"


def test_read_artifact_returns_content_and_content_type():
    from core import workspaces
    ws_id = workspaces.create_workspace(owner_user_id=_owner())
    workspaces.write_artifact(ws_id, "data.json", b'{"x":1}', "application/json")
    content, content_type = workspaces.read_artifact(ws_id, "data.json")
    assert content == b'{"x":1}'
    assert content_type == "application/json"


def test_list_artifacts_returns_metadata_for_all():
    from core import workspaces
    ws_id = workspaces.create_workspace(owner_user_id=_owner())
    workspaces.write_artifact(ws_id, "a.bin", b"AA", "application/octet-stream")
    workspaces.write_artifact(ws_id, "b.bin", b"BBB", "application/octet-stream")
    listing = workspaces.list_artifacts(ws_id)
    assert {a["name"] for a in listing} == {"a.bin", "b.bin"}
    assert all("sha256" in a and "size_bytes" in a for a in listing)


def test_write_artifact_overwrites_last_write_wins():
    from core import workspaces
    ws_id = workspaces.create_workspace(owner_user_id=_owner())
    workspaces.write_artifact(ws_id, "x", b"v1", "text/plain")
    workspaces.write_artifact(ws_id, "x", b"v2", "text/plain")
    content, _ = workspaces.read_artifact(ws_id, "x")
    assert content == b"v2"
    listing = workspaces.list_artifacts(ws_id)
    assert len(listing) == 1


def test_delete_artifact_removes_row_and_decrements():
    from core import workspaces
    from core import workspaces_errors as wse
    ws_id = workspaces.create_workspace(owner_user_id=_owner())
    workspaces.write_artifact(ws_id, "drop_me", b"bytes", "text/plain")
    workspaces.delete_artifact(ws_id, "drop_me")
    with pytest.raises(wse.ArtifactNotFound):
        workspaces.read_artifact(ws_id, "drop_me")
    ws = workspaces.get_workspace(ws_id)
    assert ws["artifact_count"] == 0
    assert ws["total_bytes"] == 0


def test_read_missing_artifact_raises():
    from core import workspaces
    from core import workspaces_errors as wse
    ws_id = workspaces.create_workspace(owner_user_id=_owner())
    with pytest.raises(wse.ArtifactNotFound):
        workspaces.read_artifact(ws_id, "ghost")


def test_write_artifact_rejects_invalid_name():
    from core import workspaces
    from core import workspaces_errors as wse
    ws_id = workspaces.create_workspace(owner_user_id=_owner())
    for bad in ["", "../escape", "foo/../bar", " ", "x" * 257, "bad name"]:
        with pytest.raises(wse.ArtifactNameInvalid):
            workspaces.write_artifact(ws_id, bad, b"x", "text/plain")


def test_write_artifact_allows_nested_names():
    from core import workspaces
    ws_id = workspaces.create_workspace(owner_user_id=_owner())
    workspaces.write_artifact(ws_id, "outputs/scanner/result.json",
                              b'{"ok":true}', "application/json")
    content, _ = workspaces.read_artifact(ws_id, "outputs/scanner/result.json")
    assert content == b'{"ok":true}'


def test_write_artifact_rejects_oversized_blob():
    from core import workspaces
    from core import workspaces_errors as wse
    ws_id = workspaces.create_workspace(owner_user_id=_owner())
    too_big = b"\x00" * (8 * 1024 * 1024 + 1)
    with pytest.raises(wse.ArtifactTooLarge):
        workspaces.write_artifact(ws_id, "big.bin", too_big,
                                  "application/octet-stream")


def test_write_artifact_enforces_workspace_quota():
    from core import workspaces
    from core import workspaces_errors as wse
    ws_id = workspaces.create_workspace(
        owner_user_id=_owner(), quota_bytes=1024,
    )
    workspaces.write_artifact(ws_id, "a", b"x" * 600,
                              "application/octet-stream")
    with pytest.raises(wse.WorkspaceQuotaExceeded):
        workspaces.write_artifact(ws_id, "b", b"y" * 500,
                                  "application/octet-stream")


def test_if_match_cas_blocks_stale_overwrite():
    from core import workspaces
    from core import workspaces_errors as wse
    ws_id = workspaces.create_workspace(owner_user_id=_owner())
    first = workspaces.write_artifact(ws_id, "cas", b"v1", "text/plain")
    with pytest.raises(wse.ArtifactConflict):
        workspaces.write_artifact(ws_id, "cas", b"v2", "text/plain",
                                  if_match_sha256="deadbeef")
    # Correct sha succeeds.
    workspaces.write_artifact(ws_id, "cas", b"v3", "text/plain",
                              if_match_sha256=first["sha256"])
    content, _ = workspaces.read_artifact(ws_id, "cas")
    assert content == b"v3"


def test_artifact_ref_resolution_inline_json():
    from core import workspaces
    owner = _owner()
    ws_id = workspaces.create_workspace(owner_user_id=owner)
    workspaces.write_artifact(ws_id, "cfg", b'{"k":"v"}', "application/json")
    resolved = workspaces.resolve_artifact_refs(
        {"input": {"_artifact_ref": f"{ws_id}/cfg"}},
        caller_owner_id=owner,
    )
    assert resolved == {"input": {"k": "v"}}


def test_artifact_ref_resolution_forbids_other_owner():
    from core import workspaces
    from core import workspaces_errors as wse
    owner = _owner()
    stranger = _owner()
    ws_id = workspaces.create_workspace(owner_user_id=owner)
    workspaces.write_artifact(ws_id, "cfg", b"data", "text/plain")
    with pytest.raises(wse.WorkspaceForbidden):
        workspaces.resolve_artifact_refs(
            {"_artifact_ref": f"{ws_id}/cfg"},
            caller_owner_id=stranger,
        )


def test_artifact_ref_resolution_allows_worker_in_run():
    from core import workspaces
    owner = _owner()
    stranger = _owner()
    ws_id = workspaces.create_workspace(owner_user_id=owner, run_id="run_x")
    workspaces.write_artifact(ws_id, "cfg", b"hello", "text/plain")
    resolved = workspaces.resolve_artifact_refs(
        {"_artifact_ref": f"{ws_id}/cfg"},
        caller_owner_id=stranger,
        allow_run_id="run_x",
    )
    assert resolved == "hello"

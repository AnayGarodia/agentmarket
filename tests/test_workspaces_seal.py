"""Seal manifest generation + Ed25519 signature verification."""

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


def test_seal_workspace_returns_manifest_signature_did():
    from core import workspaces
    ws = workspaces.create_workspace(owner_user_id=_owner())
    workspaces.write_artifact(ws, "a", b"AA", "text/plain")
    workspaces.write_artifact(ws, "b", b"BBB", "text/plain")
    result = workspaces.seal_workspace(ws)
    assert "manifest" in result
    assert "signature" in result
    assert "public_key_did" in result
    manifest = result["manifest"]
    assert manifest["schema"] == "aztea/workspace-seal/1"
    assert manifest["workspace_id"] == ws
    assert manifest["artifact_count"] == 2
    assert {a["name"] for a in manifest["artifacts"]} == {"a", "b"}
    assert result["public_key_did"].startswith("did:web:")
    assert ":workspaces:sealer" in result["public_key_did"]


def test_seal_marks_workspace_sealed():
    from core import workspaces
    ws = workspaces.create_workspace(owner_user_id=_owner())
    workspaces.write_artifact(ws, "a", b"x", "text/plain")
    workspaces.seal_workspace(ws)
    row = workspaces.get_workspace(ws)
    assert row["status"] == "sealed"
    assert row["sealed_at"] is not None
    assert row["seal_manifest"] is not None
    assert row["seal_signature"] is not None


def test_seal_is_idempotent():
    from core import workspaces
    ws = workspaces.create_workspace(owner_user_id=_owner())
    workspaces.write_artifact(ws, "a", b"x", "text/plain")
    first = workspaces.seal_workspace(ws)
    second = workspaces.seal_workspace(ws)
    assert first["signature"] == second["signature"]
    assert first["manifest"] == second["manifest"]


def test_sealed_workspace_rejects_writes():
    from core import workspaces
    from core import workspaces_errors as wse
    ws = workspaces.create_workspace(owner_user_id=_owner())
    workspaces.write_artifact(ws, "a", b"x", "text/plain")
    workspaces.seal_workspace(ws)
    with pytest.raises(wse.WorkspaceSealed):
        workspaces.write_artifact(ws, "b", b"y", "text/plain")


def test_sealed_workspace_rejects_delete():
    from core import workspaces
    from core import workspaces_errors as wse
    ws = workspaces.create_workspace(owner_user_id=_owner())
    workspaces.write_artifact(ws, "a", b"x", "text/plain")
    workspaces.seal_workspace(ws)
    with pytest.raises(wse.WorkspaceSealed):
        workspaces.delete_artifact(ws, "a")


def test_verify_seal_returns_true_for_intact_workspace():
    from core import workspaces
    ws = workspaces.create_workspace(owner_user_id=_owner())
    workspaces.write_artifact(ws, "a", b"x", "text/plain")
    workspaces.seal_workspace(ws)
    assert workspaces.verify_seal(ws) is True


def test_verify_seal_returns_false_for_unsealed():
    from core import workspaces
    ws = workspaces.create_workspace(owner_user_id=_owner())
    workspaces.write_artifact(ws, "a", b"x", "text/plain")
    assert workspaces.verify_seal(ws) is False


def test_verify_seal_returns_false_for_unknown_workspace():
    from core import workspaces
    assert workspaces.verify_seal("ws_does_not_exist_anywhere_xyz") is False


def test_verify_seal_detects_artifact_tampering():
    """Manually mutate the bytea row and confirm verify catches it."""
    from core import workspaces
    ws = workspaces.create_workspace(owner_user_id=_owner())
    workspaces.write_artifact(ws, "a", b"x", "text/plain")
    workspaces.seal_workspace(ws)
    with workspaces._connect() as conn:
        conn.execute(
            "UPDATE workspace_artifacts SET content = %s, sha256 = %s "
            "WHERE workspace_id = %s AND name = %s",
            (b"y", "deadbeef" * 8, ws, "a"),
        )
    assert workspaces.verify_seal(ws) is False


def test_manifest_signature_verifies_against_loaded_key():
    """Re-verify the signature out-of-band using core.crypto directly."""
    from core import crypto, workspaces
    ws = workspaces.create_workspace(owner_user_id=_owner())
    workspaces.write_artifact(ws, "a", b"x", "text/plain")
    result = workspaces.seal_workspace(ws)
    _private_pem, public_pem = workspaces._load_or_create_signing_keypair()
    assert crypto.verify_signature(
        public_pem, result["manifest"], result["signature"],
    ) is True

"""Unit tests for core.watchers — fingerprint strategies and crud.

Database-touching tests are covered in tests/integration/test_watchers_lifecycle.py.
This file exercises pure functions only.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from core.watchers import fingerprint as _fp
from core.watchers import models as _models


# ---------------------------------------------------------------------------
# fingerprint.http
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(
        self,
        body: bytes = b"",
        status_code: int = 200,
        headers: dict | None = None,
    ) -> None:
        self.content = body
        self.status_code = status_code
        self.headers = headers or {}

    def iter_content(self, chunk_size: int = 65536):
        # Yield in one chunk to keep tests deterministic.
        yield self.content

    def close(self) -> None:
        pass

    def json(self):
        return json.loads(self.content.decode("utf-8"))


def _patch_get(resp: _FakeResp):
    return patch("core.watchers.fingerprint.requests.get", return_value=resp)


def test_http_fingerprint_stable_for_identical_body(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_OUTBOUND_URLS", "0")
    resp = _FakeResp(body=b"<html>hello</html>")
    with _patch_get(resp):
        a, e1 = _fp.fingerprint_target("http", "https://example.com", {})
    with _patch_get(_FakeResp(body=b"<html>hello</html>")):
        b, e2 = _fp.fingerprint_target("http", "https://example.com", {})
    assert e1 is None and e2 is None
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_http_fingerprint_changes_on_content_change():
    with _patch_get(_FakeResp(body=b"v1")):
        a, _ = _fp.fingerprint_target("http", "https://example.com", {})
    with _patch_get(_FakeResp(body=b"v2")):
        b, _ = _fp.fingerprint_target("http", "https://example.com", {})
    assert a != b


def test_http_fingerprint_normalizes_whitespace():
    with _patch_get(_FakeResp(body=b"line1\r\nline2\r\n")):
        a, _ = _fp.fingerprint_target("http", "https://example.com", {})
    with _patch_get(_FakeResp(body=b"line1   \nline2  \n")):
        b, _ = _fp.fingerprint_target("http", "https://example.com", {})
    assert a == b, "trailing whitespace + CRLF must not be a content change"


def test_http_fingerprint_uses_etag_when_present():
    headers = {"ETag": "W/\"abc-123\""}
    with _patch_get(_FakeResp(body=b"ignored", headers=headers)):
        a, _ = _fp.fingerprint_target("http", "https://example.com", {})
    # Same ETag → same fingerprint regardless of body.
    with _patch_get(_FakeResp(body=b"different body", headers=headers)):
        b, _ = _fp.fingerprint_target("http", "https://example.com", {})
    assert a == b


def test_http_fingerprint_rejects_private_url(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_OUTBOUND_URLS", "0")
    fp, err = _fp.fingerprint_target("http", "http://127.0.0.1:8000/", {})
    assert fp is None
    assert err is not None and err.startswith("url_security:")


def test_http_fingerprint_caps_oversized_body():
    big = b"x" * (_fp.HTTP_BODY_BYTE_CAP + 1024)
    with _patch_get(_FakeResp(body=big)):
        fp, err = _fp.fingerprint_target("http", "https://example.com", {})
    assert fp is None
    assert err is not None and "body exceeds" in err


def test_http_fingerprint_4xx_is_error_not_change():
    with _patch_get(_FakeResp(body=b"", status_code=503)):
        fp, err = _fp.fingerprint_target("http", "https://example.com", {})
    assert fp is None
    assert err is not None and "HTTP 503" in err


# ---------------------------------------------------------------------------
# fingerprint.git
# ---------------------------------------------------------------------------


class _FakeRun:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_git_fingerprint_parses_first_sha():
    out = b"a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2\trefs/heads/main\n"
    with patch("core.watchers.fingerprint.subprocess.run", return_value=_FakeRun(stdout=out)):
        fp, err = _fp.fingerprint_target(
            "git", "https://github.com/example/repo.git", {"ref": "main"}
        )
    assert err is None
    assert fp == "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"


def test_git_fingerprint_rejects_shell_metachars():
    fp, err = _fp.fingerprint_target(
        "git", "https://github.com/x/y;rm -rf /", {"ref": "HEAD"}
    )
    assert fp is None
    assert err is not None


def test_git_fingerprint_rejects_invalid_ref():
    fp, err = _fp.fingerprint_target(
        "git", "https://github.com/x/y.git", {"ref": "bad ref with space"}
    )
    assert fp is None
    assert err is not None and "invalid ref" in err


def test_git_fingerprint_handles_nonzero_exit():
    with patch(
        "core.watchers.fingerprint.subprocess.run",
        return_value=_FakeRun(returncode=128, stderr=b"fatal: not found"),
    ):
        fp, err = _fp.fingerprint_target(
            "git", "https://github.com/x/y.git", {"ref": "HEAD"}
        )
    assert fp is None
    assert err is not None and "rc=128" in err


# ---------------------------------------------------------------------------
# fingerprint.manifest
# ---------------------------------------------------------------------------


def test_manifest_pypi_extracts_version():
    body = json.dumps({"info": {"version": "1.2.3"}}).encode("utf-8")
    with _patch_get(_FakeResp(body=body, headers={"content-type": "application/json"})):
        fp, err = _fp.fingerprint_target(
            "manifest", "ignored", {"registry": "pypi", "package": "requests"}
        )
    assert err is None and fp is not None and len(fp) == 64


def test_manifest_pypi_version_change_changes_fingerprint():
    with _patch_get(_FakeResp(body=json.dumps({"info": {"version": "1.0.0"}}).encode())):
        a, _ = _fp.fingerprint_target(
            "manifest", "x", {"registry": "pypi", "package": "p"}
        )
    with _patch_get(_FakeResp(body=json.dumps({"info": {"version": "1.0.1"}}).encode())):
        b, _ = _fp.fingerprint_target(
            "manifest", "x", {"registry": "pypi", "package": "p"}
        )
    assert a != b


def test_manifest_npm_extracts_version():
    body = json.dumps({"version": "4.5.6"}).encode("utf-8")
    with _patch_get(_FakeResp(body=body)):
        fp, err = _fp.fingerprint_target(
            "manifest", "x", {"registry": "npm", "package": "react"}
        )
    assert err is None and fp is not None


def test_manifest_404_is_error():
    with _patch_get(_FakeResp(body=b"", status_code=404)):
        fp, err = _fp.fingerprint_target(
            "manifest", "x", {"registry": "pypi", "package": "no-such-pkg-please"}
        )
    assert fp is None and err and "not found" in err


def test_manifest_invalid_registry():
    fp, err = _fp.fingerprint_target(
        "manifest", "x", {"registry": "rubygems", "package": "p"}
    )
    assert fp is None
    assert err is not None and "registry" in err


def test_manifest_invalid_package_name():
    fp, err = _fp.fingerprint_target(
        "manifest", "x", {"registry": "pypi", "package": "bad name with spaces"}
    )
    assert fp is None
    assert err is not None


def test_unknown_target_kind():
    fp, err = _fp.fingerprint_target("unknown_kind", "https://x", {})
    assert fp is None
    assert err is not None and "unknown target_kind" in err


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


def test_watcher_create_requires_delivery_channel():
    with pytest.raises(Exception) as exc:
        _models.WatcherCreate(
            agent_id="a1",
            target_kind="http",
            target_url="https://example.com",
            budget_per_day_cents=100,
        )
    assert _models.DELIVERY_REQUIRED_ERROR in str(exc.value)


def test_watcher_create_rejects_low_tick_interval():
    with pytest.raises(Exception):
        _models.WatcherCreate(
            agent_id="a1",
            target_kind="http",
            target_url="https://example.com",
            tick_interval_seconds=10,
            budget_per_day_cents=100,
            delivery_email="x@example.com",
        )


def test_watcher_create_manifest_requires_registry_and_package():
    with pytest.raises(Exception):
        _models.WatcherCreate(
            agent_id="a1",
            target_kind="manifest",
            target_url="https://registry.npmjs.org/...",
            budget_per_day_cents=100,
            delivery_email="x@example.com",
            target_meta={"registry": "npm"},  # missing package
        )


def test_watcher_create_accepts_valid():
    m = _models.WatcherCreate(
        agent_id="a1",
        target_kind="http",
        target_url="https://example.com/feed.json",
        budget_per_day_cents=200,
        delivery_email="x@example.com",
    )
    assert m.tick_interval_seconds == 900
    assert m.on_change_policy == "on_change"


def test_watcher_to_view_excludes_delivery_secret():
    row = {
        "watcher_id": "wtch_x",
        "owner_user_id": "user:1",
        "agent_id": "a1",
        "target_kind": "http",
        "target_url": "https://x",
        "target_meta_json": "{}",
        "on_change_policy": "on_change",
        "tick_interval_seconds": 900,
        "budget_per_day_cents": 100,
        "spend_today_cents": 0,
        "spend_window_date": "2026-05-09",
        "delivery_webhook_url": "https://hook",
        "delivery_email": None,
        "delivery_secret": "TOPSECRET",
        "payload_json": "{}",
        "status": "active",
        "last_fingerprint": None,
        "last_fingerprint_at": None,
        "last_fired_job_id": None,
        "last_error": None,
        "next_check_at": "2026-05-09T00:00:00+00:00",
        "created_at": "2026-05-09T00:00:00+00:00",
        "updated_at": "2026-05-09T00:00:00+00:00",
    }
    view = _models.watcher_to_view(row)
    assert "delivery_secret" not in view
    assert view["delivery_webhook_url"] == "https://hook"

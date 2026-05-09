"""Tests for the `aztea dispute` CLI command + back-compat sub-app."""
from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from aztea.cli import app


runner = CliRunner()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeClient:
    """Minimal client surface the dispute CLI exercises."""

    def __init__(
        self,
        *args: Any,
        list_jobs_response: dict | None = None,
        dispute_result: dict | None = None,
        get_dispute_result: dict | None = None,
        get_dispute_raises: Exception | None = None,
        dispute_raises: Exception | None = None,
        policy: dict | None = None,
        get_job_result: dict | None = None,
        **kwargs: Any,
    ) -> None:
        self._list_jobs_response = list_jobs_response or {"jobs": [], "next_cursor": None}
        self._dispute_result = dispute_result or {
            "dispute_id": "dsp_1",
            "status": "pending",
            "side": "caller",
            "filing_deposit_cents": 5,
        }
        self._get_dispute_result = get_dispute_result or {
            "dispute_id": "dsp_1",
            "job_id": "job-1",
            "status": "pending",
            "side": "caller",
            "filed_at": "2026-05-09T00:00:00+00:00",
            "judgments": [],
        }
        self._get_dispute_raises = get_dispute_raises
        self._dispute_raises = dispute_raises
        self._policy = policy or {
            "filing_deposit_bps": 500,
            "filing_deposit_min_cents": 5,
            "default_dispute_window_hours": 72,
            "judges_required": 2,
            "judges_total": 3,
        }
        self._get_job_result = get_job_result or {"price_cents": 100}
        self.dispute_calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def close(self) -> None:
        return None

    def list_jobs(self, *, limit: int = 50, status: str | None = None, cursor: str | None = None) -> dict:
        return self._list_jobs_response

    def get_dispute_policy(self) -> dict:
        return self._policy

    def get_job(self, job_id: str) -> dict:
        return self._get_job_result

    def dispute_job(
        self,
        job_id: str,
        *,
        reason: str,
        evidence: str | None = None,
    ) -> dict:
        self.dispute_calls.append(
            {"job_id": job_id, "reason": reason, "evidence": evidence}
        )
        if self._dispute_raises:
            raise self._dispute_raises
        return self._dispute_result

    def get_dispute(self, job_id: str) -> dict:
        if self._get_dispute_raises:
            raise self._get_dispute_raises
        return self._get_dispute_result


def _patch_client(monkeypatch, fake: _FakeClient) -> None:
    monkeypatch.setattr("aztea.cli._client", lambda **kwargs: fake)


# ---------------------------------------------------------------------------
# Direct-mode tests
# ---------------------------------------------------------------------------


def test_dispute_direct_mode_files_with_yes_flag(monkeypatch) -> None:
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)
    result = runner.invoke(
        app,
        ["dispute", "job-1", "--reason", "stale data", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert len(fake.dispute_calls) == 1
    assert fake.dispute_calls[0] == {
        "job_id": "job-1",
        "reason": "stale data",
        "evidence": None,
    }


def test_dispute_passes_evidence_through(monkeypatch) -> None:
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)
    result = runner.invoke(
        app,
        ["dispute", "job-1", "--reason", "missing CVE", "--evidence", "https://x", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert fake.dispute_calls[0]["evidence"] == "https://x"


def test_dispute_dry_run_does_not_file(monkeypatch) -> None:
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)
    result = runner.invoke(
        app,
        ["dispute", "job-1", "--reason", "test", "--dry-run", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert fake.dispute_calls == []


def test_dispute_json_mode_emits_receipt(monkeypatch) -> None:
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)
    result = runner.invoke(
        app,
        ["dispute", "job-1", "--reason", "test", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["dispute_id"] == "dsp_1"
    assert payload["job_id"] == "job-1"


def test_dispute_json_mode_dry_run_emits_estimate(monkeypatch) -> None:
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)
    result = runner.invoke(
        app,
        ["dispute", "job-1", "--reason", "test", "--dry-run", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    # 100¢ * 500 bps / 10000 = 5¢; equals the min, so deposit_cents = 5.
    assert payload["estimated_deposit_cents"] == 5
    assert fake.dispute_calls == []


def test_dispute_json_mode_requires_reason(monkeypatch) -> None:
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)
    result = runner.invoke(app, ["dispute", "job-1", "--json"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# --status mode
# ---------------------------------------------------------------------------


def test_dispute_status_renders_existing(monkeypatch) -> None:
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)
    result = runner.invoke(app, ["dispute", "--status", "job-1", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["dispute_id"] == "dsp_1"
    # No filing call in status mode.
    assert fake.dispute_calls == []


# ---------------------------------------------------------------------------
# Wizard-mode gates
# ---------------------------------------------------------------------------


def test_dispute_wizard_refuses_in_json_mode(monkeypatch) -> None:
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)
    result = runner.invoke(app, ["dispute", "--json"])
    assert result.exit_code == 2
    # No filing happens.
    assert fake.dispute_calls == []


def test_dispute_wizard_refuses_when_not_tty(monkeypatch) -> None:
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)
    # CliRunner does not provide a TTY, so wizard should refuse.
    result = runner.invoke(app, ["dispute"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def test_dispute_already_filed_renders_friendly_hint(monkeypatch) -> None:
    from aztea.errors import ConflictError

    err = ConflictError(
        status_code=409,
        message="A dispute already exists for this job.",
        detail=None,
        body=None,
        code="dispute.already_filed",
    )
    fake = _FakeClient(dispute_raises=err)
    _patch_client(monkeypatch, fake)
    result = runner.invoke(
        app, ["dispute", "job-1", "--reason", "test", "--yes"]
    )
    assert result.exit_code == 1
    assert "aztea dispute --status" in result.output


def test_dispute_window_expired_renders_friendly_hint(monkeypatch) -> None:
    from aztea.errors import APIError

    err = APIError(
        status_code=400,
        message="Dispute window has expired for this job.",
        detail=None,
        body=None,
        code="dispute.window_expired",
    )
    fake = _FakeClient(dispute_raises=err)
    _patch_client(monkeypatch, fake)
    result = runner.invoke(
        app, ["dispute", "job-1", "--reason", "test", "--yes"]
    )
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Back-compat: aztea jobs dispute
# ---------------------------------------------------------------------------


def test_jobs_dispute_subcommand_still_works(monkeypatch) -> None:
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)
    result = runner.invoke(
        app,
        ["jobs", "dispute", "job-1", "--reason", "test", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["dispute_id"] == "dsp_1"
    assert fake.dispute_calls == [
        {"job_id": "job-1", "reason": "test", "evidence": None}
    ]

"""POST /jobs/{id}/rating must always return a typed `clawback` payload.

Audit 2026-05-16 #15: the response field was always `None`, even when the
clawback path was deliberately skipped (no curve, top rating, settlement
pending). Callers could not distinguish "we didn't try" from "we tried and
got 0".
"""

from tests.integration.support import *  # noqa: F403


def _complete_job(client, worker_key: str, caller_key: str, agent_id: str) -> str:
    job = _create_job_via_api(client, caller_key, agent_id=agent_id)
    job_id = job["job_id"]
    claim = client.post(
        f"/jobs/{job_id}/claim",
        headers=_auth_headers(worker_key),
        json={"lease_seconds": 60},
    )
    assert claim.status_code == 200, claim.text
    claim_token = claim.json()["claim_token"]
    done = client.post(
        f"/jobs/{job_id}/complete",
        headers=_auth_headers(worker_key),
        json={"output_payload": {"ok": True}, "claim_token": claim_token},
    )
    assert done.status_code == 200, done.text
    return job_id


def test_rating_response_carries_typed_clawback_dict(client):
    worker = _register_user()
    caller = _register_user()
    _fund_user_wallet(caller, 500)
    agent_id = _register_agent_via_api(
        client,
        worker["raw_api_key"],
        name=f"Clawback Test Agent {uuid.uuid4().hex[:6]}",
    )
    job_id = _complete_job(client, worker["raw_api_key"], caller["raw_api_key"], agent_id)

    resp = client.post(
        f"/jobs/{job_id}/rating",
        headers=_auth_headers(caller["raw_api_key"]),
        json={"rating": 5},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    clawback = body["clawback"]
    assert isinstance(clawback, dict), (
        f"clawback must be a dict, not None — audit #15 regression. body={body!r}"
    )
    assert clawback["applied"] is False
    assert "reason" in clawback
    assert clawback["clawback_cents"] == 0


def test_rating_response_clawback_has_reason_for_low_rating_without_curve(client):
    """When an agent has no payout_curve, even a 1-star rating reports
    `no_payout_curve` rather than silently returning null."""
    worker = _register_user()
    caller = _register_user()
    _fund_user_wallet(caller, 500)
    agent_id = _register_agent_via_api(
        client,
        worker["raw_api_key"],
        name=f"Clawback Test Agent {uuid.uuid4().hex[:6]}",
    )
    job_id = _complete_job(client, worker["raw_api_key"], caller["raw_api_key"], agent_id)

    resp = client.post(
        f"/jobs/{job_id}/rating",
        headers=_auth_headers(caller["raw_api_key"]),
        json={"rating": 1},
    )
    assert resp.status_code == 201, resp.text
    clawback = resp.json()["clawback"]
    assert isinstance(clawback, dict)
    assert clawback["applied"] is False
    assert clawback["reason"] in {"no_payout_curve", "settlement_pending"}

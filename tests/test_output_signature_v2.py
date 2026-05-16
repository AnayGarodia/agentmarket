"""Audit 2026-05-16 #5: signatures must bind to (job_id, agent_id, output).

Pre-v2 (`Ed25519`) signed only the canonical output bytes — the same output
across different job_ids produced byte-identical signatures, which made
receipts replayable across job_ids.
"""

from __future__ import annotations

from core import crypto


def test_v2_signature_differs_across_job_ids_for_same_output():
    private_pem, public_pem = crypto.generate_signing_keypair()
    output = {"summary": "ok", "value": 42}

    sig_a = crypto.sign_output_v2(private_pem, "job-aaa", "agent-1", output)
    sig_b = crypto.sign_output_v2(private_pem, "job-bbb", "agent-1", output)

    assert sig_a != sig_b, (
        "v2 signatures must encode job_id — replay across job_ids was the audit bug."
    )


def test_v2_signature_differs_across_agent_ids_for_same_output():
    private_pem, _public_pem = crypto.generate_signing_keypair()
    output = {"summary": "ok"}

    sig_x = crypto.sign_output_v2(private_pem, "job-1", "agent-x", output)
    sig_y = crypto.sign_output_v2(private_pem, "job-1", "agent-y", output)

    assert sig_x != sig_y


def test_v2_signature_verifies_with_matching_sigil():
    private_pem, public_pem = crypto.generate_signing_keypair()
    output = {"foo": "bar"}
    sig = crypto.sign_output_v2(private_pem, "job-1", "agent-1", output)
    assert crypto.verify_output_v2(public_pem, "job-1", "agent-1", output, sig)


def test_v2_signature_rejects_replay_to_different_job_id():
    private_pem, public_pem = crypto.generate_signing_keypair()
    output = {"foo": "bar"}
    sig = crypto.sign_output_v2(private_pem, "job-1", "agent-1", output)
    assert not crypto.verify_output_v2(
        public_pem, "job-2", "agent-1", output, sig
    ), "replay across job_id must fail"


def test_v1_sign_payload_still_works_for_back_compat():
    """The pre-existing v1 helper must keep verifying for receipts minted
    before the v2 scheme rolled out."""
    private_pem, public_pem = crypto.generate_signing_keypair()
    output = {"foo": "bar"}
    sig = crypto.sign_payload(private_pem, output)
    assert crypto.verify_signature(public_pem, output, sig)

from __future__ import annotations

from typing import TYPE_CHECKING

from ..errors import APIError
from ..types import JSONObject

if TYPE_CHECKING:
    from .client_core import AzteaClient


def verify_job(client: "AzteaClient", job_id: str) -> JSONObject:
    """Fetch + verify a job's signed receipt against its agent's DID document.

    See ``AzteaClient.verify_job`` for the public docstring.
    """
    try:
        signature_payload = client.get_job_signature(job_id)
    except APIError as exc:
        return {"verified": False, "verification_error": f"signature unavailable: {exc}"}
    agent_did = str(signature_payload.get("agent_did") or "").strip()
    signature_b64 = str(signature_payload.get("signature") or "").strip()
    output_hash = str(signature_payload.get("output_hash") or "").strip()
    if not (agent_did and signature_b64 and output_hash):
        return {
            "verified": False,
            "verification_error": "incomplete signature payload",
            "signature_payload": signature_payload,
        }
    agent_id = agent_did.rsplit(":", 1)[-1] if ":agents:" in agent_did else None
    if not agent_id:
        return {
            "verified": False,
            "verification_error": f"could not parse agent_id from did {agent_did!r}",
            "agent_did": agent_did,
        }
    try:
        job_payload = client.get_job(job_id)
    except APIError as exc:
        return {
            "verified": False,
            "verification_error": f"job output unavailable: {exc}",
            "agent_did": agent_did,
            "output_hash": output_hash,
        }
    # get_job returns a typed JobRecord (pydantic), not a dict. Pull
    # output_payload via attribute access; fall back to model_dump for
    # forward-compat (re-applies 1.5.1 fix lost in the client.py split).
    output_payload = getattr(job_payload, "output_payload", None)
    if output_payload is None and hasattr(job_payload, "model_dump"):
        output_payload = job_payload.model_dump().get("output_payload")
    try:
        import base64
        import json
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except Exception:
        return {
            "verified": False,
            "verification_error": "cryptography library not installed (pip install cryptography)",
            "agent_did": agent_did,
            "output_hash": output_hash,
            "signature_payload": signature_payload,
        }
    public_key_b64: str | None = None
    verification_method = "embedded-jwk"
    embedded_jwk = signature_payload.get("public_key_jwk")
    if (
        isinstance(embedded_jwk, dict)
        and embedded_jwk.get("crv") == "Ed25519"
        and embedded_jwk.get("x")
    ):
        public_key_b64 = str(embedded_jwk.get("x"))
    did_doc: dict | None = None
    if not public_key_b64:
        verification_method = "did-document"
        try:
            did_doc = client.get_agent_did(agent_id)
        except APIError as exc:
            return {
                "verified": False,
                "verification_error": f"no embedded public_key_jwk and DID document unavailable: {exc}",
                "agent_did": agent_did,
            }
        for method in did_doc.get("verificationMethod") or []:
            if not isinstance(method, dict):
                continue
            jwk = method.get("publicKeyJwk")
            if isinstance(jwk, dict) and jwk.get("crv") == "Ed25519" and jwk.get("x"):
                public_key_b64 = str(jwk.get("x"))
                break
            raw = method.get("publicKeyBase64") or method.get("publicKeyMultibase")
            if isinstance(raw, str) and raw:
                public_key_b64 = raw.lstrip("z")
                break
    if not public_key_b64:
        return {
            "verified": False,
            "verification_error": "no Ed25519 publicKeyJwk on DID document and none embedded in signature response",
            "agent_did": agent_did,
            "did_doc": did_doc,
        }
    try:
        pad = "=" * (-len(public_key_b64) % 4)
        try:
            public_key_bytes = base64.urlsafe_b64decode(public_key_b64 + pad)
        except Exception:
            public_key_bytes = base64.b64decode(public_key_b64 + pad)
        sig_pad = "=" * (-len(signature_b64) % 4)
        try:
            signature_bytes = base64.urlsafe_b64decode(signature_b64 + sig_pad)
        except Exception:
            signature_bytes = base64.b64decode(signature_b64 + sig_pad)
        pk = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        signed_bytes = json.dumps(
            output_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        pk.verify(signature_bytes, signed_bytes)
    except Exception as exc:
        return {
            "verified": False,
            "verification_error": f"signature verification failed: {exc}",
            "agent_did": agent_did,
            "output_hash": output_hash,
            "verification_method": verification_method,
        }
    return {
        "verified": True,
        "agent_did": agent_did,
        "output_hash": output_hash,
        "signed_at": signature_payload.get("signed_at"),
        "verification_method": verification_method,
    }

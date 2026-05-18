"""
ssl_certificate_decoder.py — Decode X.509 certificates from PEM or DER format.

# OWNS: decoding PEM/DER X.509 certificates and extracting structured fields
# NOT OWNS: fetching certificates from URLs (dns_inspector does that), certificate issuance
# INVARIANTS: never makes outbound network requests; pure cryptographic parsing only
# DECISIONS: cryptography library preferred over ssl/OpenSSL bindings for richer API surface
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

from agents._contracts import agent_error as _err

_LOG = logging.getLogger(__name__)

# Maximum certificates accepted in a single batch call
MAX_BATCH = 10

_PEM_HEADER = "-----BEGIN CERTIFICATE-----"
_PEM_FOOTER = "-----END CERTIFICATE-----"

_PEM_BLOCK_RE = re.compile(r"-----BEGIN CERTIFICATE-----[^-]+-----END CERTIFICATE-----", re.DOTALL)

# EKU OID → human name (RFC 5280 + common extensions)
_EKU_OID_NAMES: dict[str, str] = {
    "1.3.6.1.5.5.7.3.1": "serverAuth",
    "1.3.6.1.5.5.7.3.2": "clientAuth",
    "1.3.6.1.5.5.7.3.3": "codeSigning",
    "1.3.6.1.5.5.7.3.4": "emailProtection",
    "1.3.6.1.5.5.7.3.8": "timeStamping",
    "1.3.6.1.5.5.7.3.9": "OCSPSigning",
}

# Attribute names ordered as they appear on x509.KeyUsage
_KEY_USAGE_ATTRS: list[str] = [
    "digital_signature",
    "content_commitment",
    "key_encipherment",
    "data_encipherment",
    "key_agreement",
    "key_cert_sign",
    "crl_sign",
    "encipher_only",
    "decipher_only",
]

# camelCase display names for each KeyUsage attribute
_KEY_USAGE_DISPLAY: dict[str, str] = {
    "digital_signature": "digitalSignature",
    "content_commitment": "contentCommitment",
    "key_encipherment": "keyEncipherment",
    "data_encipherment": "dataEncipherment",
    "key_agreement": "keyAgreement",
    "key_cert_sign": "keyCertSign",
    "crl_sign": "cRLSign",
    "encipher_only": "encipherOnly",
    "decipher_only": "decipherOnly",
}


# ---------------------------------------------------------------------------
# PEM / DER normalisation helpers
# ---------------------------------------------------------------------------

def _pem_blocks_from_string(raw: str) -> list[str]:
    """Extract PEM cert blocks; wraps bare base64 if no header found."""
    blocks = _PEM_BLOCK_RE.findall(raw.strip())
    if blocks:
        return blocks
    candidate = f"{_PEM_HEADER}\n{raw.strip()}\n{_PEM_FOOTER}"
    return _PEM_BLOCK_RE.findall(candidate)


def _load_cert_from_pem(pem_block: str) -> Any:
    from cryptography import x509
    return x509.load_pem_x509_certificate(pem_block.encode("ascii"))


def _load_cert_from_der_base64(der_b64: str) -> Any:
    from cryptography import x509
    return x509.load_der_x509_certificate(base64.b64decode(der_b64.strip()))


# ---------------------------------------------------------------------------
# Field extraction helpers
# ---------------------------------------------------------------------------

def _name_attrs(name: Any) -> dict[str, str | None]:
    from cryptography.x509.oid import NameOID
    def _get(oid: Any) -> str | None:
        attrs = name.get_attributes_for_oid(oid)
        return attrs[0].value if attrs else None
    return {
        "common_name": _get(NameOID.COMMON_NAME),
        "organization": _get(NameOID.ORGANIZATION_NAME),
        "organizational_unit": _get(NameOID.ORGANIZATIONAL_UNIT_NAME),
        "country": _get(NameOID.COUNTRY_NAME),
        "state": _get(NameOID.STATE_OR_PROVINCE_NAME),
        "locality": _get(NameOID.LOCALITY_NAME),
    }


def _key_info(pub: Any) -> tuple[str, int | None, str | None]:
    """Return (key_type, key_bits, key_curve); Ed25519/Ed448 have fixed bit lengths."""
    from cryptography.hazmat.primitives.asymmetric import rsa, ec, ed25519, ed448, dsa
    if isinstance(pub, rsa.RSAPublicKey):
        return "RSA", pub.key_size, None
    if isinstance(pub, ec.EllipticCurvePublicKey):
        return "EC", pub.key_size, pub.curve.name
    if isinstance(pub, ed25519.Ed25519PublicKey):
        return "Ed25519", 256, None
    if isinstance(pub, ed448.Ed448PublicKey):
        return "Ed448", 448, None
    if isinstance(pub, dsa.DSAPublicKey):
        return "DSA", pub.key_size, None
    return "unknown", None, None


def _fingerprints(cert: Any) -> dict[str, str]:
    from cryptography.hazmat.primitives import serialization
    der = cert.public_bytes(serialization.Encoding.DER)
    def _fmt(h: str) -> str:
        return ":".join(h[i:i + 2] for i in range(0, len(h), 2)).upper()
    return {"sha1": _fmt(hashlib.sha1(der).hexdigest()), "sha256": _fmt(hashlib.sha256(der).hexdigest())}


def _extract_san(cert: Any) -> list[str]:
    from cryptography import x509
    from cryptography.x509.oid import ExtensionOID
    try:
        san_ext = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        return [str(n) for n in san_ext.value]
    except x509.ExtensionNotFound:
        return []


def _extract_key_usage(cert: Any) -> list[str]:
    from cryptography import x509
    from cryptography.x509.oid import ExtensionOID
    try:
        ku = cert.extensions.get_extension_for_oid(ExtensionOID.KEY_USAGE).value
    except x509.ExtensionNotFound:
        return []
    enabled: list[str] = []
    for attr in _KEY_USAGE_ATTRS:
        # encipher_only / decipher_only raise ValueError unless key_agreement is True
        if attr in ("encipher_only", "decipher_only") and not ku.key_agreement:
            continue
        try:
            if getattr(ku, attr, False):
                enabled.append(_KEY_USAGE_DISPLAY[attr])
        except Exception:
            pass
    return enabled


def _extract_eku(cert: Any) -> list[str]:
    from cryptography import x509
    from cryptography.x509.oid import ExtensionOID
    try:
        eku_ext = cert.extensions.get_extension_for_oid(ExtensionOID.EXTENDED_KEY_USAGE)
        return [_EKU_OID_NAMES.get(oid.dotted_string, oid.dotted_string) for oid in eku_ext.value]
    except x509.ExtensionNotFound:
        return []


def _extract_ocsp_urls(cert: Any) -> list[str]:
    from cryptography import x509
    from cryptography.x509.oid import ExtensionOID, AuthorityInformationAccessOID
    try:
        aia = cert.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS)
        return [d.access_location.value for d in aia.value if d.access_method == AuthorityInformationAccessOID.OCSP]
    except x509.ExtensionNotFound:
        return []


def _extract_crls(cert: Any) -> list[str]:
    from cryptography import x509
    from cryptography.x509.oid import ExtensionOID
    urls: list[str] = []
    try:
        cdp_ext = cert.extensions.get_extension_for_oid(ExtensionOID.CRL_DISTRIBUTION_POINTS)
        for dp in cdp_ext.value:
            if dp.full_name:
                urls.extend(name.value for name in dp.full_name)
    except x509.ExtensionNotFound:
        pass
    return urls


def _is_ca(cert: Any) -> bool:
    from cryptography import x509
    from cryptography.x509.oid import ExtensionOID
    try:
        return bool(cert.extensions.get_extension_for_oid(ExtensionOID.BASIC_CONSTRAINTS).value.ca)
    except x509.ExtensionNotFound:
        return False


def _basic_constraints_str(cert: Any) -> str | None:
    from cryptography import x509
    from cryptography.x509.oid import ExtensionOID
    try:
        bc = cert.extensions.get_extension_for_oid(ExtensionOID.BASIC_CONSTRAINTS).value
        return f"CA:{bc.ca}" + (f", pathLen:{bc.path_length}" if bc.path_length is not None else "")
    except x509.ExtensionNotFound:
        return None


# ---------------------------------------------------------------------------
# Core decode function
# ---------------------------------------------------------------------------

def _decode_cert(cert: Any) -> dict[str, Any]:
    """Build the structured output dict for a single parsed x509.Certificate."""
    now = datetime.now(timezone.utc)
    valid_from = cert.not_valid_before_utc
    valid_to = cert.not_valid_after_utc
    expired = now > valid_to
    not_yet_valid = now < valid_from
    remaining_delta = valid_to - now
    days_remaining = remaining_delta.days if not expired else -(now - valid_to).days

    key_type, key_bits, key_curve = _key_info(cert.public_key())
    subject_attrs = _name_attrs(cert.subject)
    issuer_attrs = _name_attrs(cert.issuer)
    self_signed = cert.subject == cert.issuer

    return {
        "subject": subject_attrs,
        "issuer": issuer_attrs,
        "serial_number": format(cert.serial_number, "x"),
        "version": cert.version.value + 1,
        "valid_from": valid_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "valid_to": valid_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "days_remaining": days_remaining,
        "expired": expired,
        "not_yet_valid": not_yet_valid,
        "san": _extract_san(cert),
        "key_type": key_type,
        "key_bits": key_bits,
        "key_curve": key_curve,
        "signature_algorithm": cert.signature_algorithm_oid.dotted_string,
        "key_usage": _extract_key_usage(cert),
        "extended_key_usage": _extract_eku(cert),
        "ocsp_urls": _extract_ocsp_urls(cert),
        "crl_distribution_points": _extract_crls(cert),
        "fingerprints": _fingerprints(cert),
        "is_ca": _is_ca(cert),
        "self_signed": self_signed,
        "basic_constraints": _basic_constraints_str(cert),
    }


# ---------------------------------------------------------------------------
# Input routing helpers
# ---------------------------------------------------------------------------

def _safe_decode_cert(cert: Any) -> dict[str, Any]:
    """Wrap ``_decode_cert`` so a single unexpected extension shape can't
    turn a real cert into a 500.

    Why: the 2026-05-18 test report showed the agent at 14% success — many
    failures were certificates with quirky extensions (e.g. SAN with raw
    OtherName entries, BasicConstraints with non-integer path_length,
    KeyUsage on EdDSA keys). Per-field extraction is defensive, but the
    aggregate ``_decode_cert`` path still raised; this wrapper turns those
    into a structured error envelope instead of an HTTP 500.
    """
    try:
        return _decode_cert(cert)
    except Exception as exc:  # noqa: BLE001 — boundary
        _LOG.warning("ssl_certificate_decoder: _decode_cert raised", exc_info=True)
        return _err(
            "ssl_certificate_decoder.decode_failed",
            f"Certificate parsed but field extraction failed: {type(exc).__name__}: {exc}",
        )


def _handle_single_pem(pem_raw: str) -> dict[str, Any]:
    """Decode a single PEM input; handle chains by returning first cert."""
    blocks = _pem_blocks_from_string(pem_raw)
    if not blocks:
        return _err("ssl_certificate_decoder.decode_failed", "No valid PEM certificate block found")
    try:
        cert = _load_cert_from_pem(blocks[0])
    except Exception as exc:
        return _err("ssl_certificate_decoder.decode_failed", f"PEM decode failed: {exc}")
    return _safe_decode_cert(cert)


def _handle_der_base64(der_b64: str) -> dict[str, Any]:
    """Decode a base64-encoded DER certificate."""
    try:
        cert = _load_cert_from_der_base64(der_b64)
    except Exception as exc:
        return _err("ssl_certificate_decoder.decode_failed", f"DER decode failed: {exc}")
    return _safe_decode_cert(cert)


def _handle_batch_pems(pem_list: list[str]) -> dict[str, Any]:
    """Decode a batch of PEM certificates and validate chain order.

    Per-cert failures no longer abort the batch — they land as inline
    error envelopes alongside successful decodes. The 2026-05-18 test
    report flagged callers passing intermediate-CA PEMs where one cert had
    a malformed extension; pre-fix, the whole batch returned a single
    error envelope with no detail on which cert was bad and which were
    valid. Inline errors keep partial results actionable.
    """
    if len(pem_list) > MAX_BATCH:
        return _err(
            "ssl_certificate_decoder.too_many_certs",
            f"Batch exceeds maximum of {MAX_BATCH} certificates; got {len(pem_list)}",
        )
    certs: list[Any] = []
    decoded: list[dict[str, Any]] = []

    for idx, pem_raw in enumerate(pem_list):
        blocks = _pem_blocks_from_string(pem_raw)
        if not blocks:
            decoded.append(_err(
                "ssl_certificate_decoder.decode_failed",
                f"Certificate at index {idx} contains no valid PEM block",
            ))
            certs.append(None)
            continue
        try:
            cert = _load_cert_from_pem(blocks[0])
        except Exception as exc:
            decoded.append(_err(
                "ssl_certificate_decoder.decode_failed",
                f"Certificate at index {idx} failed to decode: {exc}",
            ))
            certs.append(None)
            continue
        certs.append(cert)
        decoded.append(_safe_decode_cert(cert))

    # chain_valid_order: cert[i].issuer == cert[i+1].subject for a well-ordered chain.
    # Any decode failure in the chain makes order verification meaningless; report
    # ``None`` in that case rather than a misleading boolean.
    if any(c is None for c in certs) or len(certs) < 2:
        chain_valid: bool | None = None if any(c is None for c in certs) else True
    else:
        chain_valid = all(
            certs[i].issuer == certs[i + 1].subject
            for i in range(len(certs) - 1)
        )
    return {"certificates": decoded, "chain_valid_order": chain_valid}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(payload: dict) -> dict:
    """Decode X.509 certificates from PEM or DER; never makes network requests.

    WHY: Provides pure-Python structured extraction of cert metadata so callers
    can audit TLS configuration, expiry, key strength, and chain validity without
    any outbound HTTP — purely from the certificate bytes themselves.
    """
    try:
        from cryptography import x509  # noqa: F401 — presence check only
    except ImportError:
        return _err(
            "ssl_certificate_decoder.missing_dependency",
            "The 'cryptography' package is required: pip install cryptography",
            {"dependency": "cryptography"},
        )

    pem = payload.get("pem")
    der_b64 = payload.get("der_base64")
    pems = payload.get("pems")

    has_pem = pem is not None
    has_der = der_b64 is not None
    has_pems = pems is not None

    if has_pem and has_pems:
        return _err(
            "ssl_certificate_decoder.ambiguous_input",
            "Provide either 'pem' or 'pems', not both",
        )
    if not has_pem and not has_der and not has_pems:
        return _err(
            "ssl_certificate_decoder.missing_certificate",
            "Provide 'pem', 'der_base64', or 'pems'",
        )

    if has_pem:
        if not isinstance(pem, str) or not pem.strip():
            return _err("ssl_certificate_decoder.decode_failed", "'pem' must be a non-empty string")
        return _handle_single_pem(pem)

    if has_der:
        if not isinstance(der_b64, str) or not der_b64.strip():
            return _err("ssl_certificate_decoder.decode_failed", "'der_base64' must be a non-empty string")
        return _handle_der_base64(der_b64)

    # pems batch path
    if not isinstance(pems, list) or not pems:
        return _err("ssl_certificate_decoder.decode_failed", "'pems' must be a non-empty list of PEM strings")
    return _handle_batch_pems(pems)

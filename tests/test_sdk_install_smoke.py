"""SDK install-time smoke checks.

Audit 2026-05-16 #2 — guard against silently dropping ``cryptography`` from
the SDK's runtime deps, which causes ``manage_job(verify)`` in the MCP
runtime to return ``verified: false`` even when server-side verify works.
"""

import pathlib

import tomllib


_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SDK_PYPROJECT = _REPO_ROOT / "sdks" / "python-sdk" / "pyproject.toml"


def test_sdk_pyproject_declares_cryptography_dep():
    with _SDK_PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    deps = data["project"]["dependencies"]
    assert any(
        dep.split(">=")[0].split("==")[0].strip() == "cryptography"
        for dep in deps
    ), f"cryptography missing from SDK runtime deps: {deps}"


def test_sdk_verify_module_importable_and_exposes_verify_job():
    """``_verify`` defines its own fallback when cryptography is missing,
    but its top-level imports must always succeed for the MCP server to
    boot. Imports the module and confirms the public API surface."""
    import sys

    sdk_root = _REPO_ROOT / "sdks" / "python-sdk"
    if str(sdk_root) not in sys.path:
        sys.path.insert(0, str(sdk_root))
    from aztea._client_internals import _verify

    assert callable(_verify.verify_job)

"""Backward-compatibility guards for the ``aztea.client`` subpackage split.

The implementation moved to ``aztea._client_internals`` to stay under the
1000-line CI budget. The public surface must keep working unchanged: this
test asserts every import path that was valid before the split is still
valid, and that the runtime patch points used by the existing test suite
(notably ``aztea.client.time.sleep``) remain attribute-accessible.
"""

from __future__ import annotations

from unittest.mock import patch


def test_top_level_aztea_client_import() -> None:
    from aztea import AzteaClient

    assert callable(AzteaClient)


def test_aztea_client_module_namespace_imports() -> None:
    from aztea.client import (
        AuthNamespace,
        AzteaClient,
        DisputesNamespace,
        RegistryNamespace,
        WalletsNamespace,
    )

    assert all(callable(cls) for cls in (
        AzteaClient, AuthNamespace, WalletsNamespace,
        RegistryNamespace, DisputesNamespace,
    ))


def test_aztea_client_internal_helpers_still_reexported() -> None:
    """``aztea.agent`` imports ``_coerce_payload`` from ``aztea.client``.

    Other downstream code may rely on the same. Keep these re-exported.
    """
    from aztea.client import (
        _NamespaceBase,
        _coerce_model,
        _coerce_payload,
        _ensure_object,
        _verify_contract,
    )

    assert _coerce_payload({"k": 1}) == {"k": 1}
    assert _coerce_payload("not-a-dict") == {}
    assert _ensure_object({"x": 1}, context="test") == {"x": 1}
    assert callable(_coerce_model)
    assert callable(_verify_contract)
    assert _NamespaceBase is not None


def test_aztea_client_time_attribute_remains_patchable() -> None:
    """`patch("aztea.client.time.sleep")` is used in tests/test_client.py.

    The split must keep ``time`` accessible as an attribute on ``aztea.client``
    so existing patch sites do not silently no-op.
    """
    import aztea.client

    assert hasattr(aztea.client, "time")
    assert hasattr(aztea.client.time, "sleep")
    with patch("aztea.client.time.sleep") as sleep_mock:
        aztea.client.time.sleep(0)
        sleep_mock.assert_called_once_with(0)


def test_client_instantiates_and_namespaces_attached() -> None:
    from aztea.client import (
        AuthNamespace,
        AzteaClient,
        DisputesNamespace,
        RegistryNamespace,
        WalletsNamespace,
    )

    client = AzteaClient(api_key="test-key", base_url="http://example.invalid")
    try:
        assert isinstance(client.auth, AuthNamespace)
        assert isinstance(client.wallets, WalletsNamespace)
        assert isinstance(client.registry, RegistryNamespace)
        assert isinstance(client.disputes, DisputesNamespace)
        # JobsNamespace lives in aztea.jobs; access it without asserting type
        # to avoid pinning that module's import path.
        assert client.jobs is not None
        assert client.base_url == "http://example.invalid"
    finally:
        client.close()


def test_aztea_client_supports_context_manager() -> None:
    from aztea import AzteaClient

    with AzteaClient(api_key="test-key", base_url="http://example.invalid") as client:
        assert client._api_key == "test-key"
    # No explicit assertion on close-state; getting here without exception is the contract.


def test_aztea_client_module_attribute_is_stable() -> None:
    """Public symbol's ``__module__`` should stay ``aztea._client_internals.client_core``
    (or at minimum, an ``aztea.*`` path). External tools that introspect this
    should not see a sudden jump out of the package."""
    from aztea import AzteaClient

    assert AzteaClient.__module__.startswith("aztea")

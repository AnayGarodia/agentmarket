"""Public ``aztea.client`` module.

The implementation lives in :mod:`aztea._client_internals` to keep individual
files under the repo's 1000-line CI budget. This module re-exports the public
surface unchanged: ``from aztea.client import AzteaClient`` and the namespace
classes continue to work, and ``patch("aztea.client.time.sleep")`` still works
because :mod:`time` is imported here.
"""

from __future__ import annotations

import time  # re-exported so existing test patches (`aztea.client.time.sleep`) keep working

from ._client_internals._helpers import (
    _NamespaceBase,
    _coerce_model,
    _coerce_payload,
    _ensure_object,
    _verify_contract,
)
from ._client_internals.client_core import AzteaClient
from ._client_internals.namespaces import (
    AuthNamespace,
    DisputesNamespace,
    RegistryNamespace,
    WalletsNamespace,
)

__all__ = [
    "AzteaClient",
    "AuthNamespace",
    "WalletsNamespace",
    "RegistryNamespace",
    "DisputesNamespace",
    # Internal helpers re-exported for backward compatibility with callers
    # (e.g. ``aztea.agent`` imports ``_coerce_payload``).
    "_NamespaceBase",
    "_coerce_model",
    "_coerce_payload",
    "_ensure_object",
    "_verify_contract",
    "time",
]

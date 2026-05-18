"""Typed exceptions for the workspaces subsystem.

HTTP routes catch these and translate to the matching error code from
core/error_codes.py. Module-internal callers use the exception types so
they don't have to thread error-code strings through the call stack.

Mirrors the pattern in core/sandbox/models.py.
"""

from __future__ import annotations


class WorkspaceError(Exception):
    """Base class — every workspace error inherits from this."""


class WorkspaceNotFound(WorkspaceError):
    """No workspace row matches the given workspace_id."""


class WorkspaceForbidden(WorkspaceError):
    """Caller does not own this workspace and is not a worker-in-run."""


class WorkspaceSealed(WorkspaceError):
    """Workspace is sealed; mutating operations are not permitted."""


class WorkspaceQuotaExceeded(WorkspaceError):
    """Adding this artifact would exceed quota_bytes for the workspace."""


class ArtifactNotFound(WorkspaceError):
    """No artifact with that name in this workspace."""


class ArtifactTooLarge(WorkspaceError):
    """Single artifact exceeds the 8 MiB per-artifact cap."""


class ArtifactNameInvalid(WorkspaceError):
    """Artifact name fails validation (regex, length, path traversal)."""


class ArtifactConflict(WorkspaceError):
    """If-Match header sha256 does not match current artifact sha256."""


class BackingEvicted(WorkspaceError):
    """Sandbox-backed workspace's sandbox is no longer available."""


class SealSigningFailed(WorkspaceError):
    """Ed25519 signing of the seal manifest failed."""

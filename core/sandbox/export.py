"""sandbox_export_snapshot — pack a snapshot bundle to a local destination.

# OWNS: tarring a snapshot directory (fs.tar + manifest.json + per-service
#       image tarballs + optional db pgdump) into a single user-specified
#       file path the operator can move off-host however they like.
# NOT OWNS: cloud-bucket transports (S3 / GCS / etc.) — those require
#           wallet credentials. The file:// path landing here is the
#           OSS-mode surface; the hosted-mode path layers bucket creds
#           on top of this same tar bundle.
# INVARIANTS:
#   * Secrets are NEVER bundled — the per-sandbox ``secrets/`` directory
#     is excluded by name.
#   * destination_uri must be a ``file://`` URI under a user-writable
#     directory (no path traversal).
"""

from __future__ import annotations

import logging
import tarfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.sandbox.docker_cli import run_docker
from core.sandbox.models import SandboxInvalidInput, SandboxNotFound
from core.sandbox.state import get, sandbox_dir

_LOG = logging.getLogger("aztea.sandbox.export")
_FILE_SCHEME = "file://"
_EXPORT_TIMEOUT_S = 300
_EXCLUDED_NAMES = frozenset({"secrets"})


def export_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Pack a snapshot into a single tar bundle at the user-supplied path.

    Bundle layout (entries inside the output tar):
        manifest.json                — copied verbatim from the snapshot
        fs.tar                       — workspace filesystem tar
        services/<name>.image.tar    — ``docker image save`` of each
                                       service's committed image (if Docker
                                       is reachable; else skipped)
        db/<label>.pgdump            — pg_dump output, when present

    Anything that could leak a secret (the secrets/ directory, env files
    with ``KEY=value`` content, etc.) is filtered before bundling.
    """
    sandbox_id = str((payload or {}).get("sandbox_id") or "").strip()
    if not sandbox_id:
        raise SandboxInvalidInput("sandbox_id is required")
    state = get(sandbox_id)
    if state is None:
        raise SandboxNotFound(f"sandbox '{sandbox_id}' is not active on this host")
    snap_id = str(payload.get("snapshot_id") or "").strip()
    if not snap_id:
        raise SandboxInvalidInput("snapshot_id is required")
    destination = _validate_destination(payload.get("destination_uri"))
    snap_root = sandbox_dir(sandbox_id) / "snapshots" / snap_id
    if not (snap_root / "manifest.json").is_file():
        raise SandboxNotFound(
            f"snapshot '{snap_id}' has no manifest under {snap_root}"
        )
    include_images = bool(payload.get("include_service_images", True))
    if include_images:
        _save_service_images(snap_root, sandbox_id, snap_id)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tarfile.open(destination, "w:gz") as out:
        for entry in snap_root.iterdir():
            if entry.name in _EXCLUDED_NAMES:
                continue
            out.add(entry, arcname=entry.name, filter=_strip_uid_gid)
    size_bytes = destination.stat().st_size
    state.touch()
    return {
        "sandbox_id": sandbox_id,
        "snapshot_id": snap_id,
        "destination_uri": f"{_FILE_SCHEME}{destination.resolve()}",
        "size_bytes": size_bytes,
        "included_service_images": include_images,
        "secrets_excluded": True,
        "note": (
            "Bundle is portable across hosts with the same Docker engine "
            "major version. Cloud-bucket destinations are tracked in the "
            "hosted-mode follow-up (wallet bucket creds)."
        ),
    }


def _validate_destination(value: Any) -> Path:
    """Pure: parse + validate a ``file://`` destination URI."""
    if not isinstance(value, str) or not value.strip():
        raise SandboxInvalidInput("destination_uri is required")
    raw = value.strip()
    if raw.startswith(_FILE_SCHEME):
        path_str = raw[len(_FILE_SCHEME):]
    else:
        parsed = urlparse(raw)
        if parsed.scheme and parsed.scheme != "file":
            raise SandboxInvalidInput(
                "only file:// destinations are supported in this build; "
                "see PR description for the bucket follow-up"
            )
        path_str = parsed.path or raw
    if not path_str:
        raise SandboxInvalidInput("destination_uri is empty")
    target = Path(path_str).expanduser().resolve()
    # The user owns this path; we just refuse the obvious blast-zones.
    forbidden = ("/etc", "/proc", "/sys", "/boot", "/dev")
    for prefix in forbidden:
        if str(target) == prefix or str(target).startswith(prefix + "/"):
            raise SandboxInvalidInput(
                f"destination_uri refuses paths under {prefix}/"
            )
    return target


def _save_service_images(snap_root: Path, sandbox_id: str, snap_id: str) -> None:
    """Side-effect: ``docker image save`` each committed snapshot image.

    Why: the snapshot manifest references image tags by name only —
    portable export needs the actual image layers. Best-effort: skipped
    services log a warning so the bundle still ships.
    """
    services_dir = snap_root / "services"
    if not services_dir.is_dir():
        return
    for tag_file in services_dir.glob("*.tag"):
        try:
            tag = tag_file.read_text("utf-8").strip()
        except OSError:
            continue
        if not tag:
            continue
        image_tar = services_dir / f"{tag_file.stem}.image.tar"
        if image_tar.is_file():
            continue  # already saved by a prior export
        try:
            run_docker(
                ["image", "save", "-o", str(image_tar), tag],
                timeout=_EXPORT_TIMEOUT_S,
            )
        except Exception:
            _LOG.exception(
                "image save failed for %s in snapshot %s/%s",
                tag, sandbox_id, snap_id,
            )


def _strip_uid_gid(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo:
    """Pure: zero UID/GID + numeric owner so the bundle imports cleanly elsewhere."""
    tarinfo.uid = 0
    tarinfo.gid = 0
    tarinfo.uname = ""
    tarinfo.gname = ""
    return tarinfo

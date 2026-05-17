"""sandbox_link — wire two sandboxes onto a shared Docker network.

# OWNS: creating an on-demand shared Docker network and attaching each
#       sandbox's services to it so they can resolve each other by
#       service hostname.
# NOT OWNS: any cross-host networking — by design link works on a single
#           Docker daemon. Multi-host overlay is the cluster follow-up.
# INVARIANTS:
#   * Link is symmetric: ``sandbox_link(a, b)`` and ``sandbox_link(b, a)``
#     converge to the same network membership.
#   * The shared network name is deterministic per (a,b) pair so re-linking
#     is idempotent.
"""

from __future__ import annotations

import logging
from typing import Any

from core.sandbox.docker_cli import run_docker
from core.sandbox.models import SandboxInvalidInput, SandboxNotFound
from core.sandbox.state import SandboxState, get

_LOG = logging.getLogger("aztea.sandbox.link")
_NETWORK_PREFIX = "aztea-link-"


def link(payload: dict[str, Any]) -> dict[str, Any]:
    """Connect two sandboxes on a shared Docker network.

    After the link, services in sandbox A can reach services in sandbox
    B by their compose service name + the network's resolved DNS,
    enabling microservice-topology testing without an edge proxy.
    """
    state_a = _require(payload, "sandbox_id")
    other_id = str(payload.get("other_sandbox_id") or "").strip()
    if not other_id:
        raise SandboxInvalidInput("other_sandbox_id is required")
    state_b = get(other_id)
    if state_b is None:
        raise SandboxNotFound(f"sandbox '{other_id}' is not active on this host")
    if state_a.sandbox_id == state_b.sandbox_id:
        raise SandboxInvalidInput("cannot link a sandbox to itself")
    network_name = _shared_network_name(state_a.sandbox_id, state_b.sandbox_id)
    _ensure_network(network_name)
    attached_a = _attach_services(state_a, network_name)
    attached_b = _attach_services(state_b, network_name)
    state_a.touch()
    state_b.touch()
    return {
        "sandbox_id": state_a.sandbox_id,
        "other_sandbox_id": state_b.sandbox_id,
        "shared_network": network_name,
        "attached": {
            state_a.sandbox_id: attached_a,
            state_b.sandbox_id: attached_b,
        },
        "note": (
            "Containers can now reach each other via service-name DNS on "
            f"network '{network_name}'. Service ports must already be "
            "exposed in their compose definitions."
        ),
    }


def _shared_network_name(a: str, b: str) -> str:
    """Pure: deterministic shared-network name from a (sorted) pair of IDs."""
    left, right = sorted([a, b])
    # Strip the sbx_ prefix so the network name stays readable but unique.
    return f"{_NETWORK_PREFIX}{left.removeprefix('sbx_')}-{right.removeprefix('sbx_')}"


def _ensure_network(name: str) -> None:
    """Side-effect: create the bridge network if it doesn't already exist."""
    proc = run_docker(
        ["network", "inspect", name],
        timeout=10,
        check=False,
    )
    if proc.returncode == 0:
        return
    run_docker(
        ["network", "create", "--driver", "bridge", name],
        timeout=15,
    )


def _attach_services(state: SandboxState, network_name: str) -> list[str]:
    """Side-effect: connect every running container of ``state`` to ``network_name``."""
    attached: list[str] = []
    for service, meta in state.boot.services.items():
        container = meta.get("container") or service
        proc = run_docker(
            ["network", "connect", "--alias", service, network_name, container],
            timeout=10,
            check=False,
        )
        if proc.returncode == 0:
            attached.append(service)
        elif "already exists" in (proc.stderr or "") or "is already" in (proc.stderr or ""):
            attached.append(service)
        else:
            _LOG.warning(
                "network connect failed for %s on %s: %s",
                container, network_name, (proc.stderr or "").strip()[:200],
            )
    return attached


def _require(payload: dict[str, Any], key: str) -> SandboxState:
    sandbox_id = str((payload or {}).get(key) or "").strip()
    if not sandbox_id:
        raise SandboxInvalidInput(f"{key} is required")
    state = get(sandbox_id)
    if state is None:
        raise SandboxNotFound(f"sandbox '{sandbox_id}' is not active on this host")
    return state

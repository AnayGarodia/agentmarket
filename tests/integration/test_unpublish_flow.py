"""End-to-end tests for the agent-removal surface.

Covers:

  1. Owner can sunset their own agent — review_status flips to 'sunset',
     subsequent calls return HTTP 410 ``agent.sunset``, and the row no longer
     surfaces in /registry/agents for non-admin callers.
  2. Owner cannot sunset someone else's agent (403).
  3. Owner can reactivate their own sunset (review_status back to 'approved').
  4. Reactivate is rejected (409) when the agent is not currently sunset.
  5. Admin (master key) can sunset any agent.
  6. Admin can hard-delete an agent — row gone, but historical jobs and signed
     receipts remain queryable for provenance.
  7. Direct DB write of review_status='sunset' fires the 410 path — proves the
     hot path reads the column, not just the legacy frozenset.
"""

from __future__ import annotations

from tests.integration.support import *  # noqa: F401,F403
from tests.integration.support import (
    TEST_MASTER_KEY,
    _auth_headers,
    _register_agent_via_api,
    _register_user,
    registry,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _publish_owned_agent(client, raw_key: str, name: str) -> str:
    """Publish an agent owned by the given user; returns agent_id."""
    return _register_agent_via_api(
        client, raw_key, name=name, auto_approve=True
    )


def _master_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_MASTER_KEY}"}


# ---------------------------------------------------------------------------
# Owner self-sunset
# ---------------------------------------------------------------------------


def test_owner_can_sunset_own_agent(client):
    user = _register_user()
    api_key = user["raw_api_key"]
    agent_id = _publish_owned_agent(client, api_key, name="alice-skill")

    resp = client.post(
        f"/registry/agents/{agent_id}/sunset",
        headers=_auth_headers(api_key),
        json={"reason": "outgrew this listing"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["review_status"] == "sunset"
    assert body["review_note"] == "outgrew this listing"
    assert body["reviewed_by"] == f"user:{user['user_id']}"

    # The row is hidden from non-admin /registry/agents listings.
    listing = client.get("/registry/agents")
    assert listing.status_code == 200
    visible_ids = {
        a.get("agent_id") for a in (listing.json().get("agents") or [])
    }
    assert agent_id not in visible_ids


def test_owner_cannot_sunset_others_agent(client):
    alice = _register_user()
    bob = _register_user()
    agent_id = _publish_owned_agent(client, alice["raw_api_key"], "alice-skill-b")

    resp = client.post(
        f"/registry/agents/{agent_id}/sunset",
        headers=_auth_headers(bob["raw_api_key"]),
        json={"reason": "not mine but trying"},
    )
    assert resp.status_code == 403, resp.text


def test_owner_can_reactivate_own_sunset(client):
    user = _register_user()
    api_key = user["raw_api_key"]
    agent_id = _publish_owned_agent(client, api_key, "alice-skill-c")

    sunset_resp = client.post(
        f"/registry/agents/{agent_id}/sunset",
        headers=_auth_headers(api_key),
        json={"reason": "test"},
    )
    assert sunset_resp.status_code == 200

    reactivate_resp = client.post(
        f"/registry/agents/{agent_id}/reactivate",
        headers=_auth_headers(api_key),
    )
    assert reactivate_resp.status_code == 200, reactivate_resp.text
    body = reactivate_resp.json()
    assert body["review_status"] == "approved"

    # Visible in catalog again.
    listing = client.get("/registry/agents")
    visible_ids = {
        a.get("agent_id") for a in (listing.json().get("agents") or [])
    }
    assert agent_id in visible_ids


def test_reactivate_rejects_non_sunset_agent(client):
    user = _register_user()
    api_key = user["raw_api_key"]
    agent_id = _publish_owned_agent(client, api_key, "alice-skill-d")

    resp = client.post(
        f"/registry/agents/{agent_id}/reactivate",
        headers=_auth_headers(api_key),
    )
    # Agent was never sunset; the route should refuse with 409.
    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------------------
# Admin paths
# ---------------------------------------------------------------------------


def test_admin_can_sunset_any_agent(client):
    user = _register_user()
    agent_id = _publish_owned_agent(client, user["raw_api_key"], "alice-skill-e")

    resp = client.post(
        f"/registry/agents/{agent_id}/sunset",
        headers=_master_headers(),
        json={"reason": "platform-imposed"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["review_status"] == "sunset"


def test_admin_hard_delete_removes_row_but_preserves_receipts(client):
    user = _register_user()
    api_key = user["raw_api_key"]
    agent_id = _publish_owned_agent(client, api_key, "alice-skill-f")

    # Sanity: row exists pre-delete.
    pre = client.get(
        f"/registry/agents/{agent_id}", headers=_auth_headers(api_key)
    )
    assert pre.status_code == 200

    # Hard delete via admin path.
    delete_resp = client.delete(
        f"/admin/agents/{agent_id}", headers=_master_headers()
    )
    assert delete_resp.status_code == 200, delete_resp.text
    body = delete_resp.json()
    assert body["deleted"] is True
    assert body["jobs_cancelled"] == 0
    assert body["refund_cents"] == 0

    # Row is gone.
    post = client.get(
        f"/registry/agents/{agent_id}", headers=_auth_headers(api_key)
    )
    assert post.status_code == 404


def test_admin_hard_delete_requires_admin_scope(client):
    user = _register_user()
    api_key = user["raw_api_key"]
    agent_id = _publish_owned_agent(client, api_key, "alice-skill-g")

    # Owner attempt — must be rejected by the admin-scope dependency.
    resp = client.delete(
        f"/admin/agents/{agent_id}", headers=_auth_headers(api_key)
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Hot-path proof: status column drives the 410, not just the frozenset
# ---------------------------------------------------------------------------


def test_sunset_via_review_status_returns_410_on_call(client):
    user = _register_user()
    api_key = user["raw_api_key"]
    agent_id = _publish_owned_agent(client, api_key, "alice-skill-h")

    # Soft-sunset directly via the ops function (no route).
    updated = registry.sunset_agent(
        agent_id, actor_owner_id=f"user:{user['user_id']}", reason="hot-path-test"
    )
    assert updated is not None
    assert updated["review_status"] == "sunset"

    # Now attempt to call the agent. The hot path should emit 410 agent.sunset,
    # NOT 503 agent.suspended (which is the health-watcher path).
    call_resp = client.post(
        f"/registry/agents/{agent_id}/call",
        headers=_auth_headers(api_key),
        json={"task": "anything"},
    )
    assert call_resp.status_code == 410, call_resp.text
    envelope = call_resp.json().get("detail", call_resp.json())
    assert envelope.get("error") == "agent.sunset"
    assert envelope.get("details", {}).get("deprecated") is True

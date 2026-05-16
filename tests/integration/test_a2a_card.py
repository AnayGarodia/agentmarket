"""Integration tests for the per-agent A2A card endpoint.

Covers sub-plan #10 from the 2026-05-16 production audit: external A2A
discovery tools expect ``/agents/{id}/agent.json`` (sibling of
``/agents/{id}/did.json``). The historic placement at
``/registry/agents/{id}/agent.json`` is kept as a back-compat alias.
"""

from tests.integration.support import *  # noqa: F403


def test_a2a_card_served_at_both_paths_with_identical_body(client):
    owner = _register_user()
    aid = _register_agent_via_api(
        client,
        owner["raw_api_key"],
        name=f"A2A Card Test {uuid.uuid4().hex[:6]}",
    )

    canonical = client.get(f"/agents/{aid}/agent.json")
    legacy = client.get(f"/registry/agents/{aid}/agent.json")

    assert canonical.status_code == 200, canonical.text
    assert legacy.status_code == 200, legacy.text
    assert canonical.headers["content-type"].startswith("application/json")
    assert legacy.headers["content-type"].startswith("application/json")
    assert canonical.json() == legacy.json()


def test_a2a_card_404_for_unknown_agent_at_canonical_path(client):
    resp = client.get(f"/agents/{uuid.uuid4()}/agent.json")
    assert resp.status_code == 404

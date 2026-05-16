"""Unit tests for the MCP-surface audit fixes (sub-plans #3, #4, #18, #9).

These exercise the SDK-side shape changes in isolation so they don't need a
running server.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

_SDK_ROOT = Path(__file__).resolve().parent.parent / "sdks" / "python-sdk"
if str(_SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(_SDK_ROOT))


def _fake_session(parse_result: tuple[bool, dict]):
    """Return a fake requests.Session whose .post() makes _post return parse_result."""
    sess = MagicMock()
    resp = MagicMock()
    resp.ok = parse_result[0]
    resp.status_code = parse_result[1].get("status_code", 200 if parse_result[0] else 503)
    resp.text = ""
    resp.json.return_value = (
        parse_result[1] if parse_result[0] else parse_result[1]
    )
    sess.post.return_value = resp
    sess.get.return_value = resp
    return sess


# --- #3: search timeout → AGENT_LOOKUP_TIMEOUT, not AGENT_LOOKUP_FAILED -----


def test_resolve_agent_id_returns_timeout_envelope_on_503():
    from aztea.mcp import meta_tools

    session = MagicMock()
    resp = MagicMock()
    resp.ok = False
    resp.status_code = 503
    resp.text = '{"detail": "search service overloaded"}'
    resp.json.return_value = {"detail": "search service overloaded"}
    session.post.return_value = resp

    aid, err = meta_tools._resolve_agent_id(
        session=session,
        base="http://localhost:8000",
        hdrs={},
        timeout=1.0,
        args={"slug": "anything"},
    )
    assert aid == ""
    assert err is not None
    assert err["error"] == "AGENT_LOOKUP_TIMEOUT", err
    assert "retry_after_ms" in err


def test_resolve_agent_id_404_does_not_get_timeout_envelope():
    """The point of audit #3 is to never mask a real timeout as a slug
    error. A 404 from search is NOT a timeout and must keep returning the
    `AGENT_LOOKUP_FAILED` envelope (the pre-existing behaviour)."""
    from aztea.mcp import meta_tools

    session = MagicMock()
    resp = MagicMock()
    resp.ok = False
    resp.status_code = 404
    resp.text = '{"detail": "not found"}'
    resp.json.return_value = {"detail": "not found"}
    session.post.return_value = resp

    aid, err = meta_tools._resolve_agent_id(
        session=session,
        base="http://localhost:8000",
        hdrs={},
        timeout=1.0,
        args={"slug": "definitely-not-a-real-slug-9999"},
    )
    assert aid == ""
    assert err is not None
    assert err["error"] != "AGENT_LOOKUP_TIMEOUT"


# --- #4: private_task is exposed on do_specialist_task and call_specialist --


def test_do_specialist_task_schema_exposes_private_task():
    from aztea.mcp import server as mcp_server

    schema = mcp_server._LAZY_DO_TOOL["input_schema"]
    props = schema["properties"]
    assert "private_task" in props, (
        "do_specialist_task must expose private_task — audit #4"
    )
    assert props["private_task"]["type"] == "boolean"


def test_call_specialist_schema_exposes_private_task():
    from aztea.mcp import server as mcp_server

    schema = mcp_server._LAZY_CALL_TOOL["input_schema"]
    props = schema["properties"]
    assert "private_task" in props, (
        "call_specialist must expose private_task — audit #4"
    )


# --- #18: compare_select schema accepts winner_slug alone -------------------


def test_compare_select_schema_allows_winner_slug_only():
    from aztea.mcp import meta_tools

    selectors = [
        t
        for t in meta_tools._TOOLS
        if t.get("name") == "aztea_select_compare_winner"
    ]
    assert selectors, "aztea_compare_select tool not found"
    schema = selectors[0]["input_schema"]
    assert schema["required"] == ["compare_id"], (
        f"compare_select must only require compare_id (audit #18), got {schema['required']}"
    )
    any_of = schema.get("anyOf") or []
    required_sets = {tuple(v.get("required") or ()) for v in any_of}
    assert ("winner_agent_id",) in required_sets
    assert ("winner_slug",) in required_sets
    props = schema["properties"]
    assert "winner_slug" in props

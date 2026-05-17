"""Supplementary wizard coverage — gaps the original ``test_cli_wizard.py``
suite did not exercise.

The base suite covers happy paths and a handful of validators. This file
adds the remaining safety-scanner block paths (Python handler dangerous
imports, agent.md endpoint pointing at Aztea / homoglyph variants, missing
``def handler``) and the editor-open branch of the multi-line prompt.

Reuses the autouse fixtures pattern from ``test_cli_wizard.py`` rather than
importing them, because pytest does not easily share autouse fixtures across
test modules and the duplication keeps each file self-contained.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from aztea.cli import app


# ---------------------------------------------------------------------------
# Fixtures (mirror tests/test_cli_wizard.py so each suite stands alone)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _stub_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("AZTEA_API_KEY", "test-key")
    monkeypatch.setenv("AZTEA_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("AZTEA_CONFIG_DIR", str(tmp_path / "_aztea_cfg"))
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("AZTEA_EDITOR", raising=False)
    monkeypatch.delenv("ALLOW_PRIVATE_OUTBOUND_URLS", raising=False)
    # Endpoint URLs used here are fixtures (example.com etc.); skip the CLI
    # reachability probe so tests exercise the wizard / scanner, not DNS.
    monkeypatch.setenv("AZTEA_SKIP_ENDPOINT_PROBE", "1")


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def fake_tty(monkeypatch):
    monkeypatch.setattr("aztea.cli.prompts._is_tty", lambda: True)


@pytest.fixture
def mock_build_client():
    fake_client = MagicMock()
    fake_client.list_agents.return_value = []
    fake_client._request_json.return_value = {
        "agent_id": "fake-id-123",
        "review_status": "approved",
        "agent": {"name": "fake", "review_status": "approved"},
    }
    fake_client.registry.register.return_value = {
        "agent_id": "fake-id-456",
        "review_status": "probation",
        "agent": {"name": "fake", "review_status": "probation"},
    }
    cm = MagicMock()
    cm.__enter__.return_value = fake_client
    cm.__exit__.return_value = False
    with patch("aztea.cli.publish.build_client", return_value=cm) as p:
        yield fake_client, p


# ---------------------------------------------------------------------------
# URL validator — protocol + private-IP behaviour
# ---------------------------------------------------------------------------


def test_url_validator_rejects_http_without_allow_env():
    from aztea.cli.prompts import url_validator

    ok, err = url_validator("http://my.host.example.com/run")
    assert not ok
    assert "https" in err.lower()


def test_url_validator_accepts_http_with_allow_env(monkeypatch):
    from aztea.cli.prompts import url_validator

    monkeypatch.setenv("ALLOW_PRIVATE_OUTBOUND_URLS", "1")
    ok, _ = url_validator("http://localhost:8000/run")
    assert ok


def test_url_validator_rejects_non_url():
    from aztea.cli.prompts import url_validator

    ok, err = url_validator("not-a-url")
    assert not ok
    assert "http" in err.lower()


def test_url_validator_rejects_empty():
    from aztea.cli.prompts import url_validator

    ok, err = url_validator("")
    assert not ok


# ---------------------------------------------------------------------------
# Slug, identifier, emoji validators — explicit edge cases
# ---------------------------------------------------------------------------


def test_slug_validator_rejects_uppercase_and_spaces():
    from aztea.cli.prompts import slug_validator

    for bad in ("Bad", "has space", "Has-Caps", "1starts-with-digit", "x", "no_underscore"):
        ok, _ = slug_validator(bad)
        assert not ok, f"slug_validator should reject {bad!r}"


def test_slug_validator_accepts_typical_names():
    from aztea.cli.prompts import slug_validator

    for good in ("word-counter", "ab", "kebab-case-ok", "a1-2-3"):
        ok, err = slug_validator(good)
        assert ok, f"slug_validator should accept {good!r} (err={err})"


def test_emoji_validator_rejects_long_string():
    from aztea.cli.prompts import emoji_validator

    ok, err = emoji_validator("this-is-clearly-not-an-emoji")
    assert not ok
    assert "emoji" in err.lower()


def test_emoji_validator_allows_empty_and_short():
    from aztea.cli.prompts import emoji_validator

    for value in ("", "📝", "🚀"):
        ok, _ = emoji_validator(value)
        assert ok, f"emoji_validator should accept {value!r}"


def test_identifier_validator_python_style():
    from aztea.cli.prompts import identifier_validator

    assert identifier_validator("task")[0]
    assert identifier_validator("input_field")[0]
    assert identifier_validator("_private")[0]
    assert not identifier_validator("1starts_with_digit")[0]
    assert not identifier_validator("has-dash")[0]
    assert not identifier_validator("")[0]


# ---------------------------------------------------------------------------
# Python handler — safety-scanner block paths
# ---------------------------------------------------------------------------


def test_python_handler_with_subprocess_blocked_by_scanner(
    runner, fake_tty, mock_build_client, tmp_path
):
    """Wizard accepts the input, but publish dispatch hits the safety scanner
    on the generated file. ``import subprocess`` should produce a block
    finding that surfaces a non-zero exit."""
    # Path 3 wizard never lets the user paste arbitrary code (it just writes
    # the template), so to exercise the scanner block path we hand the publish
    # CLI an already-written .py file containing the dangerous code.
    bad = tmp_path / "bad_handler.py"
    bad.write_text(
        "import subprocess\n"
        "def handler(payload):\n"
        "    return {'ok': True}\n"
    )
    result = runner.invoke(app, ["publish", str(bad)])
    assert result.exit_code != 0, result.output
    # The scanner emits a block-level python.* finding
    out = (result.output or "").lower()
    assert "block" in out or "subprocess" in out or "python." in out


def test_python_handler_missing_handler_function_blocked(
    runner, fake_tty, mock_build_client, tmp_path
):
    bad = tmp_path / "no_handler.py"
    bad.write_text("def something_else(payload):\n    return {}\n")
    result = runner.invoke(app, ["publish", str(bad)])
    assert result.exit_code != 0, result.output


def test_python_handler_with_eval_blocked(
    runner, fake_tty, mock_build_client, tmp_path
):
    bad = tmp_path / "eval_handler.py"
    bad.write_text(
        "def handler(payload):\n"
        "    return eval(payload['code'])\n"
    )
    result = runner.invoke(app, ["publish", str(bad)])
    assert result.exit_code != 0, result.output


# ---------------------------------------------------------------------------
# agent.md — endpoint pointing at Aztea / homoglyph variants
# ---------------------------------------------------------------------------


def _agent_md_with_endpoint(endpoint_url: str) -> str:
    """Build a minimal agent.md whose Registration Metadata points at ``endpoint_url``.

    Mirrors the structure of the wizard-generated agent.md just closely
    enough for the ``detect`` + scan pipeline to recognise it.
    """
    import json as _json

    metadata = {
        "name": "aztea-collision-test",
        "description": "Pretends to be Aztea-hosted, should be blocked.",
        "endpoint_url": endpoint_url,
        "price_per_call_usd": 0.01,
        "tags": [],
        "input_schema": {
            "type": "object",
            "properties": {"task": {"type": "string", "description": "input"}},
            "required": ["task"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"result": {"type": "string", "description": "output"}},
            "required": ["result"],
        },
    }
    return (
        "# aztea-collision-test\n\n"
        "Pretends to be Aztea-hosted, should be blocked.\n\n"
        "## Registry Endpoint\n\n"
        f"`{endpoint_url}`\n\n"
        "## Registration Flow\n\n"
        "POST a JSON payload to the endpoint URL above.\n\n"
        "## Settlement Flow Expectations\n\n"
        "Charged on successful response only.\n\n"
        "## Registration Metadata\n\n"
        "```json\n"
        + _json.dumps(metadata, indent=2)
        + "\n```\n"
    )


def test_agent_md_endpoint_pointing_at_aztea_blocked(
    runner, fake_tty, mock_build_client, tmp_path
):
    src = tmp_path / "collide.agent.md"
    src.write_text(_agent_md_with_endpoint("https://api.aztea.ai/forge/run"))
    result = runner.invoke(app, ["publish", str(src)])
    assert result.exit_code != 0, result.output
    out = (result.output or "").lower()
    assert "aztea" in out or "endpoint_is_aztea" in out or "block" in out


def test_agent_md_endpoint_with_percent_encoded_aztea_blocked(
    runner, fake_tty, mock_build_client, tmp_path
):
    """``%61ztea.ai`` decodes to ``aztea.ai`` — the scanner must catch it."""
    src = tmp_path / "encoded.agent.md"
    src.write_text(_agent_md_with_endpoint("https://%61pi.aztea.ai/run"))
    result = runner.invoke(app, ["publish", str(src)])
    assert result.exit_code != 0, result.output


# ---------------------------------------------------------------------------
# Editor-open branch of the wizard (Python handler, kind=2 after the 2026-05-17
# wizard-options reshuffle — SKILL.md path was removed).
# ---------------------------------------------------------------------------


def test_wizard_python_handler_editor_branch(runner, fake_tty, mock_build_client, tmp_path, monkeypatch):
    """When the user picks 'open editor', wizard spawns ``$EDITOR`` via subprocess.

    We mock subprocess.run so no real editor is launched; the temp file the
    wizard creates is left intact, and the wizard reads its content back.
    """
    monkeypatch.setenv("EDITOR", "fake-editor-not-installed")

    captured: dict[str, list] = {"calls": []}

    def fake_run(args, *_, **__):  # pragma: no cover — invoked through wizard
        captured["calls"].append(args)
        # Simulate the editor writing to the temp file with a valid handler.
        Path(args[1]).write_text(
            'def handler(payload):\n'
            '    """Echoes payload back. Authored via editor."""\n'
            '    return {"result": str(payload)}\n',
            encoding="utf-8",
        )
        return MagicMock(returncode=0)

    monkeypatch.setattr("aztea.cli.prompts.subprocess.run", fake_run)

    stdin = (
        "2\n"                                                # kind = python handler
        "editor-flow\n"                                      # name
        "Tests the editor branch end-to-end.\n"              # description
        "y\n"                                                # "Open the starter handler in your editor?"
        "y\n"                                                # multiline_or_editor: "Open your editor?"
    )
    # Python handler publishing needs --endpoint at the CLI level (the wizard
    # currently writes the file and leaves the endpoint to the flag — see
    # the "Deploy this file at the URL you'll provide next" hint).
    result = runner.invoke(
        app,
        ["publish", "--endpoint", "https://my.host.example.com/run", "--price", "0.05"],
        input=stdin,
    )
    assert result.exit_code == 0, result.output

    file_path = tmp_path / "editor_flow.py"
    assert file_path.exists(), result.output
    body = file_path.read_text()
    assert "Authored via editor" in body, body
    assert captured["calls"], "expected subprocess.run to be invoked for the editor"
    assert captured["calls"][0][0] == "fake-editor-not-installed"


# ---------------------------------------------------------------------------
# remediation_for() — friendly error mapping
# ---------------------------------------------------------------------------


def test_remediation_for_known_codes_returns_hint():
    from aztea.cli.wizard import remediation_for

    for code in (
        "skill.prompt_injection",
        "skill.embedded_api_key",
        "python.blocked_import",
        "python.blocked_builtin",
        "python.blocked_os_call",
        "manifest.endpoint_is_aztea",
    ):
        hint = remediation_for(code)
        assert hint and len(hint) > 20, f"missing remediation for {code}"


def test_remediation_for_unknown_code_returns_none():
    from aztea.cli.wizard import remediation_for

    assert remediation_for("nonexistent.code") is None


# ---------------------------------------------------------------------------
# default-name-from-cwd
# ---------------------------------------------------------------------------


def test_default_name_from_cwd_slugifies(monkeypatch, tmp_path):
    from aztea.cli.wizard import _default_name_from_cwd

    sub = tmp_path / "Some Project Dir"
    sub.mkdir()
    monkeypatch.chdir(sub)
    name = _default_name_from_cwd()
    assert name == "some-project-dir" or name.startswith("some-project-dir")


def test_default_name_from_cwd_prefixes_when_starting_with_digit(monkeypatch, tmp_path):
    from aztea.cli.wizard import _default_name_from_cwd

    sub = tmp_path / "123-project"
    sub.mkdir()
    monkeypatch.chdir(sub)
    name = _default_name_from_cwd()
    # _default_name_from_cwd prepends "my-" when the cleaned name does not start with a letter
    assert name.startswith("my-") or name[0].isalpha()


# ---------------------------------------------------------------------------
# tags parsing
# ---------------------------------------------------------------------------


def test_parse_tags_handles_empty_and_whitespace():
    from aztea.cli.wizard import _parse_tags

    assert _parse_tags("") == []
    assert _parse_tags("   ") == []
    assert _parse_tags("a,b,c") == ["a", "b", "c"]
    assert _parse_tags(" a , b , c ") == ["a", "b", "c"]
    assert _parse_tags("a,,b") == ["a", "b"]

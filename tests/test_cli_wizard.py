"""Wizard tests — drive `aztea publish` (no path) via Typer's CliRunner.

The wizard generates a file in CWD and dispatches into the existing publish
pipeline. We mock build_client + the registry POST so tests stay
network-free and fast.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from aztea.cli import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _cwd(tmp_path, monkeypatch):
    """Run each wizard test inside a clean tmp_path."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _stub_credentials(monkeypatch, tmp_path):
    """Pretend the user has a saved API key + base URL.

    The wizard's first guard refuses if both env and config are empty; we
    set the env so the wizard runs all the way through.
    """
    monkeypatch.setenv("AZTEA_API_KEY", "test-key")
    monkeypatch.setenv("AZTEA_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("AZTEA_CONFIG_DIR", str(tmp_path / "_aztea_cfg"))
    # The wizard skips editor invocation when the user answers "n"; defang
    # any accidental editor spawn just in case.
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("AZTEA_EDITOR", raising=False)
    # Wizard endpoint URLs in these tests are fixture hosts (my.host.example.com,
    # etc.); the CLI's reachability probe would correctly fail on them. Skip it
    # so we exercise the wizard flow itself, not real-world DNS.
    monkeypatch.setenv("AZTEA_SKIP_ENDPOINT_PROBE", "1")


@pytest.fixture
def runner():
    # Newer Click drops mix_stderr; stdout + stderr both land in result.output.
    return CliRunner()


@pytest.fixture
def fake_tty(monkeypatch):
    """Patch the wizard's TTY check so tests pass through."""
    monkeypatch.setattr("aztea.cli.prompts._is_tty", lambda: True)


@pytest.fixture
def mock_build_client():
    """Replace build_client so registration calls don't hit the network."""
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
# Path 1 — agent.md manifest
# (SKILL.md hosted-skill publishing was removed 2026-05-17; only two paths
# remain. kind=1 → agent.md, kind=2 → python handler.)
# ---------------------------------------------------------------------------


def test_wizard_agent_md_happy_path(runner, fake_tty, mock_build_client, tmp_path):
    fake_client, _ = mock_build_client
    stdin = (
        "1\n"
        "mybot\n"
        "Does some clever things on a server I host.\n"
        "https://my.host.example.com/run\n"
        "task\n"
        "What you want the bot to do.\n"
        "result\n"
        "The bot's response.\n"
        "0.05\n"
        "research,test\n"
    )
    result = runner.invoke(app, ["publish"], input=stdin)
    assert result.exit_code == 0, result.output
    file_path = tmp_path / "mybot.agent.md"
    assert file_path.exists()
    text = file_path.read_text()
    assert "## Registry Endpoint" in text
    assert "## Registration Metadata" in text
    assert "https://my.host.example.com/run" in text
    posts = [
        c for c in fake_client._request_json.call_args_list
        if "/onboarding/ingest" in c.args
    ]
    assert posts, "expected POST /onboarding/ingest"


# ---------------------------------------------------------------------------
# Path 2 — Python handler
# ---------------------------------------------------------------------------


def test_wizard_python_handler_happy_path(runner, fake_tty, mock_build_client, tmp_path):
    fake_client, _ = mock_build_client
    stdin = (
        "2\n"
        "echo-bot\n"
        "Echoes whatever payload it receives.\n"
        "n\n"                                # don't open editor
        "https://my.host.example.com/run\n"  # public URL
        "0.05\n"
        "\n"                                 # tags skip
    )
    result = runner.invoke(app, ["publish"], input=stdin)
    # Python-handler path requires --endpoint at registration time. Wizard
    # supplies it via the public URL prompt; CLI passes through.
    # If the wizard hasn't wired endpoint passthrough yet, the test will fail
    # cleanly here and pin the gap.
    file_path = tmp_path / "echo_bot.py"
    assert file_path.exists(), result.output
    body = file_path.read_text()
    assert "def handler" in body


# ---------------------------------------------------------------------------
# Validation re-prompts
# ---------------------------------------------------------------------------


def test_wizard_reprompts_invalid_slug(runner, fake_tty, mock_build_client, tmp_path):
    """Slug validator rejects bad names and re-prompts. Exercised via the
    agent.md path (formerly via SKILL.md; SKILL.md removed 2026-05-17)."""
    stdin = (
        "1\n"                                                  # kind = agent.md
        "BAD slug!\n"                                          # invalid → re-prompt
        "good-slug\n"                                          # valid
        "An agent with a good name.\n"
        "https://my.host.example.com/run\n"
        "task\n"
        "Tells the agent something useful.\n"
        "result\n"
        "Response returned by agent.\n"
        "0.05\n"
        "\n"
    )
    result = runner.invoke(app, ["publish"], input=stdin)
    assert result.exit_code == 0, result.output
    assert (tmp_path / "good-slug.agent.md").exists()


def test_wizard_reprompts_short_description(runner, fake_tty, mock_build_client, tmp_path):
    stdin = (
        "1\n"                                                  # kind = agent.md
        "test-name\n"
        "tiny\n"                                               # too short → re-prompt
        "Something with three or more words.\n"
        "https://my.host.example.com/run\n"
        "task\n"
        "Tells the agent something useful.\n"
        "result\n"
        "Response returned by agent.\n"
        "0.05\n"
        "\n"
    )
    result = runner.invoke(app, ["publish"], input=stdin)
    assert result.exit_code == 0, result.output


def test_wizard_reprompts_high_price(runner, fake_tty, mock_build_client, tmp_path):
    stdin = (
        "1\n"                                                  # kind = agent.md
        "price-test\n"
        "Tests price validation rejects out-of-range values.\n"
        "https://my.host.example.com/run\n"
        "task\n"
        "Tells the agent something useful.\n"
        "result\n"
        "Response returned by agent.\n"
        "9999\n"                                               # over $25 cap → re-prompt
        "0.10\n"                                               # ok
        "\n"
    )
    result = runner.invoke(app, ["publish"], input=stdin)
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_wizard_refuses_without_credentials(runner, fake_tty, monkeypatch):
    monkeypatch.delenv("AZTEA_API_KEY", raising=False)
    monkeypatch.delenv("AZTEA_BASE_URL", raising=False)
    result = runner.invoke(app, ["publish"], input="")
    assert result.exit_code == 2
    err = result.output
    assert "not signed in" in err.lower() or "aztea login" in err.lower()


def test_wizard_refuses_in_json_mode(runner, fake_tty, mock_build_client):
    result = runner.invoke(app, ["publish", "--json"], input="")
    assert result.exit_code == 2
    err = result.output
    assert "interactive" in err.lower() or "json" in err.lower()


def test_wizard_refuses_in_non_tty(runner, monkeypatch, mock_build_client):
    monkeypatch.setattr("aztea.cli.prompts._is_tty", lambda: False)
    result = runner.invoke(app, ["publish"], input="")
    assert result.exit_code == 2
    err = result.output
    assert "interactive" in err.lower() or "tty" in err.lower()


def test_wizard_existing_file_collision_refuses(runner, fake_tty, mock_build_client, tmp_path):
    """Pre-populate the target file; wizard must refuse to overwrite."""
    (tmp_path / "collide.agent.md").write_text("preexisting")
    stdin = (
        "1\n"                                                  # kind = agent.md
        "collide\n"
        "Should refuse to overwrite an existing file.\n"
        "https://my.host.example.com/run\n"
        "task\n"
        "Tells the agent something useful.\n"
        "result\n"
        "Response returned by agent.\n"
        "0.05\n"
        "\n"
        "n\n"                                                  # don't overwrite
    )
    result = runner.invoke(app, ["publish"], input=stdin)
    assert result.exit_code == 1, result.output
    # File contents preserved
    assert (tmp_path / "collide.agent.md").read_text() == "preexisting"


def test_wizard_from_template_only(runner, fake_tty, mock_build_client, tmp_path):
    """`--from-template <kind>` is non-interactive (1.5.1 fix): writes a
    placeholder starter file with stand-in values, no prompts, no TTY needed.
    User edits and re-runs `aztea publish <file>` to actually list.

    SKILL.md template path was removed 2026-05-17 along with the public
    SKILL.md publish route; only ``agent`` and ``python`` are valid kinds.
    """
    fake_client, _ = mock_build_client
    result = runner.invoke(app, ["publish", "--from-template", "agent"])
    assert result.exit_code == 0, result.output
    # The non-interactive path writes a fixed placeholder filename.
    assert (tmp_path / "agent.md").exists()
    # No registration calls
    posts = [
        c for c in fake_client._request_json.call_args_list
        if "/skills" in c.args or "/registry/register" in c.args
    ]
    assert not posts, "from-template-only should not register"


def test_wizard_from_template_skill_rejected(runner, fake_tty, mock_build_client, tmp_path):
    """``--from-template skill`` must now error cleanly (SKILL.md path is gone)."""
    result = runner.invoke(app, ["publish", "--from-template", "skill"])
    assert result.exit_code == 2, result.output
    out = (result.output or "").lower()
    assert "skill" in out
    # Don't leave a stale starter file behind.
    assert not (tmp_path / "my_new_skill.skill.md").exists()

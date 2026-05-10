"""Cover the ImportError fallback in ``aztea.cli.publish``.

The publish module tries (a) the vendored ``aztea._listing_safety`` then
(b) the monorepo ``core.listing_safety``; if both fail, every scanner
symbol is set to ``None``. The runtime guard at the top of ``publish()``
must then refuse the call with a structured error so users do not get a
silent half-degraded scan.

This test simulates both imports failing and asserts:
1. The fallback branch sets all eight names to ``None`` / string defaults.
2. Invoking ``aztea publish <path>`` exits with code 1 and surfaces the
   install hint.
3. Restoring the original module state leaves the rest of the suite
   unaffected (handled by importlib.reload in the teardown).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def force_listing_safety_import_failure(monkeypatch):
    """Make both vendored and monorepo listing_safety imports raise.

    The fixture reloads ``aztea.cli.publish`` so its module-level
    ``try/except`` runs the fallback branch, then yields. On teardown it
    reloads the module again with the originals so other tests are
    unaffected.
    """
    # Reload IN PLACE so the existing ``aztea.cli.app`` reference (and any
    # other module that has already imported ``publish``) keeps pointing at
    # the same module object — only its module-level globals change.
    # This prevents pollution of unrelated tests that ``patch("aztea.cli.publish.X")``.
    saved_safety = {
        name: sys.modules.get(name)
        for name in ("aztea._listing_safety", "core.listing_safety")
    }

    # Make sure ``publish`` is in sys.modules so ``importlib.reload`` works.
    publish = importlib.import_module("aztea.cli.publish")

    # Block both safety imports.
    sys.modules["aztea._listing_safety"] = None  # type: ignore[assignment]
    sys.modules["core.listing_safety"] = None  # type: ignore[assignment]

    # Reload the SAME module object — its ``scan_*`` globals will become None.
    importlib.reload(publish)

    yield publish

    # Restore the safety modules and reload publish in place again so its
    # globals are repopulated with the real scanners. Because we never
    # replaced the publish module object, every other module that imported
    # it earlier sees the restored scanners on the next call.
    for name, mod in saved_safety.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod
    importlib.reload(publish)


def test_fallback_sets_all_scanner_names_to_none(force_listing_safety_import_failure):
    pub = force_listing_safety_import_failure
    assert pub.scan_skill_md is None
    assert pub.scan_python_handler is None
    assert pub.scan_agent_md_endpoint is None
    assert pub.scan_clone_against is None
    assert pub.has_block is None
    assert pub.VerificationFinding is None
    # The two level constants degrade to plain strings, not None.
    assert pub.LEVEL_BLOCK == "block"
    assert pub.LEVEL_WARN == "warn"


def test_publish_exits_with_install_hint_when_safety_unavailable(
    force_listing_safety_import_failure, runner, tmp_path, monkeypatch
):
    """The runtime guard must catch ``scan_skill_md is None`` and refuse,
    not let the request through with no scan."""
    # Provide a valid skill file so detection succeeds — we want the failure
    # to come from the safety guard, not from missing input.
    skill = tmp_path / "demo.skill.md"
    skill.write_text(
        "---\n"
        "name: demo\n"
        "description: A short demo skill.\n"
        "---\n\n"
        "# demo\n\nA short demo skill.\n"
    )

    monkeypatch.setenv("AZTEA_API_KEY", "test-key")
    monkeypatch.setenv("AZTEA_BASE_URL", "http://localhost:8000")
    monkeypatch.chdir(tmp_path)

    from aztea.cli import app  # picks up the reloaded publish module via the package

    result = runner.invoke(app, ["publish", str(skill)])
    assert result.exit_code == 1, result.output
    out = (result.output or "").lower()
    assert (
        "core.listing_safety" in out
        or "publish.missing_safety_module" in out
        or "install" in out
    ), result.output


def test_publish_help_still_works_when_safety_unavailable(
    force_listing_safety_import_failure, runner
):
    """``aztea publish --help`` must keep working in a partial install — that
    is the entire reason for keeping the try/except instead of a hard import."""
    from aztea.cli import app

    result = runner.invoke(app, ["publish", "--help"])
    assert result.exit_code == 0, result.output
    assert "publish" in result.output.lower()


def test_normal_path_still_imports_safety_after_teardown():
    """After the fallback fixture's teardown, the normal import path must
    work again. This is a sanity check that prior tests do not poison the
    rest of the suite. We assert against the *existing* module object
    rather than re-importing — replacing the module in sys.modules would
    silently desync any caller (notably ``aztea.cli.app``) that already
    holds a reference to it.
    """
    pub = sys.modules.get("aztea.cli.publish")
    if pub is None:
        # Module was never imported in this process; nothing to verify.
        pytest.skip("aztea.cli.publish was not imported by any prior test")
    assert pub.scan_skill_md is not None
    assert pub.has_block is not None
    assert callable(pub.scan_skill_md)

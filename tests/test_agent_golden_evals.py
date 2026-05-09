"""Golden eval suite for public agents.

Every public agent gets fixed cases asserting required output fields,
structural correctness, and a launch-grade latency budget.

Tests run the agent ``run`` callable directly (no HTTP), so the rules they
enforce are about the agent module contract, not the marketplace surface.
A case whose output reports ``error.code`` ending in ``.tool_unavailable``
turns into a skip rather than a hard failure — the host environment may not
have every optional toolchain (tsc, etc.) installed.
"""
from __future__ import annotations

import time
from typing import Any, Callable

import pytest

from agents import (
    db_sandbox,
    linter_agent,
    multi_file_executor,
    python_executor,
    secret_scanner,
    shell_executor,
    sql_explainer,
    type_checker,
)


def _has_path(output: dict[str, Any], dotted: str) -> bool:
    value: Any = output
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return False
        value = value[part]
    return True


GOLDEN_CASES: list[dict[str, Any]] = [
    # Linter — three cases covering: real issues, clean code, filename-driven detection
    {"agent": "linter_agent", "run": linter_agent.run, "payload": {"code": "import os\nx=1\n", "language": "python"}, "required": ["issues", "total_issues", "clean"], "assert": lambda o: o["total_issues"] >= 1},
    {"agent": "linter_agent", "run": linter_agent.run, "payload": {"code": "x = 1\nprint(x)\n", "language": "python"}, "required": ["issues", "clean"], "assert": lambda o: isinstance(o["issues"], list)},
    {"agent": "linter_agent", "run": linter_agent.run, "payload": {"code": "def f():\n print(1)\n", "filename": "x.py"}, "required": ["language", "tool"], "assert": lambda o: o["language"] == "python"},

    # Type checker — two python cases (mypy required) and one typescript case (skipped if tsc unavailable)
    {"agent": "type_checker", "run": type_checker.run, "payload": {"code": "def f(x: int) -> str:\n    return x\n", "language": "python"}, "required": ["passed", "errors", "error_count"], "assert": lambda o: o["passed"] is False and o["error_count"] >= 1},
    {"agent": "type_checker", "run": type_checker.run, "payload": {"code": "def f(x: int) -> int:\n    return x\n", "language": "python"}, "required": ["passed", "errors"], "assert": lambda o: o["passed"] is True},
    {"agent": "type_checker", "run": type_checker.run, "payload": {"code": "const x: number = 's'\n", "language": "typescript"}, "required": ["passed", "errors"], "assert": lambda o: isinstance(o["errors"], list)},

    # Python and multi-file executors — success path and stderr propagation
    {"agent": "python_code_executor", "run": python_executor.run, "payload": {"code": "print(sum(range(5)))", "explain": False}, "required": ["stdout", "stderr", "exit_code", "timed_out"], "assert": lambda o: o["exit_code"] == 0 and o["stdout"].strip() == "10"},
    {"agent": "python_code_executor", "run": python_executor.run, "payload": {"code": "raise ValueError('boom')", "explain": False}, "required": ["stdout", "stderr", "exit_code"], "assert": lambda o: o["exit_code"] != 0 and "ValueError" in o["stderr"]},
    {"agent": "multi_file_python_executor", "run": multi_file_executor.run, "payload": {"files": [{"path": "util.py", "content": "def add(a,b): return a+b\n"}, {"path": "main.py", "content": "from util import add\nprint(add(2, 3))\n"}], "entry_point": "main.py", "explain": False}, "required": ["stdout", "exit_code", "files_written"], "assert": lambda o: o["exit_code"] == 0 and o["stdout"].strip() == "5"},

    # Shell and DB — bounded subprocess and real SQLite query
    {"agent": "shell_executor", "run": shell_executor.run, "payload": {"command": "python3 -c 'print(2 + 2)'"}, "required": ["stdout", "stderr", "exit_code"], "assert": lambda o: o["exit_code"] == 0 and o["stdout"].strip() == "4"},
    {"agent": "db_sandbox", "run": db_sandbox.run, "payload": {"schema_sql": "CREATE TABLE users(id INTEGER, name TEXT); INSERT INTO users VALUES (1, 'Ada');", "sql": "SELECT name FROM users WHERE id = 1", "explain": False}, "required": ["results", "statements_executed"], "assert": lambda o: o["results"][0]["rows"][0]["name"] == "Ada"},
    {"agent": "sql_explainer", "run": sql_explainer.run, "payload": {"schema_sql": "CREATE TABLE users(id INTEGER PRIMARY KEY, email TEXT);", "queries": ["SELECT * FROM users WHERE email = 'a@example.com'"]}, "required": ["queries", "total_issues", "summary"], "assert": lambda o: len(o["queries"]) == 1 and bool(o["queries"][0]["issues"])},

    # Validators / scanners — deterministic outputs, no LLM
    {"agent": "secret_scanner", "run": secret_scanner.run, "payload": {"content": "AWS_KEY=AKIAIOSFODNN7EXAMPLE\n", "filename": ".env"}, "required": ["findings", "total_findings", "summary"], "assert": lambda o: o["total_findings"] >= 1},
]


def _maybe_skip_tool_unavailable(output: dict[str, Any]) -> None:
    err = output.get("error") if isinstance(output, dict) else None
    if isinstance(err, dict):
        code = str(err.get("code") or "")
        if code.endswith(".tool_unavailable") or code.endswith(".unavailable"):
            pytest.skip(f"toolchain not installed: {err.get('message') or code}")


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=lambda c: c["agent"])
def test_launch_agent_golden_eval(case: dict[str, Any]) -> None:
    started = time.monotonic()
    output = case["run"](case["payload"])
    elapsed_ms = int((time.monotonic() - started) * 1000)

    assert isinstance(output, dict), output
    _maybe_skip_tool_unavailable(output)
    assert "error" not in output, output
    for key in case["required"]:
        assert _has_path(output, key), f"{case['agent']} missing {key}: {output}"
    predicate: Callable[[dict[str, Any]], bool] = case["assert"]
    assert predicate(output), output
    assert elapsed_ms < 10_000, f"{case['agent']} exceeded launch latency budget: {elapsed_ms}ms"


def test_golden_eval_suite_has_launch_depth() -> None:
    by_agent: dict[str, int] = {}
    for case in GOLDEN_CASES:
        by_agent[case["agent"]] = by_agent.get(case["agent"], 0) + 1
    assert len(by_agent) >= 8
    assert sum(by_agent.values()) >= 13

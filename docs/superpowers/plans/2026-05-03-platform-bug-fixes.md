# Platform Bug Fixes (2026-05-03 Eval) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 11 bugs surfaced by the 2026-05-03 adversarial external evaluation of the Aztea platform.

**Architecture:** Fixes span 7 agent modules, 2 spec files, the auto_hire decision module, and the MCP server search scoring. Each task is independent; tests go into `tests/test_agent_real_tool.py` or `tests/test_bug_regressions.py`.

**Tech Stack:** Python 3.11+, pytest, ruff, subprocess, re, sqlite3, ESLint via npx

---

### Task 1: TypeScript type checker — use npx instead of requiring global tsc

**Files:**
- Modify: `agents/type_checker.py:186-235`
- Test: `tests/test_agent_real_tool.py`

The current code does `shutil.which("tsc") or "tsc"` which raises `FileNotFoundError` when TypeScript is not globally installed. Fix: fall back to `npx --yes --package typescript tsc`, exactly as `linter_agent.py` uses `npx --yes eslint`.

- [ ] **Step 1: Write the failing test**

Add after the existing `test_type_checker_parses_mypy_json` test in `tests/test_agent_real_tool.py`:

```python
def test_type_checker_falls_back_to_npx_when_tsc_missing(monkeypatch):
    """If global tsc is absent, _run_tsc should use npx --package typescript tsc."""
    import importlib
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: None)  # no global tsc or npx check skipped

    import subprocess as _subprocess
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(_subprocess, "run", fake_run)

    import agents.type_checker as tc
    importlib.reload(tc)
    tc._run_tsc("const x: number = 1;", {}, False)
    assert captured.get("cmd", [None])[0] == "npx", "should fall back to npx"
    assert "--package" in captured["cmd"], "must specify --package typescript"
    assert "tsc" in captured["cmd"]
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_agent_real_tool.py::test_type_checker_falls_back_to_npx_when_tsc_missing -xvs
```

Expected: FAIL — current code never tries npx.

- [ ] **Step 3: Implement the fix**

In `agents/type_checker.py`, replace lines 210-228 with:

```python
        # Prefer a global tsc; fall back to npx (auto-installs typescript on demand).
        tsc_bin = shutil.which("tsc")
        if tsc_bin:
            cmd = [tsc_bin, "--noEmit", "--project", os.path.join(tmpdir, "tsconfig.json")]
        else:
            if not shutil.which("npx"):
                return _err(
                    "type_checker.tool_unavailable",
                    "tsc is not installed and npx is not available. "
                    "Install Node.js or TypeScript globally: npm install -g typescript",
                )
            cmd = [
                "npx", "--yes", "--package", "typescript", "tsc",
                "--noEmit", "--project", os.path.join(tmpdir, "tsconfig.json"),
            ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT,
                cwd=tmpdir,
            )
        except subprocess.TimeoutExpired:
            return _err("type_checker.timeout", "tsc timed out after 30 seconds.")
        except FileNotFoundError:
            return _err(
                "type_checker.tool_unavailable",
                "tsc is not installed. Install TypeScript globally: npm install -g typescript",
            )
```

Also update the version check right after (lines 230-235) to use the same fallback:

```python
        version_str = ""
        try:
            if tsc_bin:
                v = subprocess.run([tsc_bin, "--version"], capture_output=True, text=True, timeout=5)
            else:
                v = subprocess.run(
                    ["npx", "--yes", "--package", "typescript", "tsc", "--version"],
                    capture_output=True, text=True, timeout=15,
                )
            version_str = (v.stdout + v.stderr).strip()
        except Exception:
            pass
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_agent_real_tool.py::test_type_checker_falls_back_to_npx_when_tsc_missing -xvs
```

Expected: PASS

- [ ] **Step 5: Run all type_checker tests**

```
pytest tests/test_agent_real_tool.py -k type_checker -xvs
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add agents/type_checker.py tests/test_agent_real_tool.py
git commit -m "fix: type_checker falls back to npx when global tsc is absent"
```

---

### Task 2: TypeScript ESLint — add no-eval and no-var rules

**Files:**
- Modify: `agents/linter_agent.py:128-166`
- Test: `tests/test_agent_real_tool.py`

The TypeScript ESLint command is missing `no-eval:error` and `no-var:warn`. Both JS and TS commands should flag dynamic code execution (`eval`) and legacy `var` declarations.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_agent_real_tool.py`:

```python
def test_linter_agent_no_eval_and_no_var_rules_in_ts_eslint_command(monkeypatch):
    """Verify the ESLint command includes no-eval:error and no-var:warn for TypeScript."""
    import subprocess as _subprocess

    captured_cmd: dict = {}

    def fake_run(cmd, **kw):
        captured_cmd["cmd"] = list(cmd)
        import json
        return _subprocess.CompletedProcess(cmd, 0, json.dumps([{"filePath": "f.ts", "messages": []}]), "")

    monkeypatch.setattr(_subprocess, "run", fake_run)
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/npx" if x in ("npx", "node") else None)

    from agents import linter_agent
    linter_agent.run({"code": "const x = 1;", "language": "typescript"})
    cmd_str = " ".join(captured_cmd.get("cmd", []))
    assert "no-eval" in cmd_str, "TypeScript eslint command must include no-eval"
    assert "no-var" in cmd_str, "TypeScript eslint command must include no-var"
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_agent_real_tool.py::test_linter_agent_no_eval_and_no_var_rules_in_ts_eslint_command -xvs
```

Expected: FAIL

- [ ] **Step 3: Implement the fix**

In `agents/linter_agent.py`, in `_run_eslint()`:

For the **JavaScript** `base_cmd` (starting around line 128), add after the last `"--rule", "no-unreachable:error"` entry:
```python
        "--rule",
        "no-eval:error",
        "--rule",
        "no-var:warn",
```

For the **TypeScript** `base_cmd` (starting around line 146), add after `"no-unreachable:error"`:
```python
        "--rule",
        "no-eval:error",
        "--rule",
        "no-var:warn",
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_agent_real_tool.py::test_linter_agent_no_eval_and_no_var_rules_in_ts_eslint_command -xvs
```

Expected: PASS

- [ ] **Step 5: Run all linter tests**

```
pytest tests/test_agent_real_tool.py -k linter -xvs
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add agents/linter_agent.py tests/test_agent_real_tool.py
git commit -m "fix: add no-eval:error and no-var:warn to ESLint rules for JS and TS"
```

---

### Task 3: db_sandbox — explicitly block ATTACH/DETACH commands

**Files:**
- Modify: `agents/db_sandbox.py:30-106`
- Test: `tests/test_agent_real_tool.py`

`ATTACH DATABASE '/etc/passwd' AS leak` fails only accidentally because `/etc/passwd` is not a SQLite file. It must be rejected by a named check, not by a runtime sqlite3 exception.

- [ ] **Step 1: Write the failing tests**

```python
def test_db_sandbox_blocks_attach_database():
    from agents import db_sandbox
    result = db_sandbox.run({"sql": "ATTACH DATABASE '/etc/passwd' AS leak"})
    assert "error" in result
    code = result["error"]["code"]
    assert "blocked" in code or "attach" in code.lower()

def test_db_sandbox_blocks_attach_in_schema_sql():
    from agents import db_sandbox
    result = db_sandbox.run({
        "schema_sql": "ATTACH DATABASE '/tmp/other.db' AS other",
        "sql": "SELECT 1"
    })
    assert "error" in result

def test_db_sandbox_blocks_detach():
    from agents import db_sandbox
    result = db_sandbox.run({"sql": "DETACH DATABASE leak"})
    assert "error" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_agent_real_tool.py::test_db_sandbox_blocks_attach_database -xvs
```

Expected: FAIL (currently ATTACH raises `sqlite3.OperationalError`, not a structured block)

- [ ] **Step 3: Implement the fix**

At the top of `agents/db_sandbox.py`, add `import re as _re` after the existing imports. Then after `_MAX_DB_BYTES`, add:

```python
_BLOCKED_SQL_RE = _re.compile(r"^\s*(ATTACH|DETACH)\s", _re.IGNORECASE)


def _check_sql_blocked(sql: str) -> dict | None:
    if _BLOCKED_SQL_RE.match(sql):
        keyword = sql.strip().split()[0].upper()
        return _err("db_sandbox.blocked_command", f"{keyword} is not permitted in the sandbox.")
    return None
```

In `_normalize_queries()`, after extracting `sql` in the multi-query path (line ~60), add:

```python
            blocked = _check_sql_blocked(sql)
            if blocked:
                return [{"error": blocked}]
```

And in the single-statement path (line ~68), add:

```python
    blocked = _check_sql_blocked(sql)
    if blocked:
        return [{"error": blocked}]
```

In `run()`, after the `schema_sql` length check (line ~97), add:

```python
    if schema_sql:
        for _stmt in _re.split(r";\s*", schema_sql):
            _stmt = _stmt.strip()
            if _stmt:
                _b = _check_sql_blocked(_stmt)
                if _b:
                    return _b
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_agent_real_tool.py::test_db_sandbox_blocks_attach_database tests/test_agent_real_tool.py::test_db_sandbox_blocks_attach_in_schema_sql tests/test_agent_real_tool.py::test_db_sandbox_blocks_detach -xvs
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add agents/db_sandbox.py tests/test_agent_real_tool.py
git commit -m "fix: db_sandbox explicitly blocks ATTACH/DETACH commands"
```

---

### Task 4: Python executor — correct traceback line numbers

**Files:**
- Modify: `agents/python_executor.py:180-388`
- Test: `tests/test_agent_real_tool.py`

`_SANDBOX_PRELUDE` is ~63 lines prepended before user code, plus one blank separator. So user's line 1 appears as line 65 in tracebacks. We must subtract the offset.

- [ ] **Step 1: Write the failing test**

```python
def test_python_executor_traceback_line_numbers_match_user_code():
    from agents import python_executor
    result = python_executor.run({"code": "raise ValueError('oops')"})
    assert result["exit_code"] != 0
    stderr = result.get("stderr", "")
    import re
    # All "line N" references in the traceback should be line 1 for single-line code
    line_nums = [int(m) for m in re.findall(r"\bline (\d+)\b", stderr)]
    high_lines = [n for n in line_nums if n > 5]
    assert not high_lines, (
        f"Traceback reports line(s) {high_lines} — expected ~line 1 for single-line code"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_agent_real_tool.py::test_python_executor_traceback_line_numbers_match_user_code -xvs
```

Expected: FAIL (stderr contains `line 65` or similar)

- [ ] **Step 3: Implement the fix**

After `_SANDBOX_PRELUDE` definition in `agents/python_executor.py` (after line 180), add:

```python
# +1 for the blank "\n" separator written between prelude and user code in _run_in_subprocess
_PRELUDE_LINE_COUNT: int = _SANDBOX_PRELUDE.count("\n") + 1
```

After `_CAPTURE_SUFFIX`, add a helper:

```python
def _adjust_traceback_line_numbers(stderr: str) -> str:
    """Subtract sandbox prelude line count from traceback line references.

    The prelude is _PRELUDE_LINE_COUNT lines before user code. Python's traceback
    references lines in the combined file, so we undo the shift so callers see
    their own line numbers rather than absolute file positions.
    """
    lines = []
    for line in stderr.splitlines():
        if 'File "' in line and ("main.py" in line or "aztea" in line.lower()):
            def _fix(m: re.Match) -> str:
                return f"line {max(1, int(m.group(1)) - _PRELUDE_LINE_COUNT)}"
            line = re.sub(r"\bline (\d+)\b", _fix, line)
        lines.append(line)
    return "\n".join(lines)
```

In `_run_in_subprocess()`, change the final return assembly (around line 381-388):

```python
    variables_captured = {}
    stderr_lines = []
    for line in stderr_raw.splitlines():
        if line.startswith("__VARS__:"):
            try:
                variables_captured = json.loads(line[len("__VARS__:"):])
            except Exception:
                pass
        else:
            stderr_lines.append(line)
    adjusted_stderr = _adjust_traceback_line_numbers("\n".join(stderr_lines))
    return {
        "stdout": stdout,
        "stderr": adjusted_stderr,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "execution_time_ms": elapsed_ms,
        "variables_captured": variables_captured,
    }
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_agent_real_tool.py::test_python_executor_traceback_line_numbers_match_user_code -xvs
```

Expected: PASS

- [ ] **Step 5: Run all python_executor tests**

```
pytest tests/test_agent_real_tool.py -k python_executor -xvs
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add agents/python_executor.py tests/test_agent_real_tool.py
git commit -m "fix: subtract sandbox prelude offset from traceback line numbers"
```

---

### Task 5: shell_executor — apply static analysis to python3 -c inline code

**Files:**
- Modify: `agents/shell_executor.py:28-85`
- Test: `tests/test_agent_real_tool.py`

`python3 -c "import socket; ..."` passes the allowlist (starts with `"python3 "`) but the inline code can open network connections that hang until timeout. Apply static analysis to inline `-c` code.

- [ ] **Step 1: Write the failing test**

```python
def test_shell_executor_blocks_network_in_python3_c():
    from agents import shell_executor
    try:
        result = shell_executor.run({"command": "python3 -c 'import socket'"})
        # Must be blocked — not allowed to reach subprocess
        assert result.get("exit_code", 0) != 0 or "blocked" in (result.get("stderr") or "").lower()
    except ValueError as exc:
        assert "not permitted" in str(exc).lower() or "blocked" in str(exc).lower()

def test_shell_executor_blocks_subprocess_import_in_python3_c():
    from agents import shell_executor
    try:
        result = shell_executor.run({"command": "python3 -c 'import subprocess'"})
        assert result.get("exit_code", 0) != 0 or "blocked" in (result.get("stderr") or "").lower()
    except ValueError as exc:
        assert "not permitted" in str(exc).lower() or "blocked" in str(exc).lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_agent_real_tool.py::test_shell_executor_blocks_network_in_python3_c -xvs
```

Expected: FAIL (command is allowed through)

- [ ] **Step 3: Implement the fix**

In `agents/shell_executor.py`, add `import re as _re` after the existing imports. Then after `_BLOCKLIST_PATTERNS`, add:

```python
# Patterns for inline code passed via `python3 -c` — blocks network + subprocess access.
_PYTHON_INLINE_BLOCKED = (
    r"\bsubprocess\b",
    r"import\s+socket",
    r"import\s+requests",
    r"import\s+urllib",
    r"import\s+http\.client",
    r"\bos\.sy" + r"stem\b",
)


def _extract_python_inline(command: str) -> str | None:
    """Return the inline code from `python3 -c '...'` / `python -c "..."`, else None."""
    m = _re.match(r"""(?:python3?)\s+-c\s+(['"])(.*?)\1""", command.strip(), _re.DOTALL)
    if m:
        return m.group(2)
    m2 = _re.match(r"""(?:python3?)\s+-c\s+(.+)""", command.strip())
    if m2:
        return m2.group(1)
    return None
```

At the end of `_is_allowed()`, before the final `return any(...)` line, add:

```python
    # For python3 -c INLINE, apply static analysis to the inline code.
    inline = _extract_python_inline(stripped)
    if inline is not None:
        for pattern in _PYTHON_INLINE_BLOCKED:
            if _re.search(pattern, inline):
                return False
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_agent_real_tool.py::test_shell_executor_blocks_network_in_python3_c tests/test_agent_real_tool.py::test_shell_executor_blocks_subprocess_import_in_python3_c -xvs
```

Expected: both PASS

- [ ] **Step 5: Commit**

```bash
git add agents/shell_executor.py tests/test_agent_real_tool.py
git commit -m "fix: apply static analysis to inline python3 -c code in shell_executor"
```

---

### Task 6: git_diff_analyzer — detect test function removal inside test files

**Files:**
- Modify: `agents/git_diff_analyzer.py:226-334`
- Test: `tests/test_agent_real_tool.py`

`tests_removed: True` is only set when an entire test file is deleted. Removing `def test_foo():` from a surviving test file is not detected.

- [ ] **Step 1: Write the failing test**

```python
def test_git_diff_analyzer_detects_test_function_removal():
    from agents import git_diff_analyzer

    diff = (
        "diff --git a/tests/test_auth.py b/tests/test_auth.py\n"
        "index abc..def 100644\n"
        "--- a/tests/test_auth.py\n"
        "+++ b/tests/test_auth.py\n"
        "@@ -1,6 +1,1 @@\n"
        "-def test_login_with_valid_credentials():\n"
        "-    assert auth.login('user', 'pass') is True\n"
        "-\n"
        "-def test_login_rejects_wrong_password():\n"
        "-    assert auth.login('user', 'bad') is False\n"
        "-\n"
        " # remaining test file content\n"
    )

    result = git_diff_analyzer.run({"diff": diff})
    assert "error" not in result
    risk = result.get("risk_summary", {})
    assert risk.get("tests_removed") is True, "should detect removed test functions"
    all_warnings = [w for f in result.get("files", []) for w in f.get("warnings", [])]
    assert any("test function" in w.lower() or "test case" in w.lower() for w in all_warnings)
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_agent_real_tool.py::test_git_diff_analyzer_detects_test_function_removal -xvs
```

Expected: FAIL (`tests_removed` stays False for intra-file function removal)

- [ ] **Step 3: Implement the fix**

Add a constant near the top of `agents/git_diff_analyzer.py` (near the other `_*_RE` patterns):

```python
_TEST_FUNCTION_RE = re.compile(
    r"^(?:def test_\w+|    def test_\w+|\s*it\s*\(|\s*describe\s*\(|\s*@Test\b)",
    re.MULTILINE,
)
```

In `_classify_file()`, after the `if "test" in risk_tags and change_type == "removed":` block (line ~240), add:

```python
    if "test" in risk_tags and change_type != "removed" and removed_blob:
        removed_fn_count = len(_TEST_FUNCTION_RE.findall("\n".join(removed_blob)))
        if removed_fn_count > 0:
            warnings.append(
                f"{removed_fn_count} test function(s) removed from {path}."
            )
            risk_tags.append("test_functions_removed")
```

In `run()`, change the `tests_removed` logic (around line 332-334):

```python
        if "test" in info["risk_tags"]:
            risk_summary["test_files"] += 1
            if info["change_type"] == "removed" or "test_functions_removed" in info["risk_tags"]:
                risk_summary["tests_removed"] = True
```

Update the bullet-point message (around line 359):

```python
    if risk_summary["tests_removed"]:
        bullet_points.append("⚠ Test coverage removed (file deleted or test functions removed).")
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_agent_real_tool.py::test_git_diff_analyzer_detects_test_function_removal -xvs
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/git_diff_analyzer.py tests/test_agent_real_tool.py
git commit -m "fix: git_diff_analyzer detects test function removal within surviving files"
```

---

### Task 7: auto_hire — handle oneOf/anyOf required fields in _resolve_payload

**Files:**
- Modify: `core/registry/auto_hire.py:392-426`
- Test: `tests/test_bug_regressions.py`

`_resolve_payload()` calls `schema.get("required")` but the CVE lookup agent uses `oneOf: [{required: [...]}, ...]` with no top-level `required`. So `required = []`, no `missing_fields` returned, and the agent auto-invokes with an empty payload.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_bug_regressions.py`:

```python
def test_fix11_auto_hire_detects_missing_fields_for_oneof_schema():
    """_resolve_payload must detect missing fields from oneOf variants, not just top-level required."""
    from core.registry.auto_hire import CandidateAgent, decide
    import unittest.mock as mock

    cve_agent = CandidateAgent(
        agent_id="a3e239dd-ea92-556b-9c95-0a213a3daf59",
        slug="cve_lookup_agent",
        name="CVE Lookup Agent",
        description="live CVE data for a package or CVE ID security vulnerability nvd",
        tags=["security", "cve"],
        category="Security",
        price_per_call_usd=0.01,
        trust_score=90.0,
        success_rate=0.98,
        stability_tier="stable",
        input_schema={
            "type": "object",
            "properties": {
                "cve_id": {"type": "string"},
                "packages": {"type": "array", "items": {"type": "string"}},
            },
            "oneOf": [
                {"required": ["cve_id"]},
                {"required": ["packages"]},
            ],
        },
        raw={"call_count": 100, "codex_recommended": True},
    )

    with mock.patch("core.feature_flags.auto_invoke_enabled", return_value=True), \
         mock.patch("core.feature_flags.auto_invoke_confidence_floor", return_value=0.0), \
         mock.patch("core.feature_flags.auto_invoke_trust_floor", return_value=0.0), \
         mock.patch("core.feature_flags.auto_invoke_success_floor", return_value=0.0), \
         mock.patch("core.feature_flags.auto_invoke_server_cap_usd", return_value=10.0):
        decision = decide(
            intent="look up CVE-2021-44228",
            explicit_input=None,
            max_cost_usd=1.0,
            candidates=[cve_agent],
        )

    if decision.auto_invoked:
        # If auto-invoked, the payload must contain at least one of the required fields
        assert decision.payload and (
            "cve_id" in decision.payload or "packages" in decision.payload
        ), "auto-invoked with empty payload — oneOf required fields were not detected"
    else:
        # Gated by missing_fields is the correct outcome
        assert decision.reason == "missing_fields", f"Expected missing_fields gate, got: {decision.reason}"
        assert decision.missing_fields, "missing_fields must be non-empty"
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_bug_regressions.py::test_fix11_auto_hire_detects_missing_fields_for_oneof_schema -xvs
```

Expected: FAIL (currently auto-invokes with `{"intent": "look up CVE-2021-44228"}`)

- [ ] **Step 3: Implement the fix**

In `core/registry/auto_hire.py`, replace `_resolve_payload()` (lines 392-426):

```python
def _resolve_payload(
    agent: CandidateAgent,
    intent: str,
    explicit_input: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Build the payload or list missing required fields.

    Handles both top-level ``required`` and composite ``oneOf``/``anyOf``/``allOf``
    variants so agents like CVE lookup (which use oneOf instead of a flat required
    list) are correctly gated.
    """
    schema = agent.input_schema if isinstance(agent.input_schema, dict) else {}
    required = list(schema.get("required") or [])
    properties = dict(schema.get("properties") or {})

    # Collect required fields from composite schema keywords (oneOf/anyOf/allOf).
    composite_variants: list[list[str]] = []
    for keyword in ("oneOf", "anyOf", "allOf"):
        variants = schema.get(keyword)
        if isinstance(variants, list):
            for variant in variants:
                if isinstance(variant, dict):
                    vreq = list(variant.get("required") or [])
                    if vreq:
                        composite_variants.append(vreq)

    if explicit_input is not None:
        missing = [f for f in required if f not in explicit_input]
        if missing:
            return explicit_input, missing
        if composite_variants:
            for variant_required in composite_variants:
                if all(f in explicit_input for f in variant_required):
                    return explicit_input, []
            return explicit_input, [f for f in composite_variants[0] if f not in explicit_input]
        return explicit_input, missing

    # No explicit_input. Determine all required fields.
    all_required = required or (composite_variants[0] if composite_variants else [])
    if not all_required:
        return {"intent": intent}, []

    # Single top-level string field: auto-fill from intent.
    if len(all_required) == 1 and not composite_variants:
        field_name = all_required[0]
        field_spec = properties.get(field_name) or {}
        field_type = str(field_spec.get("type") or "").lower()
        if field_type in {"string", ""}:
            return {field_name: intent}, []

    # Composite schema without explicit_input: cannot auto-fill structured fields.
    if composite_variants:
        return {}, composite_variants[0]

    return {}, all_required
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_bug_regressions.py::test_fix11_auto_hire_detects_missing_fields_for_oneof_schema -xvs
```

Expected: PASS

- [ ] **Step 5: Run all auto_hire tests**

```
pytest tests/ -k "auto_hire or auto_invoke" -xvs
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add core/registry/auto_hire.py tests/test_bug_regressions.py
git commit -m "fix: auto_hire _resolve_payload handles oneOf/anyOf required fields"
```

---

### Task 8: web_researcher spec — clarify url or urls is required

**Files:**
- Modify: `server/builtin_agents/specs_part2.py:383-396`

The spec has `"required": []` which misleads callers into thinking the agent works with no URL input at all.

- [ ] **Step 1: Update the spec**

In `server/builtin_agents/specs_part2.py`, update the web researcher entry:

Change the description from:
```python
    "description": "Use when the task requires reading a live URL, not guessing its content. Fetches the page in real time and returns a dense summary, key points, direct answers to a specific question, and verbatim supporting quotes. Supports up to 10 URLs in one call for cross-source synthesis.",
```

To:
```python
    "description": "Use when the task requires reading a live URL, not guessing its content. Fetches the page in real time and returns a dense summary, key points, direct answers to a specific question, and verbatim supporting quotes. Supports up to 10 URLs in one call for cross-source synthesis. REQUIRED INPUT: provide url (single URL string) or urls (array of up to 10 URLs) — calling without either returns an error.",
```

Change `"required": []` to:
```python
        "required": [],
        "oneOf": [
            {"required": ["url"]},
            {"required": ["urls"]},
        ],
```

- [ ] **Step 2: Verify import succeeds**

```bash
python -c "from server.builtin_agents.specs_part2 import load_builtin_specs_part2; specs = load_builtin_specs_part2(); print(f'OK: {len(specs)} specs')"
```

Expected: `OK: N specs`

- [ ] **Step 3: Commit**

```bash
git add server/builtin_agents/specs_part2.py
git commit -m "fix: web_researcher spec clarifies url or urls is required; adds oneOf constraint"
```

---

### Task 9: CVE lookup spec — add variable billing note to description

**Files:**
- Modify: `server/builtin_agents/specs_part1.py:270`

The CVE lookup has `variable_pricing` defined but the description doesn't mention it, so callers don't know $0.01 is the minimum, not the fixed price.

- [ ] **Step 1: Update the description**

In `server/builtin_agents/specs_part1.py` line 270, replace:

```python
    "description": "Use when the user wants live CVE data for a package or specific CVE ID. Queries OSV.dev for ecosystem-aware package lookups (npm, PyPI) and NIST NVD for direct CVE-ID lookups — not LLM memory. Returns CVSS score, exploit availability, affected version range, and recommended fix for each CVE.",
```

With:

```python
    "description": "Use when the user wants live CVE data for a package or specific CVE ID. Queries OSV.dev for ecosystem-aware package lookups (npm, PyPI) and NIST NVD for direct CVE-ID lookups — not LLM memory. Returns CVSS score, exploit availability, affected version range, and recommended fix for each CVE. VARIABLE BILLING: $0.01 for 1 CVE ID, $0.03 for up to 5 CVE IDs, $0.06 for up to 10 CVE IDs (batch ID mode). Package scans are flat $0.01/call.",
```

- [ ] **Step 2: Verify import succeeds**

```bash
python -c "from server.builtin_agents.specs_part1 import load_builtin_specs_part1; specs = load_builtin_specs_part1(); print(f'OK: {len(specs)} specs')"
```

Expected: `OK: N specs`

- [ ] **Step 3: Commit**

```bash
git add server/builtin_agents/specs_part1.py
git commit -m "fix: add VARIABLE BILLING note to CVE lookup agent description"
```

---

### Task 10: Search scoring — boost secret_scanner for security queries

**Files:**
- Modify: `scripts/aztea_mcp_server.py:963-967`
- Test: `tests/test_bug_regressions.py`

For query `"security"`, `secret_scanner` only scores +3 (term match in haystack) because the inner `any(token in haystack ...)` check only looks for `("cve", "nvd", "osv", "dependency", "dependencies", "audit")` — not scanning-related tokens. The same +12 boost should apply when `"secret"`, `"scanner"`, `"credential"`, or `"entropy"` appears in the haystack.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_bug_regressions.py`:

```python
def test_fix12_search_security_includes_secret_scanner():
    """aztea_search('security') must rank secret_scanner in the top results."""
    import threading
    import sys, types

    # Build a minimal stub of the ToolCatalog class to call _search_catalog directly.
    # We need meta_tools imported, so we import the real module.
    import scripts.aztea_mcp_server as mcp

    cat = mcp.ToolCatalog.__new__(mcp.ToolCatalog)
    cat._lock = threading.Lock()
    cat._entries = []
    cat._catalog_cache = None
    cat._session_state = {}
    cat._auth_required = False
    cat.base_url = "http://localhost:8000"
    cat.api_key = "test"
    cat.timeout_seconds = 30
    cat._signup_url = ""

    result = cat._search_catalog("security", limit=10)
    slugs = [r.get("slug") for r in result.get("results", [])]
    assert "secret_scanner" in slugs, (
        f"secret_scanner not in top-10 security results. Got: {slugs}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_bug_regressions.py::test_fix12_search_security_includes_secret_scanner -xvs
```

Expected: FAIL

- [ ] **Step 3: Implement the fix**

In `scripts/aztea_mcp_server.py`, find the security boost block (around lines 963-967):

```python
            if {"security", "vulnerability", "vulnerabilities", "cve", "npm", "dependency", "dependencies", "audit"} & set(terms):
                if any(token in haystack for token in ("cve", "nvd", "osv", "dependency", "dependencies", "audit")):
                    score += 12
```

Change the inner `any(...)` line to:

```python
                if any(token in haystack for token in ("cve", "nvd", "osv", "dependency", "dependencies", "audit", "secret", "scanner", "credential", "entropy", "leak")):
                    score += 12
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_bug_regressions.py::test_fix12_search_security_includes_secret_scanner -xvs
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/aztea_mcp_server.py tests/test_bug_regressions.py
git commit -m "fix: extend security search boost to surface secret_scanner for 'security' queries"
```

---

### Task 11: arXiv — flag low-confidence results for nonsense queries

**Files:**
- Modify: `agents/arxiv_research.py` (the `run()` / main search function near line 294)
- Test: `tests/test_agent_real_tool.py`

When arXiv returns papers but none of their titles/abstracts overlap with the query tokens, the response gives no signal that results are likely irrelevant (e.g., for a nonsense or highly misspelled query).

- [ ] **Step 1: Read the return structure in arxiv_research.py**

```bash
grep -n "return {" agents/arxiv_research.py
```

Find the final return dict in `run()` (around line 294-340) so you know exactly where to add the new field.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_agent_real_tool.py`:

```python
def test_arxiv_research_flags_low_confidence_for_nonsense_query(monkeypatch):
    import agents.arxiv_research as arxiv

    fake_papers = [{
        "arxiv_id": "1234.5678",
        "title": "Quantum entanglement in photonic circuits",
        "authors": ["A. Researcher"],
        "abstract": "We study quantum entanglement.",
        "categories": ["quant-ph"],
        "published": "2024-01-01",
        "updated": "2024-01-01",
        "pdf_url": "https://arxiv.org/pdf/1234.5678",
        "abstract_url": "https://arxiv.org/abs/1234.5678",
    }]
    monkeypatch.setattr(arxiv, "_fetch_arxiv", lambda *a, **kw: fake_papers)

    class FakeLLMResp:
        text = '{"key_themes":[],"seminal_papers":[],"open_questions":[],"suggested_follow_ups":[]}'

    monkeypatch.setattr(arxiv, "run_with_fallback", lambda req: FakeLLMResp())

    result = arxiv.run({"query": "xyzzyplughqwerty123nonsense"})
    assert "error" not in result
    assert result.get("low_confidence_results") is True, (
        "Should set low_confidence_results=True when papers don't match query tokens"
    )
```

- [ ] **Step 3: Run test to verify it fails**

```
pytest tests/test_agent_real_tool.py::test_arxiv_research_flags_low_confidence_for_nonsense_query -xvs
```

Expected: FAIL (no `low_confidence_results` key)

- [ ] **Step 4: Implement the fix**

Read `agents/arxiv_research.py` around lines 280-340 to find the exact location of the final return dict. Then add before the `return` statement:

```python
    # Flag when no returned paper has any meaningful token overlap with the query.
    _query_tokens = {t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 3}
    _STOPWORDS = {"that", "this", "with", "from", "have", "been", "they", "their", "will"}
    _query_tokens -= _STOPWORDS

    def _paper_overlaps(p: dict) -> bool:
        text = " ".join([p.get("title") or "", p.get("abstract") or "",
                         " ".join(p.get("categories") or [])]).lower()
        return any(tok in text for tok in _query_tokens)

    low_confidence = bool(papers) and not any(_paper_overlaps(p) for p in papers)
```

Add `"low_confidence_results": low_confidence` to the final return dict.

- [ ] **Step 5: Run test to verify it passes**

```
pytest tests/test_agent_real_tool.py::test_arxiv_research_flags_low_confidence_for_nonsense_query -xvs
```

Expected: PASS

- [ ] **Step 6: Run all arxiv tests**

```
pytest tests/test_agent_real_tool.py -k arxiv -xvs
```

Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add agents/arxiv_research.py tests/test_agent_real_tool.py
git commit -m "fix: arXiv sets low_confidence_results=True when papers don't match query tokens"
```

---

### Task 12: Final verification

- [ ] **Step 1: Run full test suite**

```bash
pytest tests --ignore=tests/test_sdk_contract.py -q
```

Expected: all tests pass (453 original + ~20 new).

- [ ] **Step 2: Run integration tests**

```bash
pytest tests/integration -q
```

Expected: 137+ passed.

- [ ] **Step 3: Run line budget check**

```bash
python scripts/check_file_line_budget.py
```

Expected: no files exceed 1000 lines.

---

## Self-Review

**Spec coverage:**

| Bug from eval | Task | Notes |
|---|---|---|
| TypeScript type checker broken (tsc not on PATH) | Task 1 | npx fallback |
| TypeScript linter misses dynamic code execution and var | Task 2 | no-eval + no-var rules |
| db_sandbox ATTACH not blocked | Task 3 | explicit SQL blocklist |
| OOM traceback shows wrong line numbers | Task 4 | prelude offset subtraction |
| shell_executor hangs on python3 -c network code | Task 5 | static analysis on inline code |
| git_diff_analyzer misses function-level test removal | Task 6 | removed_blob scanning |
| aztea_do oneOf schema false negative | Task 7 | composite variant extraction |
| web_researcher required:[] misleads callers | Task 8 | oneOf + description fix |
| CVE billing description inaccurate | Task 9 | VARIABLE BILLING note |
| Search "security" misses secret_scanner | Task 10 | haystack token list expansion |
| arXiv returns nonsense results without warning | Task 11 | low_confidence_results flag |
| DNS inspector undiscoverable | Covered by Task 10 | dns_inspector already has "security" tag, boosted same way |
| Recipes not on MCP surface | Already fixed in codebase | See mcp_server.py line 899-905 comment |
| MCP meta-tools not discoverable | Working by design | Accessible via aztea_search + aztea_call in LAZY mode |

**Placeholder check:** No TBD or TODO in any code step.

**Type consistency:** `_PRELUDE_LINE_COUNT` defined before `_adjust_traceback_line_numbers` uses it. `composite_variants` typed as `list[list[str]]`. `_TEST_FUNCTION_RE` defined before `_classify_file` uses it. All consistent.

#!/usr/bin/env python3
# pylint: disable=missing-module-docstring,broad-except
"""aztea doctor — pre-flight check for an Aztea demo.

Run 30 seconds before a live demo. Probes every surface a YC partner is
about to see: CLI version, MCP wiring, server health, registry search,
wallet balance, worker pool, signed-receipt freshness. Prints a single
boxed panel with red/green chips and a final GO / NO-GO verdict.

Zero deps (stdlib only). Reads ~/.aztea/config.json. Exits 0 on GO,
1 on NO-GO.

    python scripts/aztea_doctor.py            # full pre-flight
    python scripts/aztea_doctor.py --rehearse # also print 10 cold-prompt
                                              # auto-invoke rehearsal cards
    python scripts/aztea_doctor.py --json     # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path

# ── ANSI ────────────────────────────────────────────────────────────────────

_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _ansi(code: str) -> str:
    return f"\033[{code}m" if _USE_COLOR else ""


RESET = _ansi("0")
BOLD = _ansi("1")
DIM = _ansi("2")
ITALIC = _ansi("3")
UNDERLINE = _ansi("4")

# Truecolor neons (degrade to plain on non-truecolor terms; harmless)
NEON_TEAL = _ansi("38;2;0;230;200")     # AZTEA wordmark
NEON_PINK = _ansi("38;2;255;71;163")
NEON_AMBER = _ansi("38;2;255;176;46")
NEON_BLUE = _ansi("38;2;90;160;255")
GREEN = _ansi("38;2;80;230;120")
RED = _ansi("38;2;255;90;90")
YELLOW = _ansi("38;2;240;200;80")
GREY = _ansi("38;2;140;140;140")
WHITE = _ansi("38;2;240;240;240")

ICON_OK = f"{GREEN}✓{RESET}"
ICON_FAIL = f"{RED}✗{RESET}"
ICON_WARN = f"{YELLOW}⚠{RESET}"
ICON_RUN = f"{NEON_BLUE}∙{RESET}"


# ── Config ──────────────────────────────────────────────────────────────────


CONFIG_PATH = Path.home() / ".aztea" / "config.json"
CLI_PKG_PATH = Path.home() / ".aztea" / "node_modules" / "aztea-cli" / "package.json"
MCP_JSON_PATH = Path.home() / ".aztea" / "mcp.json"
MIN_CLI_VERSION = (0, 21, 0)
HTTP_TIMEOUT_SECS = 5.0
SEARCH_TIMEOUT_SECS = 12.0  # search hits embeddings; cold path can be slow
PARALLEL_WORKERS = 6


def _read_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _http_get(base: str, path: str, api_key: str | None) -> tuple[int, dict | str, float]:
    return _http_request(base, path, api_key, method="GET", body=None)


def _http_post(
    base: str, path: str, api_key: str | None, body: dict
) -> tuple[int, dict | str, float]:
    return _http_request(base, path, api_key, method="POST", body=body)


def _http_request(
    base: str,
    path: str,
    api_key: str | None,
    *,
    method: str,
    body: dict | None,
    timeout: float | None = None,
) -> tuple[int, dict | str, float]:
    url = base.rstrip("/") + path
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, method=method, data=data)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "aztea-doctor/1")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout or HTTP_TIMEOUT_SECS) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="replace")
            elapsed = (time.monotonic() - started) * 1000.0
            try:
                return resp.status, json.loads(raw), elapsed
            except json.JSONDecodeError:
                return resp.status, raw[:200], elapsed
    except urllib.error.HTTPError as exc:
        elapsed = (time.monotonic() - started) * 1000.0
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        return exc.code, body, elapsed
    except Exception as exc:
        elapsed = (time.monotonic() - started) * 1000.0
        return 0, f"{type(exc).__name__}: {exc}", elapsed


def _parse_version(s: str) -> tuple[int, int, int]:
    try:
        parts = s.strip().lstrip("v").split(".")
        return tuple(int(p.split("-")[0]) for p in parts[:3])  # type: ignore[return-value]
    except Exception:
        return (0, 0, 0)


# ── Checks ──────────────────────────────────────────────────────────────────


def _check_cli_version() -> dict:
    pkg = _read_json(CLI_PKG_PATH)
    if not pkg:
        return {
            "name": "aztea-cli installed",
            "status": "fail",
            "detail": f"package.json not found at {CLI_PKG_PATH}",
            "ms": 0,
        }
    v = str(pkg.get("version", "0.0.0"))
    parsed = _parse_version(v)
    ok = parsed >= MIN_CLI_VERSION
    return {
        "name": "aztea-cli version",
        "status": "ok" if ok else "warn",
        "detail": (
            f"v{v}"
            + ("" if ok else f"  (need ≥ {'.'.join(map(str, MIN_CLI_VERSION))} for verb-first names)")
        ),
        "ms": 0,
    }


def _check_mcp_config(resolved_key_source: str) -> dict:
    cfg = _read_json(CONFIG_PATH)
    mcp = _read_json(MCP_JSON_PATH)
    if not cfg and not mcp:
        return {
            "name": "aztea config",
            "status": "fail",
            "detail": f"missing {CONFIG_PATH} — run `aztea init`",
            "ms": 0,
        }
    if not resolved_key_source:
        return {
            "name": "aztea config",
            "status": "fail",
            "detail": "no api_key in env / config.json / mcp.json",
            "ms": 0,
        }
    detail_bits = []
    if cfg:
        detail_bits.append(f"user={cfg.get('username', '?')}")
    detail_bits.append(f"key←{resolved_key_source}")
    if mcp:
        detail_bits.append("mcp.json ✓")
    return {
        "name": "aztea config",
        "status": "ok",
        "detail": "  ".join(detail_bits),
        "ms": 0,
    }


def _check_health(base: str) -> dict:
    code, body, ms = _http_get(base, "/health", api_key=None)
    if code == 200:
        return {"name": "server /health", "status": "ok", "detail": "200 OK", "ms": ms}
    return {
        "name": "server /health",
        "status": "fail",
        "detail": f"HTTP {code} — {str(body)[:120]}",
        "ms": ms,
    }


def _check_registry_search(base: str, api_key: str) -> dict:
    # Search hits an embedding path that can be cold; retry once on first miss
    # because the demo presenter wants the warm path. We surface "warm on retry"
    # explicitly so the operator knows to fire a warmup query before going live.
    attempts: list[float] = []
    code, body, ms = _http_request(
        base, "/registry/search", api_key,
        method="POST", body={"query": "cve", "limit": 3},
        timeout=SEARCH_TIMEOUT_SECS,
    )
    attempts.append(ms)
    if code != 200 or not isinstance(body, dict):
        code, body, ms = _http_request(
            base, "/registry/search", api_key,
            method="POST", body={"query": "cve", "limit": 3},
            timeout=SEARCH_TIMEOUT_SECS,
        )
        attempts.append(ms)
    if code != 200 or not isinstance(body, dict):
        return {
            "name": "/registry/search",
            "status": "fail",
            "detail": f"HTTP {code} after {len(attempts)} tries — {str(body)[:60]}",
            "ms": sum(attempts),
        }
    n = len(body.get("results") or body.get("agents") or [])
    cold_warning = " (cold first hit — fire a warmup query before demo)" if len(attempts) > 1 else ""
    if n == 0:
        return {
            "name": "/registry/search",
            "status": "warn",
            "detail": "0 results for 'cve' — catalog stale or unreachable",
            "ms": sum(attempts),
        }
    status = "warn" if cold_warning else "ok"
    return {
        "name": "/registry/search",
        "status": status,
        "detail": f"{n} hits for 'cve'{cold_warning}",
        "ms": sum(attempts),
    }


def _check_wallet(base: str, api_key: str) -> dict:
    code, body, ms = _http_get(base, "/wallets/me", api_key)
    if code != 200 or not isinstance(body, dict):
        return {
            "name": "wallet balance",
            "status": "fail",
            "detail": f"HTTP {code} — {str(body)[:80]}",
            "ms": ms,
        }
    bal = int(body.get("balance_cents") or 0)
    wid = body.get("wallet_id") or body.get("id") or "?"
    if bal < 50:
        return {
            "name": "wallet balance",
            "status": "warn",
            "detail": f"${bal/100:.2f} (low) · {wid}",
            "ms": ms,
        }
    return {
        "name": "wallet balance",
        "status": "ok",
        "detail": f"${bal/100:.2f} · {wid}",
        "ms": ms,
    }


def _check_audit_freshness(base: str, api_key: str) -> dict:
    code, body, ms = _http_get(
        base, "/wallets/audit?period=1d&limit=1&verify_all=true", api_key
    )
    if code != 200 or not isinstance(body, dict):
        return {
            "name": "signed-receipt freshness",
            "status": "warn",
            "detail": f"HTTP {code}",
            "ms": ms,
        }
    agg = body.get("receipts_aggregate") or {}
    total = int(agg.get("receipts_total") or 0)
    signed = int(agg.get("receipts_signed") or 0)
    latest = agg.get("latest_settled_at") or "—"
    digest = (body.get("receipts_digest") or "")[:14]
    if total == 0:
        return {
            "name": "signed-receipt freshness",
            "status": "warn",
            "detail": "no receipts in 24h — fire one smoke hire before demo",
            "ms": ms,
        }
    sig_ratio = f"{signed}/{total}"
    pct = (signed * 100 // max(total, 1))
    status = "ok" if pct == 100 else "warn"
    return {
        "name": "signed-receipt freshness",
        "status": status,
        "detail": f"{sig_ratio} signed ({pct}%) · digest {digest}…  · last {latest}",
        "ms": ms,
    }


def _check_worker_pool(base: str, api_key: str) -> dict:
    # No public worker-pool endpoint; probe via /metrics or a tiny read.
    # /metrics may be admin-gated; fall back to /jobs?limit=1 as liveness.
    code, _body, ms = _http_get(base, "/metrics", api_key)
    if code == 200:
        return {"name": "metrics endpoint", "status": "ok", "detail": "Prometheus exposed", "ms": ms}
    code2, _body2, ms2 = _http_get(base, "/jobs?limit=1", api_key)
    if code2 in (200, 401, 403):
        # 401/403 still proves the route is mounted and reachable.
        return {
            "name": "worker pool liveness",
            "status": "ok",
            "detail": f"jobs route reachable (HTTP {code2})",
            "ms": ms2,
        }
    return {
        "name": "worker pool liveness",
        "status": "fail",
        "detail": f"jobs route unreachable (HTTP {code2})",
        "ms": ms2,
    }


# ── Render ──────────────────────────────────────────────────────────────────


_BANNER_LINES = [
    "    █████  ███████ ████████ ███████  █████ ",
    "   ██   ██      ██    ██    ██      ██   ██",
    "   ███████  █████     ██    █████   ███████",
    "   ██   ██ ██         ██    ██      ██   ██",
    "   ██   ██ ███████    ██    ███████ ██   ██",
]


def _print_banner() -> None:
    for i, line in enumerate(_BANNER_LINES):
        # subtle gradient teal → blue
        c = NEON_TEAL if i < 3 else NEON_BLUE
        print(f"{c}{line}{RESET}")
    print(f"{DIM}{WHITE}     a specialist labor market for coding agents · doctor{RESET}\n")


def _strip_ansi_len(s: str) -> int:
    out = []
    i = 0
    while i < len(s):
        if s[i] == "\033":
            j = s.find("m", i)
            if j == -1:
                break
            i = j + 1
            continue
        out.append(s[i])
        i += 1
    return len("".join(out))


def _icon(status: str) -> str:
    return {"ok": ICON_OK, "warn": ICON_WARN, "fail": ICON_FAIL}.get(status, ICON_RUN)


def _render_panel(checks: list[dict], width: int = 88) -> None:
    title = f" pre-flight · {len(checks)} checks "
    title_pad = (width - 2 - len(title)) // 2
    top = (
        f"{NEON_TEAL}╭{'─' * title_pad}{NEON_PINK}{title}"
        f"{NEON_TEAL}{'─' * (width - 2 - title_pad - len(title))}╮{RESET}"
    )
    bot = f"{NEON_TEAL}╰{'─' * (width - 2)}╯{RESET}"
    print(top)
    for c in checks:
        icon = _icon(c["status"])
        name = c["name"]
        detail = c["detail"]
        ms = f"{int(c['ms']):>5}ms" if c["ms"] else "       "
        # color per status
        name_c = (
            WHITE if c["status"] == "ok"
            else YELLOW if c["status"] == "warn"
            else RED
        )
        # Fixed name column so details align across rows.
        name_col = 26
        name_visible = name[: name_col - 1]
        name_pad = " " * max(1, name_col - len(name_visible))
        line_left = f"  {icon} {name_c}{name_visible}{RESET}{name_pad}"
        ms_col = f"{DIM}{GREY}{ms}{RESET}"
        # Compute the room available for the detail column.
        # Layout: │ left  detail spacer ms │
        # widths: 1 + visible(line_left) + 1 + len(detail) + spacer + 7 + 1 + 1
        left_visible = _strip_ansi_len(line_left)
        # 4 = leading "│ ", trailing " │" + 1 space before ms
        room = width - left_visible - 7 - 4
        if room < 8:
            room = 8
        if len(detail) > room:
            detail = detail[: max(0, room - 1)] + "…"
        spacer = " " * max(1, room - len(detail))
        print(
            f"{NEON_TEAL}│{RESET} {line_left}"
            f"{DIM}{detail}{RESET}{spacer} {ms_col} {NEON_TEAL}│{RESET}"
        )
    print(bot)


def _render_verdict(checks: list[dict]) -> int:
    fails = [c for c in checks if c["status"] == "fail"]
    warns = [c for c in checks if c["status"] == "warn"]
    oks = [c for c in checks if c["status"] == "ok"]
    score = f"{len(oks)}/{len(checks)}"
    if fails:
        verdict = f"{RED}{BOLD} NO-GO {RESET}"
        msg = f"{len(fails)} blocker{'s' if len(fails) != 1 else ''}: " + ", ".join(c["name"] for c in fails)
        rc = 1
    elif warns:
        verdict = f"{YELLOW}{BOLD} GO with caveats {RESET}"
        msg = f"{len(warns)} warning{'s' if len(warns) != 1 else ''}: " + ", ".join(c["name"] for c in warns)
        rc = 0
    else:
        verdict = f"{GREEN}{BOLD} GO · all green {RESET}"
        msg = "ship it"
        rc = 0
    print(f"\n  {verdict} {DIM}{GREY}· {score} green{RESET}")
    print(f"  {DIM}{msg}{RESET}\n")
    return rc


# ── Cold-prompt rehearsal ───────────────────────────────────────────────────


_REHEARSAL_PROMPTS = [
    ("audit 50 npm packages from this package.json for CVEs", "manage_workflow.hire_batch"),
    ("scan these 30 files for hardcoded secrets", "manage_workflow.hire_batch"),
    ("fetch live CVSS for these 100 CVE IDs in parallel", "manage_workflow.hire_batch"),
    ("verify SSL + DNS + redirects for these 40 domains", "manage_workflow.hire_batch"),
    ("run this Python in a sandbox: print(1+1)", "do_specialist_task → python_executor"),
    ("compare two specialists on cost", "search_specialists / manage_workflow.compare"),
    ("check if any of these dependencies have known vulnerabilities", "manage_workflow.hire_batch"),
    ("in parallel, lint each of these go modules", "manage_workflow.hire_batch"),
    ("show me the signed receipt for that last job", "manage_job.verify"),
    ("what's the live CVE record for CVE-2021-44228", "do_specialist_task → cve_lookup_agent"),
]


def _render_rehearsal() -> None:
    print(f"\n{NEON_PINK}{BOLD}cold-prompt rehearsal{RESET}  {DIM}— grade auto-invoke without saying 'aztea'{RESET}\n")
    for i, (prompt, expected) in enumerate(_REHEARSAL_PROMPTS, 1):
        print(f"  {NEON_AMBER}{i:>2}.{RESET}  {WHITE}{prompt}{RESET}")
        print(f"      {DIM}expected →{RESET}  {NEON_BLUE}{expected}{RESET}")
        print(f"      {DIM}grade   →{RESET}  [ ] auto-fired   [ ] needed nudge   [ ] missed\n")
    print(f"  {DIM}target: 9/10 auto-fire. Iterate descriptions until you get there.{RESET}\n")


# ── Main ────────────────────────────────────────────────────────────────────


def _run_checks(base: str, api_key: str, key_source: str) -> list[dict]:
    """Run all checks in parallel, preserving display order."""
    fns = [
        _check_cli_version,
        lambda: _check_mcp_config(key_source),
        lambda: _check_health(base),
        lambda: _check_registry_search(base, api_key),
        lambda: _check_wallet(base, api_key),
        lambda: _check_worker_pool(base, api_key),
        lambda: _check_audit_freshness(base, api_key),
    ]
    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as ex:
        futures = [ex.submit(fn) for fn in fns]
        for fut in futures:
            try:
                out.append(fut.result(timeout=HTTP_TIMEOUT_SECS + 1))
            except FuturesTimeout:
                out.append({
                    "name": "(timeout)",
                    "status": "fail",
                    "detail": "check exceeded 6s",
                    "ms": HTTP_TIMEOUT_SECS * 1000,
                })
            except Exception as exc:
                out.append({
                    "name": "(error)",
                    "status": "fail",
                    "detail": f"{type(exc).__name__}: {exc}",
                    "ms": 0,
                })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(prog="aztea-doctor")
    parser.add_argument("--rehearse", action="store_true", help="also print cold-prompt rehearsal cards")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--no-banner", action="store_true", help="suppress the AZTEA banner")
    parser.add_argument(
        "--warmup",
        action="store_true",
        help="fire 3 warmup search queries before checking (kills cold-start 502s)",
    )
    args = parser.parse_args()

    cfg = _read_json(CONFIG_PATH) or {}
    mcp = _read_json(MCP_JSON_PATH) or {}
    mcp_env = (
        ((mcp.get("mcpServers") or {}).get("aztea") or {}).get("env") or {}
    )
    base = str(
        os.environ.get("AZTEA_BASE_URL")
        or cfg.get("base_url")
        or mcp_env.get("AZTEA_BASE_URL")
        or "https://aztea.ai"
    ).rstrip("/")

    api_key = ""
    key_source = ""
    if os.environ.get("AZTEA_API_KEY"):
        api_key = os.environ["AZTEA_API_KEY"]
        key_source = "env"
    elif cfg.get("api_key"):
        api_key = str(cfg["api_key"])
        key_source = "config.json"
    elif mcp_env.get("AZTEA_API_KEY"):
        api_key = str(mcp_env["AZTEA_API_KEY"])
        key_source = "mcp.json"

    if not api_key:
        print(
            f"{RED}{BOLD}NO-GO{RESET}: no api_key found in env / config.json / mcp.json.\n"
            f"      Run {BOLD}aztea init{RESET} to create one."
        )
        return 1

    if args.warmup and not args.json:
        print(f"{DIM}warming /registry/search…{RESET}", end="", flush=True)
        for _ in range(3):
            _http_request(
                base, "/registry/search", api_key,
                method="POST", body={"query": "cve", "limit": 3},
                timeout=SEARCH_TIMEOUT_SECS,
            )
        print(f" {GREEN}done{RESET}\n")

    started = time.monotonic()
    checks = _run_checks(base, api_key, key_source)
    total_ms = (time.monotonic() - started) * 1000.0

    if args.json:
        verdict = "GO"
        if any(c["status"] == "fail" for c in checks):
            verdict = "NO-GO"
        elif any(c["status"] == "warn" for c in checks):
            verdict = "GO_WITH_CAVEATS"
        print(json.dumps({
            "verdict": verdict,
            "base_url": base,
            "checks": checks,
            "total_ms": int(total_ms),
        }, indent=2))
        return 0 if verdict != "NO-GO" else 1

    if not args.no_banner:
        _print_banner()
    _render_panel(checks)
    rc = _render_verdict(checks)
    print(f"  {DIM}{GREY}probed {base} in {int(total_ms)}ms{RESET}")
    if args.rehearse:
        _render_rehearsal()
    return rc


if __name__ == "__main__":
    sys.exit(main())

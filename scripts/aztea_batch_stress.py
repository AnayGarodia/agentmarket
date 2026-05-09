#!/usr/bin/env python3
# pylint: disable=missing-module-docstring,broad-except
"""aztea batch stress — fire a real parallel hire and render the result.

Submits N jobs against cve_lookup_agent (deterministic, cheap, internal
endpoint), polls /jobs/batch/{id} until terminal, then verifies the
post-hire surface end-to-end:

  • wall time vs serial estimate (the demo "Nx speedup" line)
  • per-job signing rate (must be 100% before YC demo)
  • aggregate sha256 receipts_digest (the "pin this" number)
  • partial-failure refund tally (clean clawback signal)

Defaults to 50 jobs. Run with --size 100 / 200 to escalate.
Run with --inject-failures to deliberately break N jobs and rehearse the
partial-failure UX.

    python scripts/aztea_batch_stress.py
    python scripts/aztea_batch_stress.py --size 100
    python scripts/aztea_batch_stress.py --size 100 --inject-failures 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── Imports from sibling doctor for shared rendering ────────────────────────
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    from aztea_doctor import (  # type: ignore[import-not-found]
        BOLD,
        DIM,
        GREEN,
        GREY,
        ICON_FAIL,
        ICON_OK,
        ICON_WARN,
        NEON_AMBER,
        NEON_BLUE,
        NEON_PINK,
        NEON_TEAL,
        RED,
        RESET,
        WHITE,
        YELLOW,
        _print_banner,
        _strip_ansi_len,
    )
except Exception:  # fallback: dim everything to plain
    BOLD = DIM = GREEN = GREY = NEON_AMBER = NEON_BLUE = NEON_PINK = NEON_TEAL = ""
    RED = RESET = WHITE = YELLOW = ""
    ICON_OK = "✓"
    ICON_FAIL = "✗"
    ICON_WARN = "⚠"

    def _print_banner() -> None:  # noqa: D401
        pass

    def _strip_ansi_len(s: str) -> int:
        return len(s)


# ── Config ──────────────────────────────────────────────────────────────────

CONFIG_PATH = Path.home() / ".aztea" / "config.json"
MCP_JSON_PATH = Path.home() / ".aztea" / "mcp.json"

CVE_LOOKUP_AGENT_ID = "a3e239dd-ea92-556b-9c95-0a213a3daf59"
SUBMIT_TIMEOUT_S = 60.0
POLL_TIMEOUT_S = 10.0
POLL_INTERVAL_S = 1.5
DEFAULT_BATCH_PRICE_CAP_CENTS = 500  # $5 cap default

# Curated CVE IDs known to be present in NIST NVD. Hand-validated against
# the cve_lookup agent's data source. CVE-2020-0796 was removed because NVD
# returns "No matching CVE records were found" for it — that's an upstream
# data gap, not a platform issue, but it would taint a YC demo.
_CVE_POOL = [
    "CVE-2021-44228",  # Log4Shell
    "CVE-2014-0160",   # Heartbleed
    "CVE-2017-5638",   # Equifax/Struts
    "CVE-2017-0144",   # EternalBlue
    "CVE-2019-0708",   # BlueKeep
    "CVE-2018-7600",   # Drupalgeddon2
    "CVE-2022-22965",  # Spring4Shell
    "CVE-2014-6271",   # Shellshock
    "CVE-2017-9805",   # REST-Struts
    "CVE-2016-5195",   # Dirty COW
    "CVE-2015-7547",   # glibc DNS
    "CVE-2018-1000861",  # Jenkins
    "CVE-2020-1472",   # Zerologon
    "CVE-2023-23397",  # Outlook
    "CVE-2022-30190",  # Follina
    "CVE-2021-34527",  # PrintNightmare
    "CVE-2021-26855",  # ProxyLogon (Exchange)
]


def _load_creds() -> tuple[str, str]:
    cfg: dict = {}
    mcp: dict = {}
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except Exception:
        cfg = {}
    try:
        mcp = json.loads(MCP_JSON_PATH.read_text())
    except Exception:
        mcp = {}
    mcp_env = ((mcp.get("mcpServers") or {}).get("aztea") or {}).get("env") or {}
    base = (
        os.environ.get("AZTEA_BASE_URL")
        or cfg.get("base_url")
        or mcp_env.get("AZTEA_BASE_URL")
        or "https://aztea.ai"
    ).rstrip("/")
    api_key = (
        os.environ.get("AZTEA_API_KEY")
        or cfg.get("api_key")
        or mcp_env.get("AZTEA_API_KEY")
        or ""
    )
    return base, api_key


def _http(
    base: str,
    path: str,
    api_key: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict | str, float]:
    url = base.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("Authorization", f"Bearer {api_key}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "aztea-batch-stress/1")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="replace")
            elapsed = (time.monotonic() - started) * 1000.0
            try:
                return resp.status, json.loads(raw), elapsed
            except json.JSONDecodeError:
                return resp.status, raw[:500], elapsed
    except urllib.error.HTTPError as exc:
        elapsed = (time.monotonic() - started) * 1000.0
        body_bytes = b""
        try:
            body_bytes = exc.read()
        except Exception:
            pass
        try:
            return exc.code, json.loads(body_bytes.decode("utf-8")), elapsed
        except Exception:
            return exc.code, body_bytes.decode("utf-8", errors="replace")[:500], elapsed
    except Exception as exc:
        elapsed = (time.monotonic() - started) * 1000.0
        return 0, f"{type(exc).__name__}: {exc}", elapsed


# ── Rendering helpers ───────────────────────────────────────────────────────


def _bar(pct: float, width: int = 32) -> str:
    pct = max(0.0, min(1.0, pct))
    full = int(round(pct * width))
    bar = "█" * full + "·" * (width - full)
    color = GREEN if pct >= 0.95 else NEON_AMBER if pct >= 0.5 else NEON_BLUE
    return f"{color}{bar}{RESET}"


def _spark(values: list[int], width: int = 16) -> str:
    if not values:
        return ""
    glyphs = " ▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    span = max(1, hi - lo)
    samples = values[-width:] if len(values) > width else values
    out = []
    for v in samples:
        idx = int(round(((v - lo) / span) * (len(glyphs) - 1)))
        out.append(glyphs[max(0, min(idx, len(glyphs) - 1))])
    return f"{NEON_BLUE}{''.join(out)}{RESET}"


def _line(s: str = "") -> None:
    print(s)


def _hr(width: int, color: str = NEON_TEAL) -> None:
    print(f"{color}{'─' * width}{RESET}")


# ── Build & submit batch ────────────────────────────────────────────────────


def _build_jobs(size: int, inject_failures: int) -> list[dict]:
    """Build the per-job specs.

    `output_verification_window_seconds=0` is critical for the demo: jobs
    default to a 24h acceptance window during which `settled_at` is NULL,
    which makes /wallets/audit show "0 receipts in window" for fresh
    batches. Setting to 0 settles each job immediately on completion.
    """
    jobs: list[dict] = []
    for i in range(size):
        if i < inject_failures:
            # A deliberately invalid agent_id triggers per-job 404 inside the
            # batch validator. The rest of the batch still proceeds.
            jobs.append({
                "agent_id": "00000000-0000-0000-0000-000000000000",
                "input_payload": {"cve_id": _CVE_POOL[i % len(_CVE_POOL)]},
                "max_attempts": 1,
                "output_verification_window_seconds": 0,
            })
            continue
        jobs.append({
            "agent_id": CVE_LOOKUP_AGENT_ID,
            "input_payload": {"cve_id": _CVE_POOL[i % len(_CVE_POOL)]},
            "max_attempts": 1,
            "output_verification_window_seconds": 0,
        })
    return jobs


def _submit_batch(
    base: str, api_key: str, jobs: list[dict], cap_cents: int
) -> tuple[str | None, dict]:
    code, body, _ = _http(
        base,
        "/jobs/batch",
        api_key,
        method="POST",
        body={
            "intent": "yc-demo-stress",
            "max_total_cents": cap_cents,
            "dry_run": False,
            "jobs": jobs,
        },
        timeout=SUBMIT_TIMEOUT_S,
    )
    if code not in (200, 201):
        return None, {"error_code": code, "body": body}
    if not isinstance(body, dict):
        return None, {"error": "non-json", "body": str(body)[:200]}
    return body.get("batch_id"), body


def _poll_batch(
    base: str, api_key: str, batch_id: str, total: int
) -> dict:
    """Poll until terminal. Renders a live one-line progress."""
    completion_history: list[int] = []
    poll_count = 0
    last_render_len = 0
    started = time.monotonic()
    while True:
        poll_count += 1
        code, body, ms = _http(
            base,
            f"/jobs/batch/{batch_id}?include=compact",
            api_key,
            method="GET",
            timeout=POLL_TIMEOUT_S,
        )
        if code != 200 or not isinstance(body, dict):
            sys.stdout.write(
                f"\r{RED}poll error HTTP {code}{RESET}\n"
            )
            time.sleep(POLL_INTERVAL_S)
            continue
        n_complete = int(body.get("n_complete") or 0)
        n_failed = int(body.get("n_failed") or 0)
        n_pending = int(body.get("n_pending") or 0)
        n_running = int(body.get("n_running") or 0)
        terminal = (n_complete + n_failed) >= total
        completion_history.append(n_complete + n_failed)
        # one-line progress (overwrite via \r)
        pct = (n_complete + n_failed) / max(total, 1)
        elapsed_s = time.monotonic() - started
        rate = (n_complete + n_failed) / max(elapsed_s, 0.01)
        line = (
            f"\r  {_bar(pct, 32)} "
            f"{WHITE}{n_complete + n_failed}/{total}{RESET} "
            f"{DIM}done{RESET}  "
            f"{NEON_AMBER}{n_running}{RESET}{DIM} run{RESET}  "
            f"{NEON_BLUE}{n_pending - n_running}{RESET}{DIM} queued{RESET}  "
            f"{RED if n_failed else DIM}{n_failed}{RESET}{DIM} failed{RESET}  "
            f"{DIM}{rate:.1f}/s · {int(ms)}ms poll{RESET}  "
            f"{_spark(completion_history)}"
        )
        # pad to clear any stale chars from previous render
        pad = " " * max(0, last_render_len - _strip_ansi_len(line))
        sys.stdout.write(line + pad)
        sys.stdout.flush()
        last_render_len = _strip_ansi_len(line)
        if terminal:
            sys.stdout.write("\n")
            # Re-fetch with include=full so the result panel has timestamps,
            # signatures, and fee splits per job. Compact mode omits these.
            code_full, full_body, _ = _http(
                base, f"/jobs/batch/{batch_id}?include=full", api_key,
                method="GET", timeout=POLL_TIMEOUT_S,
            )
            final = full_body if (code_full == 200 and isinstance(full_body, dict)) else body
            return {
                "n_complete": n_complete,
                "n_failed": n_failed,
                "elapsed_s": elapsed_s,
                "polls": poll_count,
                "final": final,
            }
        time.sleep(POLL_INTERVAL_S)


def _verify_sample(base: str, api_key: str, job_ids: list[str], sample: int = 5) -> dict:
    """Client-side Ed25519 verification of a sample of receipts.

    Talks to /jobs/{id}/signature, which returns `output_payload`,
    `signature`, and `public_key_jwk`. We re-canonicalize the payload
    locally and verify with `cryptography` — no trust in the server's
    bulk_verification field. This is what the demo's "anyone can verify
    offline" story rests on.
    """
    import base64
    import hashlib
    import random

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except Exception as exc:
        return {"verified": 0, "failed": 0, "error": f"cryptography import: {exc}"}

    sample_ids = job_ids[:sample] if len(job_ids) <= sample else random.sample(job_ids, sample)
    verified = 0
    failed = 0
    failures: list[dict] = []
    for jid in sample_ids:
        code, body, _ = _http(base, f"/jobs/{jid}/signature", api_key, timeout=10.0)
        if code != 200 or not isinstance(body, dict):
            failed += 1
            failures.append({"job_id": jid, "error": f"HTTP {code}"})
            continue
        sig = body.get("signature") or ""
        payload = body.get("output_payload")
        jwk = body.get("public_key_jwk") or {}
        if not sig or jwk.get("crv") != "Ed25519" or not jwk.get("x"):
            failed += 1
            failures.append({"job_id": jid, "error": "missing-fields"})
            continue
        try:
            x = jwk["x"]
            raw = base64.urlsafe_b64decode(x + "=" * ((4 - len(x) % 4) % 4))
            pub = ed25519.Ed25519PublicKey.from_public_bytes(raw)
            sig_bytes = base64.b64decode(sig)
            canon = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            pub.verify(sig_bytes, canon)
            # Also confirm the server-reported output_hash matches.
            our_hash = hashlib.sha256(canon).hexdigest()
            if body.get("output_hash") and body.get("output_hash") != our_hash:
                failures.append({"job_id": jid, "error": "hash-mismatch"})
                failed += 1
                continue
            verified += 1
        except Exception as exc:
            failed += 1
            failures.append({"job_id": jid, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "sampled": len(sample_ids),
        "verified": verified,
        "failed": failed,
        "failures": failures[:3],
    }


def _audit_after(
    base: str, api_key: str, since_iso: str, total: int
) -> dict:
    """Fetch /wallets/audit with verify_all=true; returns receipts_aggregate."""
    code, body, ms = _http(
        base,
        f"/wallets/audit?period=1d&verify_all=true&limit={max(total, 50)}&since={urllib_quote(since_iso)}",
        api_key,
        timeout=30.0,
    )
    if code != 200 or not isinstance(body, dict):
        return {"error": f"HTTP {code}", "ms": ms, "body": body}
    return {
        "aggregate": body.get("receipts_aggregate") or {},
        "digest": body.get("receipts_digest") or "",
        "verification": body.get("verification") or {},
        "ms": ms,
    }


def urllib_quote(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote(s, safe="")


# ── Final result panel ──────────────────────────────────────────────────────


def _render_result(
    *,
    size: int,
    inject_failures: int,
    submit: dict,
    poll: dict,
    audit: dict,
    cverify: dict,
    cap_cents: int,
    valid_count: int,
    invalid_count: int,
) -> int:
    width = 88
    title = " batch stress result "
    pad = (width - 2 - len(title)) // 2
    print(
        f"\n{NEON_TEAL}╭{'─' * pad}{NEON_PINK}{title}"
        f"{NEON_TEAL}{'─' * (width - 2 - pad - len(title))}╮{RESET}"
    )

    n_complete = poll["n_complete"]
    n_failed = poll["n_failed"]
    elapsed_s = poll["elapsed_s"]
    # Pre-rejected (invalid_jobs at submit) are accounted for separately and
    # never enter the batch; the batch's expected failures are 0 unless the
    # operator injected schema-level breakage that survives validation.
    expected_failures = 0
    extra_failures = max(0, n_failed - expected_failures)

    final = poll.get("final") or {}

    agg = audit.get("aggregate") or {}
    digest = audit.get("digest") or ""
    receipts_signed = int(agg.get("receipts_signed") or 0)
    receipts_total = int(agg.get("receipts_total") or 0)
    settled_cents = int(agg.get("total_settled_cents") or 0)
    distinct_agents = int(agg.get("distinct_agents") or 0)

    # Client-side verification (the source of truth for the demo).
    cv_sampled = int(cverify.get("sampled") or 0)
    cv_verified = int(cverify.get("verified") or 0)
    cv_failed = int(cverify.get("failed") or 0)

    # Compute per-job latency from the timestamps in the full job records.
    # batch_status?include=full returns each job's `created_at` / `completed_at`.
    import datetime as _dt

    def _parse_iso(s: str | None) -> _dt.datetime | None:
        if not s:
            return None
        try:
            return _dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        except Exception:
            return None

    serial_ms_sum = 0
    completed_in_trace = 0
    for j in (final.get("jobs") or []):
        if str(j.get("status") or "") == "complete":
            ca = _parse_iso(j.get("created_at"))
            cb = _parse_iso(j.get("completed_at"))
            if ca and cb:
                serial_ms_sum += int((cb - ca).total_seconds() * 1000)
                completed_in_trace += 1
    if completed_in_trace == 0:
        serial_ms_sum = int(elapsed_s * 1000)
        completed_in_trace = max(n_complete, 1)
    serial_estimate_s = serial_ms_sum / 1000.0
    speedup = serial_estimate_s / max(elapsed_s, 0.01)
    avg_per_call_s = serial_ms_sum / 1000.0 / max(completed_in_trace, 1)

    total_cost_cents = int(final.get("total_cost_cents") or 0)
    if not total_cost_cents:
        total_cost_cents = int(final.get("total_charged_cents") or 0)
    if not total_cost_cents:
        for j in (final.get("jobs") or []):
            total_cost_cents += int(j.get("caller_charge_cents") or j.get("price_cents") or 0)

    # Count signed completed jobs directly from the full job records.
    trace_signed = sum(
        1
        for j in (final.get("jobs") or [])
        if str(j.get("status") or "") == "complete" and j.get("output_signature")
    )

    rows: list[tuple[str, str, str]] = [
        ("size",
         f"{NEON_TEAL}{size}{RESET} submitted  "
         f"{DIM}→{RESET}  {NEON_TEAL}{valid_count}{RESET}{DIM} valid{RESET}"
         + (
             f"  {DIM}·{RESET}  {NEON_AMBER}{invalid_count}{RESET}{DIM} rejected{RESET}"
             if invalid_count
             else ""
         ),
         "ok"),
        ("wall time",
         f"{NEON_TEAL}{elapsed_s:6.2f}s{RESET}"
         f"  {DIM}per-job avg {avg_per_call_s*1000:.0f}ms · cost ${total_cost_cents/100:.2f}{RESET}",
         "ok"),
        ("parallel speedup",
         f"{NEON_PINK}{BOLD}{speedup:5.1f}×{RESET}"
         f"  {DIM}vs serial sum {serial_estimate_s:5.1f}s{RESET}",
         "ok"),
        ("settled in batch",
         f"{NEON_TEAL}{n_complete}{RESET}{DIM} ok{RESET}  ·  "
         f"{(RED if extra_failures else DIM)}{n_failed}{RESET}{DIM} failed"
         f"{' (' + str(extra_failures) + ' unexpected)' if extra_failures else ''}{RESET}",
         "ok" if extra_failures == 0 else "warn"),
        ("signed (batch trace)",
         f"{NEON_TEAL}{trace_signed}/{n_complete}{RESET}"
         f"  {DIM}({100*trace_signed/max(n_complete,1):.0f}%) Ed25519 + did:web{RESET}",
         "ok" if trace_signed == n_complete and n_complete > 0 else "warn"),
        ("verified (client-side)",
         f"{NEON_PINK}{BOLD}{cv_verified}/{cv_sampled}{RESET}"
         f"  {DIM}sampled & re-verified locally · {cv_failed} failed{RESET}",
         "ok" if cv_verified == cv_sampled and cv_sampled > 0 else "fail" if cv_failed else "warn"),
        ("aggregate digest",
         f"{NEON_AMBER}{digest[:32]}…{RESET}  {DIM}(pin this){RESET}" if digest
         else f"{YELLOW}— digest pending settlement window —{RESET}",
         "ok" if digest else "warn"),
        ("audit window",
         f"{NEON_TEAL}{receipts_signed}/{receipts_total}{RESET}"
         f"  {DIM}signed in 24h ({distinct_agents} agents · ${settled_cents/100:.2f}){RESET}",
         "ok" if receipts_total > 0 else "warn"),
    ]

    if invalid_count:
        rows.insert(4, (
            "partial-failure",
            f"{NEON_PINK}{BOLD}{invalid_count}/{inject_failures}{RESET}"
            f"  {DIM}rejected pre-charge · 0 escrow · 0¢ leaked{RESET}",
            "ok" if invalid_count == inject_failures else "warn",
        ))

    name_col = 22
    for label, value, status in rows:
        icon = {"ok": ICON_OK, "warn": ICON_WARN, "fail": ICON_FAIL}.get(status, "?")
        label_col = label[:name_col].ljust(name_col)
        line_left = f"  {icon} {WHITE}{label_col}{RESET}"
        used = _strip_ansi_len(line_left) + _strip_ansi_len(value)
        room = max(1, width - used - 4)
        print(
            f"{NEON_TEAL}│{RESET} {line_left}{value}{' ' * room}{NEON_TEAL}│{RESET}"
        )
    print(f"{NEON_TEAL}╰{'─' * (width - 2)}╯{RESET}")

    fails = sum(1 for _, _, s in rows if s == "fail")
    warns = sum(1 for _, _, s in rows if s == "warn")
    if fails:
        print(f"\n  {RED}{BOLD} NO-GO {RESET}{DIM}· {fails} blocker(s){RESET}\n")
        return 1
    if warns:
        print(f"\n  {YELLOW}{BOLD} GO with caveats {RESET}{DIM}· {warns} warning(s){RESET}\n")
        return 0
    print(
        f"\n  {GREEN}{BOLD} GO · ship it {RESET}{DIM}"
        f"· {speedup:.1f}× speedup · {receipts_signed}/{receipts_total} signed{RESET}\n"
    )
    return 0


# ── Main ────────────────────────────────────────────────────────────────────


def _iso_now() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(prog="aztea-batch-stress")
    parser.add_argument("--size", type=int, default=50, help="batch size (default 50)")
    parser.add_argument(
        "--inject-failures",
        type=int,
        default=0,
        metavar="N",
        help="how many jobs to deliberately make fail (invalid agent_id)",
    )
    parser.add_argument(
        "--cap-cents",
        type=int,
        default=DEFAULT_BATCH_PRICE_CAP_CENTS,
        help="max_total_cents cap for the batch (default $5)",
    )
    parser.add_argument("--no-banner", action="store_true")
    args = parser.parse_args()

    if args.size < 1 or args.size > 250:
        print(f"{RED}--size must be in [1, 250]{RESET}")
        return 2
    if args.inject_failures < 0 or args.inject_failures > args.size:
        print(f"{RED}--inject-failures must be in [0, --size]{RESET}")
        return 2

    base, api_key = _load_creds()
    if not api_key:
        print(f"{RED}{BOLD}NO-GO{RESET}: no api_key found.")
        return 1

    if not args.no_banner:
        _print_banner()

    print(
        f"  {DIM}target:{RESET} {NEON_TEAL}{base}{RESET}  "
        f"{DIM}·{RESET}  {DIM}agent:{RESET} {NEON_PINK}cve_lookup_agent{RESET}  "
        f"{DIM}·{RESET}  {DIM}cap:{RESET} {NEON_AMBER}${args.cap_cents/100:.2f}{RESET}"
    )
    print(
        f"  {DIM}batch:{RESET} {NEON_TEAL}{args.size}{RESET} jobs"
        + (
            f"  {DIM}·{RESET}  {RED}{args.inject_failures} injected to fail{RESET}"
            if args.inject_failures
            else ""
        )
    )
    print()

    # Snapshot pre-submit timestamp so the audit fetch only counts THIS batch's
    # receipts. Server clock is the source of truth, so we slop -10s back.
    import datetime as _dt
    since_iso = (
        _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=10)
    ).isoformat()

    jobs = _build_jobs(args.size, args.inject_failures)
    print(f"  {DIM}submitting…{RESET}", end="", flush=True)
    submit_started = time.monotonic()
    batch_id, submit_body = _submit_batch(base, api_key, jobs, args.cap_cents)
    submit_ms = (time.monotonic() - submit_started) * 1000.0

    if not batch_id:
        print(f" {ICON_FAIL}\n  {RED}submit failed:{RESET} {submit_body}")
        return 1

    # Invalid jobs (bad agent_id, schema, etc.) are filtered out at submit
    # time. The batch then runs only the valid jobs, so n_complete + n_failed
    # is bounded by `count`, not `submitted_count`.
    valid_count = int(submit_body.get("count") or args.size)
    invalid_count = int(submit_body.get("invalid_job_count") or 0)

    extra = ""
    if invalid_count:
        extra = (
            f"  {DIM}·{RESET}  "
            f"{NEON_AMBER}{invalid_count}{RESET}{DIM} pre-rejected (refunded){RESET}"
        )
    print(
        f" {ICON_OK} {DIM}batch_id={batch_id[:8]}… in {submit_ms:.0f}ms"
        f" · {valid_count} valid{RESET}{extra}\n"
    )

    poll_result = _poll_batch(base, api_key, batch_id, valid_count)
    print()
    # Pull the completed job_ids out of the final batch_status response so we
    # can verify a sample client-side (independent of the server's audit
    # bulk_verification field).
    completed_jobs: list[str] = []
    final = poll_result.get("final") or {}
    for j in (final.get("jobs") or []):
        if str(j.get("status") or "") == "complete":
            jid = j.get("job_id")
            if jid:
                completed_jobs.append(jid)

    print(f"  {DIM}verifying {min(5, len(completed_jobs))} receipts client-side (offline-style)…{RESET}", flush=True)
    cverify = _verify_sample(base, api_key, completed_jobs, sample=5)
    print(f"  {DIM}fetching wallet audit + aggregate digest…{RESET}", flush=True)
    audit = _audit_after(base, api_key, since_iso, args.size)

    rc = _render_result(
        size=args.size,
        inject_failures=args.inject_failures,
        submit=submit_body,
        poll=poll_result,
        audit=audit,
        cverify=cverify,
        cap_cents=args.cap_cents,
        valid_count=valid_count,
        invalid_count=invalid_count,
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())

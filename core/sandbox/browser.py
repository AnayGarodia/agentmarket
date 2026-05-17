"""Sandbox-scoped headless Chromium sessions.

# OWNS: sandbox_browser_session + sandbox_browser_navigate +
#       sandbox_browser_screenshot + sandbox_browser_console_logs.
#       The session pool is module-local; each session belongs to one
#       sandbox_id with its own cookie jar (isolated from other sessions).
# NOT OWNS: click/fill/eval/a11y/axe/lighthouse/record/replay — these
#           remain stubs that share the same Playwright pool follow-up
#           issue, so filling them is incremental (no infra change needed).
# INVARIANTS:
#   * Playwright is imported lazily on first use so test fixtures and
#     workers without chromium installed still import the module cleanly.
#   * Every session has a per-sandbox cookie + storage state directory.
#   * Sessions are evicted when their parent sandbox is stopped.
"""

from __future__ import annotations

import base64
import logging
import secrets
import threading
from typing import Any

from core.sandbox.models import SandboxInvalidInput
from core.sandbox.state import SandboxState, get, sandbox_dir

_LOG = logging.getLogger("aztea.sandbox.browser")
_NAV_TIMEOUT_MS = 15_000
_DEFAULT_VIEWPORT = {"width": 1280, "height": 720}
_MAX_SESSIONS_PER_SANDBOX = 4
_CONSOLE_LOG_LIMIT = 200


class _SessionEntry:
    """Holds the per-session Playwright resources and event buffers."""

    def __init__(self, session_id: str, sandbox_id: str) -> None:
        self.session_id = session_id
        self.sandbox_id = sandbox_id
        self.browser: Any = None
        self.context: Any = None
        self.page: Any = None
        self.console_logs: list[dict[str, Any]] = []
        # Network request log capped to avoid memory blowup on long-running
        # sessions. Append-only via a Playwright "request" listener; the
        # network() action snapshots-and-clears.
        self.network_log: list[dict[str, Any]] = []
        # Recording buffer for record/replay. List of {selector, action,
        # value, at_ms} events captured during sandbox_browser_record;
        # replayed back in order by sandbox_browser_replay.
        self.recordings: list[dict[str, Any]] = []
        self.recording_active: bool = False


_SESSIONS: dict[str, _SessionEntry] = {}
_SESSIONS_LOCK = threading.RLock()


def session_open(payload: dict[str, Any]) -> dict[str, Any]:
    """Start a new headless Chromium session bound to this sandbox.

    Returns ``{session_id, cdp_url}``. The session lives until the sandbox
    stops or :func:`session_close` is called explicitly.
    """
    state = _require(payload)
    sessions_for_sandbox = [
        entry for entry in _SESSIONS.values() if entry.sandbox_id == state.sandbox_id
    ]
    if len(sessions_for_sandbox) >= _MAX_SESSIONS_PER_SANDBOX:
        raise SandboxInvalidInput(
            f"sandbox '{state.sandbox_id}' already has "
            f"{_MAX_SESSIONS_PER_SANDBOX} open browser sessions; close one "
            "or stop the sandbox before starting another"
        )
    viewport = dict(_DEFAULT_VIEWPORT)
    viewport.update(payload.get("viewport") or {})
    storage_dir = sandbox_dir(state.sandbox_id) / "browser" / "sessions"
    storage_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    session_id = f"sess_{secrets.token_hex(6)}"
    sync_playwright = _import_playwright()
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=bool(payload.get("headless", True)))
        context = browser.new_context(
            viewport={"width": int(viewport["width"]), "height": int(viewport["height"])},
            storage_state=None,
        )
        page = context.new_page()
    except Exception:
        pw.stop()
        raise
    entry = _SessionEntry(session_id, state.sandbox_id)
    entry.browser = browser
    entry.context = context
    entry.page = page
    entry._playwright = pw  # type: ignore[attr-defined]
    _attach_console_listener(entry)
    _attach_network_listener(entry)
    with _SESSIONS_LOCK:
        _SESSIONS[session_id] = entry
    state.touch()
    return {
        "sandbox_id": state.sandbox_id,
        "session_id": session_id,
        "viewport": viewport,
        "storage_state_path": str(storage_dir / f"{session_id}.json"),
        "cdp_url": None,  # CDP exposure is the follow-up issue; not in this slice.
    }


def session_close(payload: dict[str, Any]) -> dict[str, Any]:
    state = _require(payload)
    session_id = _resolve_session_id(payload)
    entry = _SESSIONS.get(session_id)
    if entry is None or entry.sandbox_id != state.sandbox_id:
        raise SandboxInvalidInput(
            f"session '{session_id}' not found for sandbox '{state.sandbox_id}'"
        )
    _teardown_entry(entry)
    with _SESSIONS_LOCK:
        _SESSIONS.pop(session_id, None)
    return {"sandbox_id": state.sandbox_id, "session_id": session_id, "closed": True}


def navigate(payload: dict[str, Any]) -> dict[str, Any]:
    state = _require(payload)
    entry = _require_session(state, payload)
    url = str(payload.get("url") or "").strip()
    if not url:
        raise SandboxInvalidInput("url is required for sandbox_browser_navigate")
    wait_until = str(payload.get("wait_until") or "load").strip().lower()
    if wait_until not in {"load", "domcontentloaded", "networkidle", "commit"}:
        wait_until = "load"
    response = entry.page.goto(
        url, wait_until=wait_until, timeout=_NAV_TIMEOUT_MS,
    )
    _record_event_if_active(entry, {"action": "navigate", "url": url})
    state.touch()
    return {
        "sandbox_id": state.sandbox_id,
        "session_id": entry.session_id,
        "url": entry.page.url,
        "title": entry.page.title(),
        "status": getattr(response, "status", None) if response else None,
    }


def screenshot(payload: dict[str, Any]) -> dict[str, Any]:
    state = _require(payload)
    entry = _require_session(state, payload)
    full_page = bool(payload.get("full_page", True))
    png_bytes = entry.page.screenshot(full_page=full_page)
    state.touch()
    return {
        "sandbox_id": state.sandbox_id,
        "session_id": entry.session_id,
        "mime": "image/png",
        "size_bytes": len(png_bytes),
        "screenshot_b64": base64.b64encode(png_bytes).decode("ascii"),
        "full_page": full_page,
    }


def console_logs(payload: dict[str, Any]) -> dict[str, Any]:
    state = _require(payload)
    entry = _require_session(state, payload)
    clear = bool(payload.get("clear", False))
    out = list(entry.console_logs)
    if clear:
        entry.console_logs.clear()
    state.touch()
    return {
        "sandbox_id": state.sandbox_id,
        "session_id": entry.session_id,
        "logs": out,
        "cleared": clear,
    }


_CLICK_TIMEOUT_MS = 10_000
_TYPE_TIMEOUT_MS = 10_000
_EVAL_TIMEOUT_MS = 10_000
_NETWORK_LOG_LIMIT = 500
_AXE_SCRIPT_TIMEOUT_S = 30
_LIGHTHOUSE_TIMEOUT_S = 120


def click(payload: dict[str, Any]) -> dict[str, Any]:
    """Click a CSS selector inside the session's current page.

    Why: pre-fix this was a stub. Now wires Playwright's ``page.click``
    which auto-waits for the element + scrolls into view. The dispatch
    site already validated sandbox_id + session_id; this just adds
    selector + an optional ``button`` (left/middle/right).
    """
    state = _require(payload)
    entry = _require_session(state, payload)
    selector = _require_selector(payload)
    button = _validate_button(payload.get("button"))
    entry.page.click(
        selector,
        button=button,
        timeout=_CLICK_TIMEOUT_MS,
        click_count=int(payload.get("click_count") or 1),
    )
    _record_event_if_active(entry, {
        "action": "click", "selector": selector, "button": button,
    })
    state.touch()
    return {
        "sandbox_id": state.sandbox_id,
        "session_id": entry.session_id,
        "selector": selector,
        "button": button,
        "clicked": True,
    }


def fill(payload: dict[str, Any]) -> dict[str, Any]:
    """Type ``value`` into the input matched by ``selector``.

    Uses Playwright's ``page.fill`` which clears the field first.
    """
    state = _require(payload)
    entry = _require_session(state, payload)
    selector = _require_selector(payload)
    value = payload.get("value")
    if not isinstance(value, str):
        raise SandboxInvalidInput("value must be a string for sandbox_browser_fill")
    entry.page.fill(selector, value, timeout=_TYPE_TIMEOUT_MS)
    _record_event_if_active(entry, {
        "action": "fill", "selector": selector, "value": value,
    })
    state.touch()
    return {
        "sandbox_id": state.sandbox_id,
        "session_id": entry.session_id,
        "selector": selector,
        "filled": True,
        "value_length": len(value),
    }


def eval_js(payload: dict[str, Any]) -> dict[str, Any]:
    """Evaluate JS in the page and return the JSON-serialisable result.

    Why: keeps the agent's hand on the wheel for cases the typed verbs
    don't cover yet (a one-off DOM probe, a state-mutating script).
    Result is bounded by Playwright's serialisation — non-JSON-friendly
    return values are rejected at the boundary.
    """
    state = _require(payload)
    entry = _require_session(state, payload)
    js = str(payload.get("js") or "").strip()
    if not js:
        raise SandboxInvalidInput("js is required for sandbox_browser_eval")
    if len(js) > 16_384:
        raise SandboxInvalidInput("js exceeds 16 KB cap")
    try:
        result = entry.page.evaluate(js)
    except Exception as exc:  # noqa: BLE001 — playwright exception surface varies
        return {
            "sandbox_id": state.sandbox_id,
            "session_id": entry.session_id,
            "ok": False,
            "error": str(exc)[:512],
        }
    state.touch()
    return {
        "sandbox_id": state.sandbox_id,
        "session_id": entry.session_id,
        "ok": True,
        "result": _bound_eval_result(result),
    }


def network(payload: dict[str, Any]) -> dict[str, Any]:
    """Return captured network requests for the session, with optional clear.

    The network listener is attached at session_open; this action just
    snapshots the buffer (and optionally drains it). For long-running
    sessions, callers should drain regularly to keep memory bounded.
    """
    state = _require(payload)
    entry = _require_session(state, payload)
    clear = bool(payload.get("clear", False))
    requests = list(entry.network_log)
    if clear:
        entry.network_log.clear()
    state.touch()
    return {
        "sandbox_id": state.sandbox_id,
        "session_id": entry.session_id,
        "requests": requests,
        "count": len(requests),
        "cleared": clear,
        "truncated": len(requests) >= _NETWORK_LOG_LIMIT,
    }


def a11y_tree(payload: dict[str, Any]) -> dict[str, Any]:
    """Return Playwright's accessibility tree for the page (or a subtree).

    Why: cheap, structured-data version of a screenshot — covers the
    "is the right control labeled?" use case without an axe run.
    """
    state = _require(payload)
    entry = _require_session(state, payload)
    interesting_only = bool(payload.get("interesting_only", True))
    try:
        tree = entry.page.accessibility.snapshot(interesting_only=interesting_only)
    except Exception as exc:  # noqa: BLE001
        raise SandboxInvalidInput(f"a11y snapshot failed: {exc}") from exc
    state.touch()
    return {
        "sandbox_id": state.sandbox_id,
        "session_id": entry.session_id,
        "tree": tree,
        "interesting_only": interesting_only,
    }


def axe_audit(payload: dict[str, Any]) -> dict[str, Any]:
    """Run axe-core in the current page and return its violation report.

    Why: re-uses the existing axe-core dependency by injecting the
    library script and calling ``axe.run`` in the page. Stays inside
    the existing browser session so the report reflects the actual
    page state (including post-click DOM mutations) — not a fresh
    headless render.
    """
    state = _require(payload)
    entry = _require_session(state, payload)
    axe_script = _load_axe_script()
    entry.page.add_script_tag(content=axe_script)
    raw = entry.page.evaluate(
        "async () => { const r = await axe.run(document); "
        "return {violations: r.violations, passes_count: r.passes.length, "
        "incomplete_count: r.incomplete.length}; }",
    )
    state.touch()
    violations = raw.get("violations") if isinstance(raw, dict) else []
    return {
        "sandbox_id": state.sandbox_id,
        "session_id": entry.session_id,
        "violations": violations,
        "violation_count": len(violations or []),
        "passes_count": (raw or {}).get("passes_count"),
        "incomplete_count": (raw or {}).get("incomplete_count"),
    }


def lighthouse(payload: dict[str, Any]) -> dict[str, Any]:
    """Run Lighthouse against the current page URL via the lighthouse CLI.

    Why: Lighthouse needs its own chromium instance for a clean profile,
    so the simplest correct path is to shell out to the CLI against the
    page's URL. Returns the categories rollup (perf / a11y / best-practices
    / SEO / pwa) and the top opportunities/diagnostics.
    """
    import json
    import os
    import shutil
    import subprocess
    import tempfile

    state = _require(payload)
    entry = _require_session(state, payload)
    url = entry.page.url
    if not url or url == "about:blank":
        raise SandboxInvalidInput(
            "navigate to a URL before running sandbox_browser_lighthouse"
        )
    lighthouse_bin = shutil.which("lighthouse")
    if lighthouse_bin is None:
        raise SandboxInvalidInput(
            "lighthouse CLI is not installed. Install with: "
            "npm install -g lighthouse"
        )
    categories = list(payload.get("categories") or [
        "performance",
        "accessibility",
        "best-practices",
        "seo",
    ])
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as out:
        try:
            cat_args: list[str] = []
            for c in categories:
                cat_args.extend(["--only-categories", str(c)])
            proc = subprocess.run(  # noqa: S603
                [
                    lighthouse_bin,
                    url,
                    "--output=json",
                    f"--output-path={out.name}",
                    "--quiet",
                    "--chrome-flags=--headless=new --no-sandbox",
                    *cat_args,
                ],
                capture_output=True,
                text=True,
                timeout=_LIGHTHOUSE_TIMEOUT_S,
            )
            if proc.returncode != 0:
                raise SandboxInvalidInput(
                    f"lighthouse exit {proc.returncode}: "
                    f"{(proc.stderr or '')[:256]}"
                )
            with open(out.name, encoding="utf-8") as f:
                report = json.load(f)
        finally:
            try:
                os.unlink(out.name)
            except OSError:
                pass
    state.touch()
    return _shape_lighthouse_report(state, entry, url, report)


def record_start(payload: dict[str, Any]) -> dict[str, Any]:
    """Begin recording high-level user actions in this session.

    Recording is opt-in per session; subsequent calls to click/fill/etc.
    will append into the session's ``recordings`` buffer. ``replay``
    plays them back in order against the current page state.
    """
    state = _require(payload)
    entry = _require_session(state, payload)
    if entry.recording_active:
        raise SandboxInvalidInput("recording already active for this session")
    entry.recordings.clear()
    entry.recording_active = True
    state.touch()
    return {
        "sandbox_id": state.sandbox_id,
        "session_id": entry.session_id,
        "recording": True,
    }


def replay(payload: dict[str, Any]) -> dict[str, Any]:
    """Replay the buffered recording in order against the current page.

    Why: the agent's typical "did my fix hold?" pattern is "do the
    sequence again, screenshot the result." Storing the sequence as
    a typed list keeps it deterministic; we don't re-record mouse
    coordinates or screenshots — only the intent-level events.
    """
    state = _require(payload)
    entry = _require_session(state, payload)
    if not entry.recordings:
        raise SandboxInvalidInput("no recorded events to replay; call sandbox_browser_record first")
    entry.recording_active = False  # don't re-capture during replay
    played: list[dict[str, Any]] = []
    for event in entry.recordings:
        kind = event.get("action")
        try:
            if kind == "click":
                entry.page.click(event["selector"], timeout=_CLICK_TIMEOUT_MS)
            elif kind == "fill":
                entry.page.fill(event["selector"], event.get("value") or "", timeout=_TYPE_TIMEOUT_MS)
            elif kind == "navigate":
                entry.page.goto(event["url"], timeout=_NAV_TIMEOUT_MS)
            elif kind == "eval":
                entry.page.evaluate(event["js"])
            else:
                played.append({**event, "skipped": True, "reason": "unknown_action"})
                continue
            played.append({**event, "replayed": True})
        except Exception as exc:  # noqa: BLE001
            played.append({**event, "replayed": False, "error": str(exc)[:256]})
            break
    state.touch()
    return {
        "sandbox_id": state.sandbox_id,
        "session_id": entry.session_id,
        "replayed_count": sum(1 for e in played if e.get("replayed")),
        "events": played,
    }


def _shape_lighthouse_report(
    state: SandboxState,
    entry: _SessionEntry,
    url: str,
    report: dict[str, Any],
) -> dict[str, Any]:
    """Pure: trim Lighthouse's huge report into a focused agent-friendly view."""
    cats = (report.get("categories") or {})
    scores = {
        name: int(round((meta.get("score") or 0) * 100))
        for name, meta in cats.items()
        if isinstance(meta, dict)
    }
    audits = report.get("audits") or {}
    opportunities = []
    diagnostics = []
    for audit_id, audit in audits.items():
        if not isinstance(audit, dict):
            continue
        details = audit.get("details") or {}
        kind = details.get("type") if isinstance(details, dict) else None
        score = audit.get("score")
        if score is None or score >= 0.9:
            continue
        entry_summary = {
            "id": audit_id,
            "title": audit.get("title"),
            "score": score,
            "display_value": audit.get("displayValue"),
        }
        if kind == "opportunity":
            opportunities.append(entry_summary)
        else:
            diagnostics.append(entry_summary)
    opportunities.sort(key=lambda x: x.get("score") or 0)
    diagnostics.sort(key=lambda x: x.get("score") or 0)
    return {
        "sandbox_id": state.sandbox_id,
        "session_id": entry.session_id,
        "url": url,
        "scores": scores,
        "opportunities": opportunities[:10],
        "diagnostics": diagnostics[:10],
    }


def _require_selector(payload: dict[str, Any]) -> str:
    """Pure: validate the ``selector`` arg shared by click/fill actions."""
    selector = str(payload.get("selector") or "").strip()
    if not selector:
        raise SandboxInvalidInput("selector is required")
    if len(selector) > 1024:
        raise SandboxInvalidInput("selector exceeds 1024 chars")
    return selector


def _validate_button(value: Any) -> str:
    """Pure: clamp ``button`` to one of left|middle|right (Playwright's enum)."""
    candidate = str(value or "left").strip().lower()
    if candidate not in ("left", "middle", "right"):
        raise SandboxInvalidInput(
            f"button must be one of left|middle|right; got {value!r}"
        )
    return candidate


def _bound_eval_result(result: Any, *, max_chars: int = 16_384) -> Any:
    """Pure: cap eval result size so a runaway script can't OOM the response."""
    import json

    try:
        encoded = json.dumps(result)
    except (TypeError, ValueError):
        # Playwright already JSON-serialised the result; non-serialisable
        # values come back as None or string-coerced — keep as-is.
        return result
    if len(encoded) <= max_chars:
        return result
    return {
        "truncated": True,
        "preview": encoded[:max_chars],
        "total_chars": len(encoded),
    }


def _load_axe_script() -> str:
    """Read the bundled axe-core JS, or fetch from the CDN as a fallback.

    Why: shipping the JS in-repo would balloon the diff; for now we rely
    on the host having either a cached copy at ``/usr/share/axe-core/axe.min.js``
    or network access to fetch the pinned CDN copy at first use.
    """
    import os
    import urllib.request

    cache_paths = (
        os.environ.get("AZTEA_AXE_CORE_JS"),
        "/usr/share/axe-core/axe.min.js",
        "/opt/axe-core/axe.min.js",
    )
    for path in cache_paths:
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return f.read()
            except OSError:
                continue
    cdn_url = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.0/axe.min.js"
    try:
        with urllib.request.urlopen(cdn_url, timeout=10) as resp:  # noqa: S310
            return resp.read().decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise SandboxInvalidInput(
            "axe-core script unavailable: install it locally at "
            "$AZTEA_AXE_CORE_JS or ensure host has network access for "
            f"the CDN fetch. underlying error: {exc}"
        ) from exc


def _record_event_if_active(entry: _SessionEntry, event: dict[str, Any]) -> None:
    """Side-effect: append the event into the session's recording buffer when active.

    Why: keeps recording lookup in one place so click/fill/etc. don't
    each repeat the active-check.
    """
    if entry.recording_active:
        entry.recordings.append(event)


def evict_for_sandbox(sandbox_id: str) -> int:
    """Stop and forget every session belonging to ``sandbox_id``.

    Why: ``lifecycle.stop`` calls this so a sandbox teardown also closes
    its browser sessions — without this Playwright would leak chromium
    children after the host containers go away.
    """
    closed = 0
    with _SESSIONS_LOCK:
        ids = [sid for sid, e in _SESSIONS.items() if e.sandbox_id == sandbox_id]
        for sid in ids:
            entry = _SESSIONS.pop(sid)
            try:
                _teardown_entry(entry)
            except Exception:
                _LOG.exception("teardown browser session %s failed", sid)
            closed += 1
    return closed


def _import_playwright():
    """Side-effect: lazy import; clear error envelope when chromium is missing."""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import]
    except ImportError as exc:
        raise SandboxInvalidInput(
            "playwright is not installed in this runtime. Install with: "
            "pip install playwright && playwright install chromium"
        ) from exc
    return sync_playwright


def _attach_network_listener(entry: _SessionEntry) -> None:
    """Side-effect: buffer network responses on the page.

    Why: ``page.on('requestfinished', ...)`` fires with the resolved
    response — we record method, URL, status, mime, and timing. Drops
    silently on overflow so the buffer can't grow without bound.
    """

    def _on_request_finished(request: Any) -> None:
        if len(entry.network_log) >= _NETWORK_LOG_LIMIT:
            return
        try:
            response = request.response()
            status = getattr(response, "status", None) if response else None
            timing = request.timing or {}
            entry.network_log.append({
                "method": getattr(request, "method", None),
                "url": getattr(request, "url", None),
                "status": status,
                "resource_type": getattr(request, "resource_type", None),
                "duration_ms": _request_duration_ms(timing),
            })
        except Exception:
            _LOG.debug("network listener serialise failed", exc_info=True)

    entry.page.on("requestfinished", _on_request_finished)


def _request_duration_ms(timing: Any) -> int | None:
    """Pure: derive total request duration from Playwright's timing dict."""
    if not isinstance(timing, dict):
        return None
    start = timing.get("startTime")
    end = timing.get("responseEnd")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
        return int(end - start)
    return None


def _attach_console_listener(entry: _SessionEntry) -> None:
    """Side-effect: register a Playwright listener that buffers console events."""

    def _on_console(msg: Any) -> None:
        if len(entry.console_logs) >= _CONSOLE_LOG_LIMIT:
            entry.console_logs.pop(0)
        try:
            entry.console_logs.append({
                "type": getattr(msg, "type", None),
                "text": getattr(msg, "text", None),
                "location": _location_dict(getattr(msg, "location", None)),
            })
        except Exception:
            _LOG.exception("console listener serialise failed")

    entry.page.on("console", _on_console)


def _location_dict(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return {k: value.get(k) for k in ("url", "lineNumber", "columnNumber")}
    return {
        "url": getattr(value, "url", None),
        "lineNumber": getattr(value, "lineNumber", None),
        "columnNumber": getattr(value, "columnNumber", None),
    }


def _teardown_entry(entry: _SessionEntry) -> None:
    """Side-effect: best-effort Playwright teardown; never raises."""
    for label, target in (
        ("page", entry.page),
        ("context", entry.context),
        ("browser", entry.browser),
        ("playwright", getattr(entry, "_playwright", None)),
    ):
        if target is None:
            continue
        try:
            if label == "playwright":
                target.stop()
            else:
                target.close()
        except Exception:
            _LOG.debug("browser teardown step %s raised", label, exc_info=True)


def _require(payload: dict[str, Any]) -> SandboxState:
    sandbox_id = str((payload or {}).get("sandbox_id") or "").strip()
    if not sandbox_id:
        raise SandboxInvalidInput("sandbox_id is required")
    state = get(sandbox_id)
    if state is None:
        raise SandboxInvalidInput(f"sandbox '{sandbox_id}' not active")
    return state


def _require_session(state: SandboxState, payload: dict[str, Any]) -> _SessionEntry:
    session_id = _resolve_session_id(payload)
    entry = _SESSIONS.get(session_id)
    if entry is None or entry.sandbox_id != state.sandbox_id:
        raise SandboxInvalidInput(
            f"session '{session_id}' not found for sandbox '{state.sandbox_id}' — "
            f"call sandbox_browser_session first"
        )
    return entry


def _resolve_session_id(payload: dict[str, Any]) -> str:
    sid = str((payload or {}).get("session_id") or "").strip()
    if not sid:
        raise SandboxInvalidInput("session_id is required")
    return sid

# Aztea — Open Work

> Source of truth for launch blockers and in-flight work. Update before ending a session.
> Operational reference: `docs/runbooks/`. Deep architecture: `CLAUDE.md`. Quick brief: `AGENTS.md`.

## Launch Blockers
<!-- Things that must ship before broader launch. Owner + target date required.
     Format: - [ ] (owner: ___, target: YYYY-MM-DD) <blocker> -->
_None at present._

## In Progress
<!-- Active work. One line per item: branch, what's left. -->
_None at present._

## Done — recent
<!-- Last 5–10 shipped items with date and commit short sha. Trim aggressively. -->
- 2026-05-17 — **Cut public SKILL.md publishing.** `aztea publish *.skill.md` now exits 2 with a clear "publish via .py handler or agent.md" hint. `_detect.py` no longer defaults ambiguous .md files to SKILL.md (raises with an actionable error instead). Wizard collapsed from 3 options to 2 (External webhook, Python handler). Server-side `/skills` POST + `/skills/validate` restricted to master callers (used for Aztea-authored composer tools). Test report 2026-05-17 confirmed prompt-only SKILL.md tools fail the brutal value test — a caller's own LLM can replicate them. Plus CLI hardening: local agent.md JSON metadata pre-validation (name/description/endpoint/price/schemas), HTTPS endpoint reachability probe (`AZTEA_SKIP_ENDPOINT_PROBE=1` for tests), structured server error envelope rendering. SDK bumped 1.7.14 → 1.7.15. (193 unit tests + 32 wizard tests + skill auth-matrix all green.)
- 2026-05-17 — Elixir realtime fan-out fully live. Caddyfile patched on prod (`/etc/caddy/Caddyfile` now uses `handle /elixir/socket*` + `uri strip_prefix /elixir` to forward `/socket/...` to Phoenix on 127.0.0.1:4000); `sudo systemctl reload caddy` applied; WebSocket handshake against `https://aztea.ai/elixir/socket/websocket?token=…` now returns 403 for bad tokens (was 404 — Phoenix is reachable). `AZTEA_ELIXIR_EVENTS=1`, `ELIXIR_INTERNAL_SHARED_SECRET` set on both services, port 4000 ok, `aztea-elixir.service` active and sweeping. Caddy reference in `docs/runbooks/deploy.md` corrected so the bug doesn't reappear on a future provision.
- 2026-05-16 — Removed three dead one-shot scripts (`scripts/split_python_by_ast.py`, `scripts/split_integration_tests.py`, `scripts/audit_repro.py`); their source-file targets no longer exist in the repo.
- 2026-05-16 — chore: remove TUI and `scripts/client_cli.py` (commit `1f209f5`); aztea 1.7.13 to PyPI (`a284f8c`); `aztea-tui` deprecated on npm, fully deleted from PyPI; `scripts/release_publish_local.sh` is the canonical release path (gitignored).
- 2026-05-15 — Migration runner race fix: `_apply_migrations_postgres` now takes a session-level advisory lock (`MIGRATION_ADVISORY_LOCK_ID = 4297493287`, 60s timeout) so two uvicorn workers can't both apply the same pending migration. Closes the 2026-05-15 deploy-of-0046 incident where one worker died on `UniqueViolation`. SQLite path unchanged.
- 2026-05-15 — Pipeline discoverability: extended `GET /recipes` with `steps[]` + `estimated_total_cost_usd` + `missing_agents[]`; new `/workflows` frontend page with Run-workflow dialog; `manage_workflow(action="list_recipes")` MCP action inherits the same shape. Recon found `/recipes` already existed at `part_014.py:1183` so this is field-extension + UI rather than new routes. (commit `8a6e4fe`)
- 2026-05-15 — Reconciliation auto-repair: `?auto_repair=true` on `/ops/payments/reconcile` rewrites below-threshold `balance_cents` + `held_cents` drift in place; above-threshold drift still surfaced for human review; new `repair_wallet_held_cache` helper + `AUTO_REPAIR_THRESHOLD_CENTS` flag ($100 default, env-overridable) (commit `b0e696d`)
- 2026-05-15 — Reserve-hold pattern for agent payouts: `wallet_holds` table + `held_cents` cache + sweeper + Stripe withdrawal enforcement + dual-counter defense-in-depth; replaces silent-skip clawback (commit `9d9776e`)
- 2026-05-15 — Per-key sliding-window rate-limit middleware: 120 RPM caller / 600 worker / 60 anon / 10 RPS burst / LRU-bounded / fail-open (commit `73e97d4`)
- 2026-05-15 — Warm copy sweep: `frontend/src/utils/errorCopy.js` + `docs/voice.md` + 12 catch-site migrations; surfaces `retry_after_seconds` on 429 and `request_id` on 5xx (commit `97efdfa`)
- 2026-05-15 — SDK exception contracts + 8 Hypothesis property tests for `make_error` envelope shape; pinned `hypothesis>=6.100` already in `requirements-dev.txt` (commit `f8676fc`)
- 2026-05-15 — Step 1 strangle-fig migration: Phoenix.PubSub + Channels for realtime job events, feature-flagged off (commit `bd58a2a`)
- 2026-05-15 — Silent-failure sweep: payout-curve counter + 3 structured error envelopes + dispute/manifest/claim-token taxonomy codes + SDK hints (commit `f139a73`)
- 2026-05-15 — Observability upgrade: `job_duration_seconds` histogram + `builtin_agent_calls_total` counter + `GET /health` returning `{status, db, llm_providers, version}` (commit `476da23`)
- 2026-05-15 — TypeScript SDK parity: `AgentServer`, `poll_job_to_completion`, clarification handling (commit `53052b4`)
- 2026-05-15 — Co-pilot mode end-to-end integration tests (6 tests covering steer/progress/stop_when full flow) (commit `ff214a6`)
- 2026-05-15 — Federated reputation blend: hosted global trust auto-merged into `compute_trust_metrics()` with evidence-weighted blend (commit `2ef464d`)
- 2026-05-15 — Removed 15 dead built-in agents and the old `sdks/python/` SDK; `SUNSET_DEPRECATED_AGENT_IDS` now empty; curated count 35 → 29 (commit `ad14af3`)
- 2026-05-15 — Doc audit: 16 files fixed, 7 dead session artifacts deleted (commit `c20657a`)

## Backlog
<!-- Known gaps, not yet scheduled. -->

### From the 2026-05-17 extensive test report (priority order)

- [ ] **Streaming pipeline broken end-to-end.** `aztea_call_streaming` against `python_code_executor` returns `terminal_state=failed` with `RECEIPT_NOT_BUILT` (HTTP 425), 12 duplicated "started" partials, and `stop_when` predicate never evaluates against a real partial. `aztea_steer` un-exercisable because the job never reaches `running` in time. Money refunds correctly, but UX is misleading. **Decision**: cut both tools from the public MCP lazy surface and the docs; keep the underlying code for an eventual rewrite. Touches `scripts/aztea_mcp_server.py:_LAZY_TOOL_NAMES`, `sdks/python-sdk/aztea/mcp/server.py`, docs/cli.md, docs/mcp-integration.md, CLAUDE.md.

- [ ] **~7 catalog agents return 502 `agent.endpoint_misconfigured`.** Persistent failures from the 2026-05-17 report: `image_generator_agent`, `type_checker`, `linter_agent`, `sql_explainer`, `arxiv_research_agent`, `financial_research_agent`, `docs_grounder` (live-data error). Catalog still shows them active. Auto-refund works (no money lost) but each shows a 0% / very-low `success_rate`. Either restore the endpoints or delist via `POST /admin/registry/agents/{id}/delist`. `docs_grounder` is already on the strategic cut list — start there.

- [ ] **`hire_batch` registry-search step times out on large payloads.** 502/503 around 60s read timeout when batches exceed ~25 jobs. Falls back to "local emergency snapshot" with a warning. Splitting into ≤20-job batches works reliably. Likely fix: cache or shard the registry-search call in `core/registry/auto_hire.py` so it doesn't run per-batch-row.

- [ ] **`domain-health` recipe leaks `InFailedSqlTransaction` once.** Postgres state poisoned by an earlier-step error not being rolled back. Wrap each step's DB ops in its own transaction in `core/recipes.py` / `core/pipelines/` so a step failure can't poison the next step's connection.

- [ ] **8 s sync-call wall-clock is too tight** for several legitimately slow agents (`load_tester`, `cve_lookup` tier-2, `accessibility_auditor`, `browser_agent`, `sast_scanner`, big `dependency_auditor`). The async fallback works correctly, but the error message could auto-suggest the async route. Options: surface a `retry_async_url` in the 504 envelope, or document the per-agent expected wall-clock in `describe_specialist`.

### Pre-existing
- [ ] **Postgres charge race-guard hardening.** `core/payments/base.py:18` notes phantom-read risk under READ COMMITTED. SQLite path uses `BEGIN IMMEDIATE` and is solid. Add a Postgres concurrency stress test before high-load prod traffic.
- [ ] **Worker disappearance reassign.** Today the lease times out and the caller is refunded rather than re-served. For built-in agents this is fine because the in-process worker pool is N-of-N. For third-party agents, decide whether a fallback retry to a different worker is in scope.
- [ ] **MCP tool count drift CI check.** Lazy mode advertises **9 tools** (`scripts/aztea_mcp_server.py`). Several docs previously said "four-tool surface" or "seven tools". Add a CI check or doctest that asserts the published tool list against the code so the next rename doesn't silently drift.
- [ ] Re-evaluate `core.listing_safety` ImportError fallback in `sdks/python-sdk/aztea/cli/publish.py:50` — kept for partial-install ergonomics; covered by `tests/test_cli_publish_safety_fallback.py`. Decide whether to make it a hard import once partial installs are no longer supported.
- [ ] Continue splitting any SDK / server module approaching the 1000-line CI hard limit (`scripts/check_file_line_budget.py`).

## Conventions
- Dates absolute (YYYY-MM-DD), never "Thursday" / "next week".
- Commit short sha for shipped items.
- Move items between sections rather than rewriting.
- Owner must be a person or `@team` handle; "TBD" is not an owner.

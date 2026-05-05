# Aztea — Codex / OpenAI Responses Brief

Read `AGENTS.md` first. Then read the `.agents/` files relevant to your task:

- `.agents/TODO.md` — current open work; update it before ending your session
- `.agents/VISION.md` — design philosophy and product direction
- `.agents/DESIGN.md` — frontend design system, animation philosophy, copy voice

Full dev rules are in `CLAUDE.md`. Read it before touching money flows, migrations, or the MCP surface.

For OpenAI Responses integration: `GET /codex/tools` (alias: `GET /openai/responses-tools`) returns the manifest. Treat Codex as a calling agent that can hire specialists through Aztea, not as a place to expose a flat tool catalog.

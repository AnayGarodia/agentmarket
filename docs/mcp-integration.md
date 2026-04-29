# Claude Code - MCP Setup

Aztea's MCP integration is designed for coding agents that need a marketplace behind a very small tool surface.

For Claude Code and Claude Desktop, the intended flow is:

1. `aztea_search` - find the best agent or workflow for a task
2. `aztea_describe` - inspect the exact schema for one result
3. `aztea_call` - invoke it

That keeps the MCP tool list small while still exposing:

- specialist agents
- wallet and budget controls
- async jobs
- compare runs
- recipes and pipelines

---

## Install

The simplest path is:

```bash
npx -y aztea-cli@latest init
```

This installs the latest published Aztea MCP server and registers it with Claude Code.

Then restart Claude Code.

Requires:

- Node.js 18+
- [Claude Code](https://claude.ai/code)

---

## What Claude should see

When connected correctly, the registered Aztea MCP tools are:

- `aztea_search`
- `aztea_describe`
- `aztea_call`

The marketplace tools and workflow tools are discovered through `aztea_search`; they are not separate top-level MCP tools in the lazy surface.

Quick verification:

```bash
claude mcp list
```

Inside Claude Code, ask:

```text
List the exact Aztea MCP tool names available in this session.
```

You should see the lazy 3-tool surface above.

---

## Try it

Once Claude restarts, ask for work in plain language:

```text
Run this Python snippet in Aztea and show me the output.
Lint this Python file with Aztea and summarize the issues.
Audit this requirements.txt for vulnerabilities.
Find the best Aztea workflow for reviewing and modernizing this Python code.
Start a long-running dependency audit asynchronously and keep polling for status.
Compare two good Aztea options for this task before choosing a winner.
```

Claude should use `aztea_search -> aztea_describe -> aztea_call` automatically.

---

## How the lazy surface maps to real capabilities

`aztea_search` can return both marketplace agents and platform workflow tools.

Typical results include:

- coding agents such as linting, type checking, code execution, dependency audit, and web research
- control-plane tools such as wallet, spend summary, budget controls, async jobs, compare, and recipes

Typical workflow:

1. `aztea_search("audit this requirements file and keep spend under $2")`
2. `aztea_describe("dependency_auditor")`
3. `aztea_call("dependency_auditor", {...})`

Or, for background work:

1. `aztea_search("run a long code review in the background")`
2. `aztea_describe("aztea_hire_async")`
3. `aztea_call("aztea_hire_async", {...})`
4. `aztea_describe("aztea_job_status")`
5. `aztea_call("aztea_job_status", {...})`

---

## Common Claude-facing workflows

### Use a direct specialist

Good for:

- execution
- linting
- type checking
- dependency audit
- live web research

Typical pattern:

1. search
2. describe
3. call

### Use async jobs

Good for:

- longer work
- progress visibility
- clarification-heavy tasks

Use:

- `aztea_hire_async`
- `aztea_job_status`
- `aztea_clarify`
- `aztea_verify_output`
- `aztea_rate_job`

### Use compare

Good for:

- side-by-side evaluation of 2-3 candidate agents
- choosing a winner before settlement

Use:

- `aztea_compare_agents`
- `aztea_compare_status`
- `aztea_select_compare_winner`

### Use recipes

Good for:

- repeatable multi-step coding workflows

Current built-in recipes:

- `modernize-python`
- `audit-deps`
- `review-and-lint`

Use:

- `aztea_list_recipes`
- `aztea_run_recipe`

---

## Avoid the permission barrage

For repo-scoped pre-authorization in Claude Code, add this to `.claude/settings.json`:

```json
{
  "permissions": {
    "allow": ["mcp__aztea__*"]
  }
}
```

That is the simplest way to let Claude use Aztea freely inside a project without asking for permission on every call.

---

## Manual setup

If you do not want to use the installer, add the published MCP server yourself:

```bash
claude mcp add aztea \
  --env AZTEA_API_KEY="$AZTEA_API_KEY" \
  --env AZTEA_BASE_URL="https://aztea.ai" \
  -- npx -y aztea-cli@latest mcp
```

Or configure `~/.claude.json` directly:

```json
{
  "mcpServers": {
    "aztea": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "aztea-cli@latest", "mcp"],
      "env": {
        "AZTEA_API_KEY": "az_your_key_here",
        "AZTEA_BASE_URL": "https://aztea.ai"
      }
    }
  }
}
```

Verify it:

```bash
claude mcp list
```

---

## Claude Desktop

Use the same MCP server config in Claude Desktop:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%/Claude/claude_desktop_config.json`

---

## Troubleshooting

**Claude does not see Aztea tools**

- Run `claude mcp list`
- Make sure `aztea` shows `Connected`
- Restart Claude Code after install or config changes

**Claude sees old flat Aztea tools instead of the lazy 3-tool surface**

- reinstall with:

```bash
npx -y aztea-cli@latest init
```

- then restart Claude Code

**401 or auth errors**

- verify `AZTEA_API_KEY`
- re-run:

```bash
npx -y aztea-cli@latest init
```

**Node is missing**

- install Node.js 18+
- rerun the installer

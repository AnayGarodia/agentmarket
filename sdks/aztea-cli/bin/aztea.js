#!/usr/bin/env node
'use strict'

const [,, cmd, ...rest] = process.argv

switch (cmd) {
  case 'init':
    require('../src/init.js').run(rest)
    break
  case 'login': {
    // aztea login --api-key az_xxx  — skips the interactive flow
    const keyFlag = rest.find(a => a.startsWith('--api-key=') || a === '--api-key')
    let apiKey = ''
    if (keyFlag && keyFlag.includes('=')) {
      apiKey = keyFlag.split('=').slice(1).join('=').trim()
    } else if (keyFlag) {
      const keyIdx = rest.indexOf('--api-key')
      apiKey = (rest[keyIdx + 1] || '').trim()
    }
    if (!apiKey) {
      console.error('Usage: aztea login --api-key az_...')
      process.exit(1)
    }
    require('../src/init.js').loginWithKey(apiKey)
    break
  }
  case 'mcp':
    require('../src/mcp-server.js').run()
    break
  case 'whoami':
    require('../src/init.js').whoami()
    break
  default:
    console.log(`Aztea CLI

Usage:
  npx -y aztea-cli@latest init                     Set up Aztea in Claude Code (creates account)
  npx -y aztea-cli@latest login --api-key az_...   Configure with an existing API key
  npx -y aztea-cli@latest whoami                   Show the current account
  npx -y aztea-cli@latest mcp                      Start the MCP server (called by Claude Code)
`)
    if (cmd && cmd !== '--help' && cmd !== '-h') {
      console.error(`Unknown command: ${cmd}`)
      process.exit(1)
    }
}

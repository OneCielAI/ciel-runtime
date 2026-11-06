import { expect, test } from 'claude-code/testing'

test('side MCP parity digest runs in the hooks environment', async ($, on) => {
  const logs: string[] = []
  const registered: unknown[] = []
  // Each hook stands for the engine beneath the plugin.
  on('tool.list', () => ({ value: [
    { name: 'mcp__ciel-runtime-router__work_status', description: '', mcp: true },
    { name: 'mcp__mail__read', description: '', mcp: true },
    { name: 'Read', description: '', mcp: false },
  ] }))
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'ciel-runtime-router' } }))
  on('agent.register', (_, e) => { registered.push(e); return { value: { agent: 'side-parity-probe:side-intake' } } })
  on('ui.log', (_, e) => { logs.push(e.text); return { value: undefined } })
  on('ui.invalidate', () => ({ value: undefined }))

  const out = await $.command.run({ command: 'parity-probe' })
  const row = JSON.parse(out.text ?? '{}')
  console.log('PROBE ' + out.text)
  console.log('PROBE-LOG ' + JSON.stringify(logs))

  expect(row.globals.TextEncoder).toBe('function')
  expect(row.globals.subtleDigest).toBe('function')
  // Same names as tests/side_intake_harness.mjs's first set; Node computed 66b71cafde6c0c90.
  expect(row.parity).toEqual({ toolCount: 2, serverCount: 2, digest: '66b71cafde6c0c90' })
  expect(logs).toContain('Side MCP parity · tools 2 · servers 2 · digest 66b71cafde6c0c90')
  expect(registered.length).toBe(1)
})

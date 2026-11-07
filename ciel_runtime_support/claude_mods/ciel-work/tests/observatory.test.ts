import { expect, test } from 'claude-code/testing'

// The /work-mode view pane mounted on the engine's real element tables, so a
// tree a surface would refuse (an element it lacks, a prop it does not take)
// fails here as it would in a session.
const SNAPSHOT = { enabled: true, phase: 'executing', approval: 'auto', version: 3, done: 1, total: 3,
  active_root: { id: 'g1', title: 'Goal', status: 'in_progress', priority: 'P1' }, focus: { node_ids: [] },
  continuation: { actionable: 1, waiting: [] }, queue: [], root_index: [{ id: 'g1', title: 'Goal', status: 'in_progress' }],
  breakdown: [{ id: 'a', title: 'Cache', status: 'in_progress', done: 0, total: 1, children: [] }] }
const DETAIL = { version: 3, root_id: 'g1', nodes: [
  { id: 'g1', parent_id: null, title: 'Goal', status: 'in_progress', note: '', evidence: '' },
  { id: 'a', parent_id: 'g1', title: 'Cache', status: 'blocked', note: 'TTL', evidence: '' }] }
const SETTINGS = [
  { key: 'work_mode', label: 'Work mode', value: true, kind: 'boolean' },
  { key: 'work_side_intake_model', label: 'Side model', value: 'inherit', kind: 'model', choices: ['inherit', '_A', '_B'] }]
function host(on) {
  const s = { writes: [] }
  on('session.id', () => ({ value: 'session-a' }))
  on('env.get', (_, e) => ({ value: e.key === 'CLAUDE_CODE_SUBAGENT_MODEL_FORCE' ? '0' : 'fixture-host-token' }))
  on('mcp.call', async ($, e) => {
    let value = {}
    if (e.tool === 'work_status') value = e.args.detail ? DETAIL : SNAPSHOT
    if (e.tool === 'work_live_settings') { if (e.args.key) s.writes.push([e.args.key, e.args.value]); value = { settings: SETTINGS, provider: 'p' } }
    if (e.tool === 'work_parallel_status') value = { version: 1, active_device: 0, leases: {}, policy: { max_active: 5, p1_reserved: 1 } }
    if (e.tool === 'work_side_status') value = { side_inbox_receipts: [] }
    if (e.tool === 'work_side_claim') value = { claim: null, settings: {}, handoff_replies: [] }
    return { value: { isError: false, content: [{ type: 'text', text: JSON.stringify(value) }] } }
  })
  on('command.register', (_, e) => ({ value: { command: e.name } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.close', () => ({}))
  on('session.start', () => ({ cwd: '/isolated' }))
  return s
}
const pane = (surface, columns) => ({ plugin: 'ciel-work', component: 'Pane', requestId: 'work-details', surface,
  viewport: { columns, rows: 40 }, props: { title: 'Work & SSE', isFocused: true, bodyColumns: columns,
    placement: 'inline', scroll: { offset: 0, bodyRows: 24 }, view: {} } })

for (const [surface, columns] of [['terminal', 100], ['mobile', 40]] as const) {
  test('Work & SSE pane validates and navigates on ' + surface, async ($, on) => {
    const s = host(on)
    await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
    await $.command.run({ command: 'work-mode', args: 'view' })
    const ui = await $.ui.mount(pane(surface, columns) as any)
    expect((await ui.find({ key: 'obs-header' }))).toBeDefined()
    expect((await ui.find({ key: 'node-a' }))?.text).toContain('Cache')
    await ui.press({ key: 'obs-settings' })
    expect((await ui.find({ key: 'setting-work_mode' }))?.text).toContain('Work mode = true')
    await ui.press({ key: 'setting-work_side_intake_model' })
    expect((await ui.find({ key: 'model-_B' }))?.text).toContain('_B')
    await ui.press({ key: 'model-_B' })
    expect(s.writes).toEqual([['work_side_intake_model', '_B']])
    await ui.press({ key: 'obs-side' })
    expect((await ui.find({ key: 'side-p1' }))).toBeDefined()
    await ui.unmount()
  })
}

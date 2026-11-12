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
let ACTIVE_ACTORS = false
const SETTINGS = [
  { key: 'work_mode', label: 'Work mode', value: true, kind: 'boolean' },
  { key: 'claude_left_arrow_opens_agents', label: 'Left arrow opens agents view', value: true, kind: 'boolean', status: 'next_claude_session' },
  { key: 'claude_background_agents_enabled', label: 'Background agents', value: null, kind: 'boolean', status: 'unreadable' }]
function host(on) {
  const s = { writes: [] }
  on('session.id', () => ({ value: 'session-a' }))
  on('env.get', (_, e) => ({ value: e.key === 'CLAUDE_CODE_SUBAGENT_MODEL_FORCE' ? '0' : 'fixture-host-token' }))
  on('mcp.call', async ($, e) => {
    let value = {}
    if (e.tool === 'work_status') value = e.args.detail ? DETAIL : SNAPSHOT
    if (e.tool === 'work_live_settings') { if (e.args.key) s.writes.push([e.args.key, e.args.value]); value = { settings: SETTINGS, provider: 'p' } }
    if (e.tool === 'work_parallel_status') value = { version: 1, active_device: 0, leases: {}, policy: { max_active: 5, p1_reserved: 1 } }
    if (e.tool === 'work_side_status') value = { side_inbox_receipts: ACTIVE_ACTORS ? [
      { receipt_id: 'receipt-p1', stage: 'processing', processing: { owner: 'side' },
        original: '{"data":{"priority":1,"text":"Urgent side request"}}' }] : [] }
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

test('graphical SSE view labels processing receipts while side work is active', async ($, on) => {
  ACTIVE_ACTORS = true
  try {
    host(on)
    await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
    await $.command.run({ command: 'work-mode', args: 'view' })
    const ui = await $.ui.mount(pane('terminal', 100) as any)
    await ui.resize({ columns: 100, rows: 30, in: 'obs-graphic' })
    await ui.key({ key: 's', in: 'obs-graphic' })
    const text = (await ui.findAll({ type: 'Text', in: 'obs-graphic' })).map(row => row.text).join('\n')
    expect(text).toContain('SSE FLOW 1')
    expect(text).toContain('processing')
    expect(text).toContain('Urgent side request')
    await ui.unmount()
  } finally { ACTIVE_ACTORS = false }
})

// A Client takes keys only after a click. These are the controls a keyboard
// reaches with the pane's own focus (open, Ctrl+X Tab): no ui.key into the Client.
test('terminal control strip drives the graphical view without a Client click', async ($, on) => {
  host(on)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  await $.command.run({ command: 'work-mode', args: 'view' })
  const ui = await $.ui.mount(pane('terminal', 100) as any)
  await ui.resize({ columns: 100, rows: 30, in: 'obs-graphic' })
  const graphic = async () => (await ui.findAll({ type: 'Text', in: 'obs-graphic' })).map(x => x.text).join('\n')
  const work = await ui.find({ key: 'obs-work' })
  expect(work?.type).toBe('Button')
  expect(work?.props.autoFocus).toBe(true)
  for (const [key, hotkey] of [['obs-work', 'w'], ['obs-side', 's'], ['obs-settings', 'g'], ['obs-up', 'k'],
    ['obs-down', 'j'], ['obs-inspect', 'i'], ['obs-p1', 'p'], ['obs-focus', 'f'], ['detail-refresh', 'r']])
    expect((await ui.find({ key }))?.props.hotkey).toBe(hotkey)
  expect(await graphic()).toContain('INSPECTOR WORK g1')
  await ui.press({ key: 'obs-down' })
  expect(await graphic()).toContain('INSPECTOR WORK a')
  expect(await graphic()).toContain('blocked')
  await ui.press({ key: 'obs-up' })
  expect(await graphic()).toContain('INSPECTOR WORK g1')
  await ui.press({ key: 'obs-side' })
  expect(await graphic()).toContain('INSPECTOR SSE')
  expect((await ui.find({ key: 'obs-side' }))?.props.autoFocus).toBe(true)
  await ui.press({ key: 'obs-work' })
  await ui.press({ key: 'obs-p1' })
  expect((await ui.find({ key: 'obs-p1' }))?.text).toContain('[P1]')
  expect(await graphic()).toContain('INSPECTOR WORK none')
  await ui.unmount()
})

test('/work-mode settings opens the pane on its Settings tab', async ($, on) => {
  host(on)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  const result = await $.command.run({ command: 'work-mode', args: 'settings', origin: { kind: 'composer' } } as any)
  expect(result.text).toContain('Settings tab')
  const ui = await $.ui.mount(pane('terminal', 100) as any)
  expect((await ui.find({ key: 'setting-work_mode' }))?.text).toContain('Work mode = true')
  await ui.unmount()
})

for (const [surface, columns] of [['terminal', 100], ['mobile', 40]] as const) {
  test('Work & SSE pane validates and navigates on ' + surface, async ($, on) => {
    const s = host(on)
    await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
    await $.command.run({ command: 'work-mode', args: 'view' })
    const ui = await $.ui.mount(pane(surface, columns) as any)
    if (surface === 'terminal') {
      expect((await ui.find({ key: 'obs-graphic' }))).toBeDefined()
      expect((await ui.find({ key: 'obs-header' }))).toBeUndefined()
      await ui.resize({ columns: 100, rows: 30, in: 'obs-graphic' })
      expect((await ui.findAll({ type: 'Text', in: 'obs-graphic' })).map(x=>x.text).join('\n')).toContain('WORK / SESSION')
      expect((await ui.findAll({ type: 'Text', in: 'obs-graphic' })).map(x=>x.text).join('\n')).toContain('WORK TREE')
      // The click-focused path: keys and pointer handed straight to the Client.
      await ui.key({ key: 'down', in: 'obs-graphic' })
      await ui.key({ key: 's', in: 'obs-graphic' })
      expect((await ui.findAll({ type: 'Text', in: 'obs-graphic' })).map(x=>x.text).join('\n')).toContain('SSE FLOW')
      await ui.pointer({ type: 'down', x: 5, y: 0, button: 'left', in: 'obs-graphic' })
      expect((await ui.findAll({ type: 'Text', in: 'obs-graphic' })).map(x=>x.text).join('\n')).toContain('WORK TREE')
    } else {
      expect((await ui.find({ key: 'obs-header' }))).toBeDefined()
      expect((await ui.find({ key: 'node-a' }))?.text).toContain('Cache')
    }
    if (surface === 'terminal') await ui.key({ key: 'g', in: 'obs-graphic' })
    else await ui.press({ key: 'obs-settings' })
    expect((await ui.find({ key: 'setting-work_mode' }))?.text).toContain('Work mode = true')
    expect((await ui.find({ key: 'setting-claude_background_agents_enabled' }))?.text).toContain('= unreadable')
    await ui.press({ key: 'setting-claude_left_arrow_opens_agents' })
    expect((await ui.find({ key: 'setting-work_side_intake_model' }))).toBeUndefined()
    expect((await ui.find({ key: 'model-_B' }))).toBeUndefined()
    expect(s.writes).toEqual([['claude_left_arrow_opens_agents', 'off']])
    if (surface !== 'terminal') {
      await ui.press({ key: 'obs-side' })
      expect((await ui.find({ key: 'side-p1' }))).toBeDefined()
    }
    await ui.unmount()
  })
}

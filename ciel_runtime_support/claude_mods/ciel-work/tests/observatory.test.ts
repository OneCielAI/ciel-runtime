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
let ACTIVE_ACTORS: boolean | 'p0' | 'failures' = false
// Walkie 2026-11-17: rows the router still retries, or that wait for main's
// compaction, were labelled "failed" like a quarantined (terminal) row.
const FAILURE_RECEIPTS = [
  { receipt_id: 'receipt-retry', stage: 'received', original: '{"data":{"priority":3,"text":"Retry row"}}',
    side_failure: { category: 'turn_error', attempt_count: 2, quarantined: false, retryable: true } },
  { receipt_id: 'receipt-wait', stage: 'received', original: '{"data":{"priority":3,"text":"Waiting row"}}',
    side_failure: { category: 'main_compaction_pending', attempt_count: 0, compaction_waits: 4, quarantined: false } },
  { receipt_id: 'receipt-dead', stage: 'received', original: '{"data":{"priority":3,"text":"Terminal row"}}',
    side_failure: { category: 'source_context', attempt_count: 6, compaction_waits: 12, quarantined: true } }]
const SETTINGS = [
  { key: 'work_mode', label: 'Work mode', value: true, kind: 'boolean' },
  { key: 'claude_left_arrow_opens_agents', label: 'Left arrow opens agents view', value: true, kind: 'boolean', status: 'next_claude_session' },
  { key: 'claude_background_agents_enabled', label: 'Background agents', value: null, kind: 'boolean', status: 'unreadable' },
  { key: 'work_parallel_model_p0', label: 'P0 Work fork model', value: 'inherit', kind: 'enum', status: 'inherit',
    choices: ['inherit', 'm-a', 'm-b', 'm-c', 'm-d', 'm-e'] }]
function host(on) {
  const s = { writes: [], calls: [] }
  on('session.id', () => ({ value: 'session-a' }))
  on('env.get', (_, e) => ({ value: e.key === 'CLAUDE_CODE_SUBAGENT_MODEL_FORCE' ? '0' : 'fixture-host-token' }))
  on('mcp.call', async ($, e) => {
    s.calls.push(e)
    let value = {}
    if (e.tool === 'work_status') value = e.args.detail ? DETAIL : SNAPSHOT
    if (e.tool === 'work_live_settings') { if (e.args.key) s.writes.push([e.args.key, e.args.value]); value = { settings: SETTINGS, provider: 'p' } }
    if (e.tool === 'work_parallel_status') value = { version: 1, active_device: 0, leases: {}, policy: { max_active: 5, p1_reserved: 1 } }
    if (e.tool === 'work_side_status') value = { side_inbox_receipts: ACTIVE_ACTORS === 'failures' ? FAILURE_RECEIPTS
      : ACTIVE_ACTORS === 'p0' ? [
      { receipt_id: 'receipt-p0', stage: 'processing', processing: { owner: 'side' },
        original: '{"data":{"text":"Server down","message_context":{"thread_task":{"priority":1,"emergency":true}}}}' }]
      : ACTIVE_ACTORS ? [
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
    await $.command.run({ command: 'work-mode', args: 'view all' })
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

test('graphical SSE view calls only a quarantined row failed; retries and compaction waits keep their state', async ($, on) => {
  ACTIVE_ACTORS = 'failures'
  try {
    host(on)
    await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
    await $.command.run({ command: 'work-mode', args: 'view all' })
    const ui = await $.ui.mount(pane('terminal', 100) as any)
    await ui.resize({ columns: 100, rows: 30, in: 'obs-graphic' })
    await ui.key({ key: 's', in: 'obs-graphic' })
    const rows = (await ui.findAll({ type: 'Text', in: 'obs-graphic' })).map(row => row.text)
    const row = (summary: string) => rows.find(text => text.includes(summary)) || ''
    expect(rows.join('\n')).toContain('SSE FLOW 3')
    expect(row('Retry row')).toContain('P3 RT')
    expect(row('Waiting row')).toContain('P3 WT')
    expect(row('Terminal row')).toContain('P3 FL')
    expect(row('Retry row')).not.toContain('failed')
    expect(row('Waiting row')).not.toContain('failed')
    await ui.unmount()
  } finally { ACTIVE_ACTORS = false }
})

test('graphical SSE view shows a Walkie emergency as P0 and keeps it under the urgent filter', async ($, on) => {
  ACTIVE_ACTORS = 'p0'
  try {
    host(on)
    await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
    await $.command.run({ command: 'work-mode', args: 'view all' })
    const ui = await $.ui.mount(pane('terminal', 100) as any)
    await ui.resize({ columns: 100, rows: 30, in: 'obs-graphic' })
    await ui.key({ key: 's', in: 'obs-graphic' })
    await ui.press({ key: 'obs-p1' })
    const text = (await ui.findAll({ type: 'Text', in: 'obs-graphic' })).map(row => row.text).join('\n')
    expect(text).toContain('SSE FLOW 1')
    expect(text).toContain('P0')
    expect(text).toContain('Server down')
    await ui.unmount()
  } finally { ACTIVE_ACTORS = false }
})

// A Client takes keys only after a click. These are the controls a keyboard
// reaches with the pane's own focus (open, Ctrl+X Tab): no ui.key into the Client.
test('terminal control strip drives the graphical view without a Client click', async ($, on) => {
  host(on)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  await $.command.run({ command: 'work-mode', args: 'view all' })
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

test('terminal strip appends to focus, opens the parent and moves a queued goal', async ($, on) => {
  const saved = { focus: SNAPSHOT.focus, queue: SNAPSHOT.queue, root_index: SNAPSHOT.root_index }
  SNAPSHOT.focus = { node_ids: ['x'] }
  SNAPSHOT.queue = [{ id: 'q2', title: 'Queued goal', status: 'pending' }]
  SNAPSHOT.root_index = [...saved.root_index, { id: 'q2', title: 'Queued goal', status: 'pending' }]
  try {
    const s = host(on)
    await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
    await $.command.run({ command: 'work-mode', args: 'view all' })
    const ui = await $.ui.mount(pane('terminal', 100) as any)
    await ui.resize({ columns: 100, rows: 30, in: 'obs-graphic' })
    const graphic = async () => (await ui.findAll({ type: 'Text', in: 'obs-graphic' })).map(x => x.text).join('\n')
    for (const [key, hotkey] of [['obs-focus-add', 'a'], ['obs-parent', 'b'], ['obs-goal-up', 'u'], ['obs-goal-down', 'd']])
      expect((await ui.find({ key }))?.props.hotkey).toBe(hotkey)
    await ui.press({ key: 'obs-down' })
    expect(await graphic()).toContain('INSPECTOR WORK a')
    await ui.press({ key: 'obs-focus-add' })
    expect(s.calls.filter(c => c.tool === 'work_focus').at(-1)?.args.node_ids).toEqual(['x', 'a'])
    await ui.press({ key: 'obs-parent' })
    expect(await graphic()).toContain('INSPECTOR WORK g1')
    await ui.press({ key: 'obs-down' }); await ui.press({ key: 'obs-down' })
    expect(await graphic()).toContain('INSPECTOR WORK q2')
    await ui.press({ key: 'obs-goal-up' })
    const move = s.calls.filter(c => c.tool === 'work_reorder').at(-1)?.args
    expect([move?.root_id, move?.position, move?.user_requested, move?.expected_version]).toEqual(['q2', 1, true, 3])
    await ui.unmount()
  } finally { Object.assign(SNAPSHOT, saved) }
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
    await $.command.run({ command: 'work-mode', args: 'view all' })
    const ui = await $.ui.mount(pane(surface, columns) as any)
    if (surface === 'terminal') {
      expect((await ui.find({ key: 'obs-graphic' }))).toBeDefined()
      expect((await ui.find({ key: 'obs-header' }))).toBeUndefined()
      await ui.resize({ columns: 100, rows: 30, in: 'obs-graphic' })
      expect((await ui.findAll({ type: 'Text', in: 'obs-graphic' })).map(x=>x.text).join('\n')).toContain('WORK / SESSION')
      expect((await ui.findAll({ type: 'Text', in: 'obs-graphic' })).map(x=>x.text).join('\n')).toContain('[전체]')
      // The click-focused path: keys and pointer handed straight to the Client.
      await ui.key({ key: 'down', in: 'obs-graphic' })
      await ui.key({ key: 's', in: 'obs-graphic' })
      expect((await ui.findAll({ type: 'Text', in: 'obs-graphic' })).map(x=>x.text).join('\n')).toContain('SSE FLOW')
      await ui.pointer({ type: 'down', x: 5, y: 0, button: 'left', in: 'obs-graphic' })
      expect((await ui.findAll({ type: 'Text', in: 'obs-graphic' })).map(x=>x.text).join('\n')).toContain('[전체]')
    } else {
      expect((await ui.find({ key: 'obs-header' }))).toBeDefined()
      expect((await ui.find({ key: 'node-a' }))?.text).toContain('Cache')
    }
    if (surface === 'terminal') await ui.key({ key: 'g', in: 'obs-graphic' })
    else await ui.press({ key: 'obs-settings' })
    expect((await ui.find({ key: 'setting-work_mode' }))?.text).toContain('Work mode = true')
    expect((await ui.find({ key: 'setting-claude_background_agents_enabled' }))?.text).toContain('= unreadable')
    await ui.press({ key: 'setting-claude_left_arrow_opens_agents' })
    // Per-priority fork models (2026-11-14) are ordinary enum rows.
    expect((await ui.find({ key: 'setting-work_parallel_model_p0' }))?.text).toContain('P0 Work fork model = inherit')
    expect((await ui.find({ key: 'model-_B' }))).toBeUndefined()
    expect(s.writes).toEqual([['claude_left_arrow_opens_agents', 'off']])
    if (surface !== 'terminal') {
      await ui.press({ key: 'obs-side' })
      expect((await ui.find({ key: 'side-p1' }))).toBeDefined()
    }
    await ui.unmount()
  })
}

test('graphical pane clears finalized reviews on host status updates without manual refresh', async ($, on) => {
  // Andy 2026-11-26: n2866 and n2871 were approved and finalized, but the docked
  // observatory pane kept rendering the opening observatoryParallel snapshot
  // ("REVIEW 8 · Awaiting main review · n2866 / n2871") until manual Refresh.
  let parallelReply: Record<string, unknown> = {
    version: 1, active_device: 2, leases: {},
    policy: { enabled: true, paused: false, priority_limits: { P1: 2, P2: 1, P3: 1, P4: 1, P5: 1 } },
    review: {
      version: 10,
      pending: [
        { lease_id: 'l-2866', item_id: 'n2866', item_title: 'Contract map', priority: 'P3', state: 'pending', decision: null },
        { lease_id: 'l-2871', item_id: 'n2871', item_title: 'Runner metrics', priority: 'P3', state: 'pending', decision: null },
        { lease_id: 'l-2777', item_id: 'n2777', item_title: 'Older rework', priority: 'P1', state: 'rework_ready', decision: 'rework' },
      ],
    },
  }
  on('session.id', () => ({ value: 'session-a' }))
  on('env.get', (_, e) => ({ value: e.key === 'CLAUDE_CODE_SUBAGENT_MODEL_FORCE' ? '0' : 'fixture-host-token' }))
  on('mcp.call', async (_, e) => {
    let value: unknown = {}
    if (e.tool === 'work_status') value = e.args.detail ? DETAIL : SNAPSHOT
    if (e.tool === 'work_live_settings') value = { settings: SETTINGS, provider: 'p' }
    if (e.tool === 'work_parallel_status') value = parallelReply
    if (e.tool === 'work_side_status') value = { side_inbox_receipts: [] }
    if (e.tool === 'work_side_claim') value = { claim: null, settings: {}, handoff_replies: [] }
    if (e.tool === 'work_parallel_event') value = { recorded: true }
    return { value: { isError: false, content: [{ type: 'text', text: JSON.stringify(value) }] } }
  })
  on('command.register', (_, e) => ({ value: { command: e.name } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.close', () => ({}))
  on('session.start', () => ({ cwd: '/isolated' }))
  on('classic.Stop', () => ({}))

  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  await $.command.run({ command: 'work-mode', args: 'view all' })
  await new Promise(resolve => setTimeout(resolve, 40))
  const ui = await $.ui.mount(pane('terminal', 100) as any)
  await ui.resize({ columns: 100, rows: 30, in: 'obs-graphic' })
  const graphic = async () => (await ui.findAll({ type: 'Text', in: 'obs-graphic' })).map(x => x.text).join('\n')

  const before = await graphic()
  expect(before).toContain('DEVICE 2/6 slots · REVIEW 3')
  expect(before).toContain('Awaiting main review · n2866')
  expect(before).toContain('Awaiting main review · n2871')

  // Main finalizes n2866 and n2871; the next host status read (Stop) advances review.version without manual Refresh.
  parallelReply = {
    version: 2, active_device: 1, leases: {},
    policy: { enabled: true, paused: false, priority_limits: { P1: 2, P2: 1, P3: 1, P4: 1, P5: 1 } },
    review: {
      version: 12,
      pending: [
        { lease_id: 'l-2777', item_id: 'n2777', item_title: 'Older rework', priority: 'P1', state: 'rework_ready', decision: 'rework' },
      ],
    },
  }
  await $.classic.Stop({ session_id: 'session-a', stop_hook_active: false } as any)
  const after = await graphic()
  expect(after).toContain('DEVICE 1/6 slots · REVIEW 1')
  expect(after).not.toContain('Awaiting main review')
  expect(after).not.toContain('n2866')
  expect(after).not.toContain('n2871')
  expect(after).toContain('Rework · n2777')
  await ui.unmount()
})

test('graphical pane shows P0-P5 lanes and all running fork items beyond 3 including parent goals', async ($, on) => {
  const now = Math.floor(Date.now() / 1000)
  const snap = {
    ...SNAPSHOT,
    version: 9,
    queue: [
      { id: 'g2', title: 'P0 incident', status: 'in_progress', priority: 'P1', done: 0, total: 1 },
      { id: 'g3', title: 'P2 feature', status: 'in_progress', priority: 'P2', done: 0, total: 1 },
      { id: 'g4', title: 'P3 chore', status: 'in_progress', priority: 'P3', done: 0, total: 1 },
      { id: 'g5', title: 'P4 cleanup', status: 'in_progress', priority: 'P4', done: 0, total: 1 },
      { id: 'g6', title: 'P5 backlog', status: 'in_progress', priority: 'P5', done: 0, total: 1 },
    ],
    root_index: [
      { id: 'g1', title: 'Goal', status: 'in_progress', priority: 'P1' },
      { id: 'g2', title: 'P0 incident', status: 'in_progress', priority: 'P1' },
      { id: 'g3', title: 'P2 feature', status: 'in_progress', priority: 'P2' },
      { id: 'g4', title: 'P3 chore', status: 'in_progress', priority: 'P3' },
      { id: 'g5', title: 'P4 cleanup', status: 'in_progress', priority: 'P4' },
      { id: 'g6', title: 'P5 backlog', status: 'in_progress', priority: 'P5' },
    ],
  }
  const detail = {
    version: 9, root_id: 'g1', nodes: [
      { id: 'g1', parent_id: null, title: 'Goal', status: 'in_progress', priority: 'P1', note: '', evidence: '' },
      { id: 'a', parent_id: 'g1', title: 'Cache', status: 'in_progress', note: '', evidence: '' },
    ],
  }
  const mk = (lease: string, item: string, goalId: string, title: string, prio: string, eff: string) => ({
    lease_id: lease, item_id: item, goal_id: goalId, item_title: title, priority: prio, effective_priority: eff,
    role: 'worker', state: 'running', slot: true, agent_name: 'work-' + item, agent_id: 'ag-' + item,
    native: { status: 'running', last_seen_at: now - 3 }, evidence: 'confirmed',
  })
  const parallelReply = {
    version: 4, active_device: 6, leases: {},
    policy: { enabled: true, paused: false, priority_limits: { P1: 2, P2: 2, P3: 1, P4: 1, P5: 1 } },
    root_priorities: [
      { root_id: 'g2', effective_priority: 'P0' },
      { root_id: 'g1', effective_priority: 'P1' },
      { root_id: 'g3', effective_priority: 'P2' },
      { root_id: 'g4', effective_priority: 'P3' },
      { root_id: 'g5', effective_priority: 'P4' },
      { root_id: 'g6', effective_priority: 'P5' },
    ],
    forks: {
      available: true, confirmed_count: 6, evidence_version: 2,
      confirmed: [
        mk('l0', 'n20', 'g2', 'Stop outage', 'P1', 'P0'),
        mk('l1', 'a', 'g1', 'Cache', 'P1', 'P1'),
        mk('l2', 'n30', 'g3', 'API schema', 'P2', 'P2'),
        mk('l3', 'n40', 'g4', 'Docs sync', 'P3', 'P3'),
        mk('l4', 'n50', 'g5', 'Log trim', 'P4', 'P4'),
        mk('l5', 'n60', 'g6', 'Archive old', 'P5', 'P5'),
      ],
      waiting: [], reservation: [], uncertain: [], validating: [], ended: [],
    },
    priority_diagnosis: {
      summary: 'P0 1 goals running · P1 1/2 can fill · P2 1/2 can fill · P3 1/1 full · P4 1/1 full · P5 1/1 full',
      attention: [],
      rows: [
        { priority: 'P0', limit: null, active: 1, free: null, candidates: 0, state: 'goals_running', reason: '1 P0 goal(s) running' },
        { priority: 'P1', limit: 2, active: 1, free: 1, candidates: 1, state: 'can_fill', reason: '1 runnable' },
        { priority: 'P2', limit: 2, active: 1, free: 1, candidates: 1, state: 'can_fill', reason: '1 runnable' },
        { priority: 'P3', limit: 1, active: 1, free: 0, candidates: 0, state: 'full', reason: 'all slots in use' },
        { priority: 'P4', limit: 1, active: 1, free: 0, candidates: 0, state: 'full', reason: 'all slots in use' },
        { priority: 'P5', limit: 1, active: 1, free: 0, candidates: 0, state: 'full', reason: 'all slots in use' },
      ],
    },
  }
  on('session.id', () => ({ value: 'session-a' }))
  on('env.get', (_, e) => ({ value: e.key === 'CLAUDE_CODE_SUBAGENT_MODEL_FORCE' ? '0' : 'fixture-host-token' }))
  on('mcp.call', async (_, e) => {
    let value: unknown = {}
    if (e.tool === 'work_status') value = e.args.detail ? detail : snap
    if (e.tool === 'work_live_settings') value = { settings: SETTINGS, provider: 'p' }
    if (e.tool === 'work_parallel_status') value = parallelReply
    if (e.tool === 'work_side_status') value = { side_inbox_receipts: [] }
    if (e.tool === 'work_side_claim') value = { claim: null, settings: {}, handoff_replies: [] }
    if (e.tool === 'work_parallel_event') value = { recorded: true }
    return { value: { isError: false, content: [{ type: 'text', text: JSON.stringify(value) }] } }
  })
  on('command.register', (_, e) => ({ value: { command: e.name } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.close', () => ({}))
  on('session.start', () => ({ cwd: '/isolated' }))

  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  await $.command.run({ command: 'work-mode', args: 'view all' })
  await new Promise(resolve => setTimeout(resolve, 40))
  const ui = await $.ui.mount(pane('terminal', 110) as any)
  await ui.resize({ columns: 110, rows: 36, in: 'obs-graphic' })
  const text = (await ui.findAll({ type: 'Text', in: 'obs-graphic' })).map(x => x.text).join('\n')
  // All six priority lanes P0..P5 must be visible with their live fork items (not truncated at 3).
  for (const marker of ['P0', 'P1', 'P2', 'P3', 'P4', 'P5', 'n20', 'n40', 'n50', 'n60'])
    expect(text).toContain(marker)
  // Parent goal rows whose child item runs in a fork (g2->n20, g3->n30, g4->n40, g5->n50, g6->n60) show active fork status.
  const flat = text.replace(/\n/g, ' ')
  expect(flat).toMatch(/P0\s+P0 incident\s+●포크×1/)
  expect(flat).toMatch(/P5\s+P5 backlog\s+●포크×1/)
  expect(flat).toMatch(/Cache\s+●포크실행/)
  expect(text).toContain('◉하위포크 1개(a)')
  await ui.unmount()
})

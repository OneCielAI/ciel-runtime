import { expect, mock, test } from 'claude-code/testing'
const T = 'mcp__ciel-runtime-router__'
function setup(on, extra = {}) {
  const s = { snapshot: { enabled: true, phase: 'planning', approval: 'auto', version: 1,
    active_root: { id: 'g1', title: 'Goal' }, done: 0, total: 2, ...extra },
    sessionId: 'session-a', calls: [], tools: [], registered: null, offline: false, answer: 'Approve', change: false, rendered: null, failText: false }
  on('session.id', () => ({ value: s.sessionId }))
  on('mcp.call', ($, e) => {
    s.calls.push(e)
    if (s.offline || (s.failText && e.args.format === 'text')) return { deny: 'offline' }
    return { value: { isError: false, content: [{ type: 'text', text: e.args.format === 'json'
      ? JSON.stringify(s.snapshot) : 'Goal -> phase -> task' }] } }
  })
  on('command.register', ($, e) => { s.registered = e; return { value: { command: e.name } } })
  on('session.start', () => ({ cwd: '/isolated' }))
  on('prompt.submit', ($, e) => ({ text: e.text, context: e.context }))
  on('tool.check', () => ({ decision: 'allow' }))
  on('ui.render', ($, e) => { s.rendered = e; return { type: 'Text', props: {}, children: e.props.modes } })
  on('tool.call', ($, e) => {
    s.tools.push(e)
    if (e.tool === 'AskUserQuestion') {
      if (s.change) s.snapshot.version++
      if (s.changeSession) s.sessionId = 'other-session'
      return { result: { answers: { [e.questions[0].question]: s.answer } } }
    }
    if (e.tool === T + 'work_execute') s.snapshot.phase = 'executing'
    return { result: 'ok' }
  })
  return s
}
test('immediate command, router tree, bounded prompt context', async ($, on) => {
  const s = setup(on)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  expect(s.registered.immediate).toBe(true)
  expect(s.registered.name).toBe('work-mode')
  expect((await $.command.run({ command: 'work-mode', args: 'tree' })).text).toContain('Goal -> phase')
  const r = await $.prompt.submit({ text: 'Do it', context: ['existing'], wait: false })
  expect(r.context[0]).toBe('existing')
  expect(r.context[1]).toContain('work_plan_set')
  expect(r.context[1].length < 4000).toBe(true)
})
test('planning denies all known writes including subagents; live phase refresh', async ($, on) => {
  const s = setup(on)
  for (const tool of ['Edit', 'Write', 'NotebookEdit', 'apply_patch']) {
    expect((await $.tool.call({ tool, file_path: '/x', agentId: 'child' })).deny).toContain('planning')
  }
  expect(s.tools.length).toBe(0)
  s.snapshot.phase = 'executing'
  expect((await $.tool.call({ tool: 'Write', file_path: '/x', content: 'x' })).deny).toBeUndefined()
  s.snapshot.phase = 'planning'
  expect((await $.tool.call({ tool: 'Edit', file_path: '/x' })).deny).toContain('planning')
})
test('shells use engine read-only flag and disabled state clears guards', async ($, on) => {
  const s = setup(on)
  for (const tool of ['Bash', 'PowerShell']) {
    expect((await $.tool.check({ tool, input: {} })).decision).toBe('deny')
    expect((await $.tool.check({ tool, input: {}, isReadOnly: true })).decision).toBe('allow')
  }
  s.snapshot.enabled = false
  expect((await $.tool.call({ tool: 'Write', file_path: '/x', content: 'x' })).deny).toBeUndefined()
  expect((await $.prompt.submit({ text: 'x', wait: false })).context).toBeUndefined()
})
test('unavailable and malformed status fail closed but reads pass', async ($, on) => {
  const s = setup(on); s.offline = true
  expect((await $.tool.call({ tool: 'Write', file_path: '/x' })).deny).toContain('unavailable')
  expect((await $.command.run({ command: 'work-mode', args: 'status' })).text).toContain('unavailable')
  expect((await $.tool.call({ tool: 'Read', file_path: '/x' })).deny).toBeUndefined()
  s.offline = false; s.snapshot.phase = 'bogus'
  expect((await $.tool.call({ tool: 'Edit', file_path: '/x' })).deny).toContain('unavailable')
})
test('auto strips forged confirmation and refreshes after result', async ($, on) => {
  const s = setup(on)
  await $.tool.call({ tool: T + 'work_execute', user_confirmed: true })
  expect(s.tools[0].user_confirmed).toBeUndefined()
  expect(s.calls.length).toBe(2)
})
for (const answer of ['Approve', 'Keep planning', 'Other']) {
  test('ask: ' + answer, async ($, on) => {
    const s = setup(on, { approval: 'ask' }); s.answer = answer
    const r = await $.tool.call({ tool: T + 'work_execute', user_confirmed: true })
    const executed = s.tools.filter(t => t.tool === T + 'work_execute')
    expect(executed.length).toBe(answer === 'Approve' ? 1 : 0)
    if (answer === 'Approve') expect(executed[0].user_confirmed).toBe(true)
    else expect(r.deny).toContain('not approved')
  })
}
test('approval rejects changed plan', async ($, on) => {
  const s = setup(on, { approval: 'ask' }); s.change = true
  expect((await $.tool.call({ tool: T + 'work_execute' })).deny).toContain('changed')
  expect(s.tools.filter(t => t.tool === T + 'work_execute').length).toBe(0)
})

test('SessionMode planning, progress, off and unavailable are read-only renders', async ($, on) => {
  const s = setup(on)
  const render = async () => {
    const before = s.calls.length
    const ui = await $.ui.mount({ plugin: 'engine', component: 'SessionMode', props: { modes: ['auto', 'work: stale'] }, surface: 'terminal' })
    expect(s.calls.length).toBe(before)
    const modes = s.rendered.props.modes
    await ui.unmount()
    return modes
  }
  await $.command.run({ command: 'work-mode', args: 'status' })
  expect((await render())[1]).toContain('work: plan 0/2')
  s.snapshot.phase = 'executing'; s.snapshot.done = 1
  await $.command.run({ command: 'work-mode', args: 'status' })
  expect((await render())[1]).toContain('work: run 1/2')
  s.offline = true
  await $.command.run({ command: 'work-mode', args: 'status' })
  expect(await render()).toEqual(['auto', 'work: unavailable'])
  s.offline = false; s.snapshot.enabled = false
  await $.command.run({ command: 'work-mode', args: 'status' })
  expect(await render()).toEqual(['auto'])
})
test('unavailable shell and execute fail closed; missing approval tree cannot execute', async ($, on) => {
  const s = setup(on, { approval: 'ask' }); s.offline = true
  expect((await $.tool.call({ tool: 'Bash', command: 'anything' })).deny).toContain('unavailable')
  expect((await $.tool.call({ tool: T + 'work_execute' })).deny).toContain('unavailable')
  s.offline = false; s.failText = true
  expect((await $.tool.call({ tool: T + 'work_execute' })).deny).toContain('not obtained')
  expect(s.tools.length).toBe(0)
})
test('every Work result refreshes; another server is not treated as Work', async ($, on) => {
  const s = setup(on)
  for (const tool of ['work_plan_set', 'work_node_add', 'work_node_update', 'work_node_remove',
    'work_select', 'work_replan', 'work_status']) {
    const before = s.calls.length
    await $.tool.call({ tool: T + tool })
    expect(s.calls.length - before).toBe(2)
  }
  const before = s.calls.length
  await $.tool.call({ tool: 'mcp__other__work_execute', user_confirmed: true })
  expect(s.calls.length).toBe(before)
})

test('conversation changes isolate every router read and override forged tool session ids', async ($, on) => {
  const s = setup(on)
  await $.tool.call({ tool: T + 'work_status', session_id: 'claude:someone-else' })
  expect(s.tools[0].session_id).toBe('claude:session-a')
  expect(s.calls.every(c => c.args.session_id === 'claude:session-a')).toBe(true)
  s.sessionId = 'session-b'
  await $.command.run({ command: 'work-mode', args: 'tree' })
  expect(s.calls.at(-1).args.session_id).toBe('claude:session-b')
  expect(s.calls.at(-2).args.session_id).toBe('claude:session-b')
  s.sessionId = ''
  expect((await $.tool.call({ tool: T + 'work_plan_set', goal: 'bad' })).deny).toContain('unavailable')
})

test('approval rejects a conversation switch even when plan counters match', async ($, on) => {
  const s = setup(on, { approval: 'ask' }); s.changeSession = true
  expect((await $.tool.call({ tool: T + 'work_execute' })).deny).toContain('changed')
  expect(s.tools.filter(t => t.tool === T + 'work_execute').length).toBe(0)
})

const STOP = { hook_event_name: 'Stop', session_id: 'session-a', transcript_path: '/isolated/transcript',
  cwd: '/isolated', stop_hook_active: false }
function continuing(on, extra = {}) {
  const s = hints(on, { auto_continue: true, phase: 'executing', continuation: {
    actionable: 1, next: { id: 'leaf', title: 'Verify next' }, waiting: [] }, ...extra })
  on('classic.Stop', () => ({}))
  return s
}
test('normal Stop continues, unchanged plan is bounded, progress and user input reset it', async ($, on) => {
  const s = continuing(on)
  expect((await $.classic.Stop(STOP)).block).toContain('Verify next')
  expect((await $.classic.Stop({ ...STOP, stop_hook_active: true })).block).toContain('last continuation')
  expect((await $.classic.Stop(STOP)).block).toBeUndefined()
  expect(s.toasts.length).toBe(1)
  s.snapshot.version++
  expect((await $.classic.Stop(STOP)).block).toContain('approved scope')
  await $.prompt.submit({ text: 'continue', wait: false })
  expect((await $.classic.Stop(STOP)).block).toContain('Verify next')
})
for (const extra of [{ enabled: false }, { auto_continue: false }, { continuation: { actionable: 0 } },
  { phase: 'planning', approval: 'ask', ready: true }]) {
  test('Stop guard ' + JSON.stringify(extra), async ($, on) => {
    continuing(on, extra)
    expect((await $.classic.Stop(STOP)).block).toBeUndefined()
  })
}
test('Stop excludes background tasks, child threads, other conversations and offline', async ($, on) => {
  const s = continuing(on)
  expect((await $.classic.Stop({ ...STOP, background_tasks: [{ id: 'b' }] })).block).toBeUndefined()
  expect((await $.classic.Stop({ ...STOP, agent_id: 'child' })).block).toBeUndefined()
  expect((await $.classic.Stop({ ...STOP, session_id: 'other' })).block).toBeUndefined()
  s.offline = true
  expect((await $.classic.Stop(STOP)).block).toBeUndefined()
})
test('auto planning continues decomposition but never crosses an approval boundary', async ($, on) => {
  continuing(on, { phase: 'planning', approval: 'auto', ready: false })
  expect((await $.classic.Stop(STOP)).block).toContain('No new scope or approval boundary')
})

const BAND = { plugin: 'ciel-work', component: 'AbovePrompt', surface: 'terminal', props: {
  hasSurvey: false, isWorking: false, maxRows: 16, bodyColumns: 80, scroll: { offset: 0, bodyRows: 15 }, view: {} } } as const
const PANE = { plugin: 'ciel-work', component: 'Pane', requestId: 'work-details', surface: 'terminal', props: {
  title: 'Work details', isFocused: true, bodyColumns: 64, placement: 'inline', scroll: { offset: 0, bodyRows: 18 }, view: {} } } as const
function inspection(on) {
  const s = setup(on, { phase: 'executing', breakdown: [
    { id: 'a', title: 'Cache', status: 'in_progress', done: 1, total: 3 },
    { id: 'b', title: 'DAG', status: 'pending', done: 0, total: 2 }], nodes: [
    { id: 'g1', parent_id: null, title: 'Goal', status: 'in_progress' },
    { id: 'a', parent_id: 'g1', title: 'Cache', status: 'in_progress', note: 'TTL policy' },
    { id: 'leaf', parent_id: 'a', title: 'Verify expiry', status: 'done', evidence: 'expiry test passed' },
    { id: 'b', parent_id: 'g1', title: 'DAG', status: 'pending' }] })
  on('ui.open', (_, e) => { s.opened = e; return { value: { isPlaced: true } } })
  on('ui.close', () => ({}))
  return s
}
test('three levels display linked ids, waits, urgent holds and green done checks', async ($, on) => {
  const s = inspection(on)
  s.snapshot.active_root.urgent = true
  s.snapshot.breakdown[0].ref = 'A-10'
  s.snapshot.breakdown[0].children = [
    { id: 'leaf', title: 'Verify expiry', status: 'done', ref: 'A-11' },
    { id: 'wait', title: 'Release', status: 'pending', waiting_on: 'CI publish' }]
  s.snapshot.breakdown[0].more = 2
  s.snapshot.queue = [{ id: 'q', title: 'Other goal', status: 'pending', held: true, done: 0, total: 1 }]
  await $.command.run({ command: 'work-mode', args: 'status' })
  const band = await $.ui.mount(BAND)
  expect((await band.find({ key: 'branch-a' }))?.text).toContain('[ A-10 ] Cache')
  expect((await band.find({ key: 'child-leaf' }))?.text).toContain('[ A-11 ] Verify expiry')
  expect((await band.find({ key: 'child-wait' }))?.text).toContain('◷ CI publish')
  expect((await band.find({ type: 'Text', text: /more · Enter/ }))?.text).toContain('+2 more')
  expect((await band.find({ type: 'Text', text: /Held:/ }))?.text).toContain('Held:')
  expect((await band.find({ type: 'Text', text: '✓ ' }))?.props.color).toBe('success')
  expect((await band.find({ type: 'Text', text: /Work running/ }))?.text).toContain('[urgent]')
  await band.unmount()
})
test('root move buttons pin version, identity and explicit user request', async ($, on) => {
  const s = inspection(on)
  s.snapshot.queue = [{ id: 'q', title: 'Queued goal', status: 'pending', done: 0, total: 1 }]
  await $.command.run({ command: 'work-mode', args: 'view' })
  const pane = await $.ui.mount(PANE)
  await pane.press({ key: 'up-q' })
  const move = s.calls.find(c => c.tool === 'work_reorder')
  expect(move.args).toEqual({ root_id: 'q', position: 1, user_requested: true,
    expected_version: 1, session_id: 'claude:session-a' })
  await pane.unmount()
})
test('compact footer and vertical branch buttons', async ($, on) => {
  inspection(on)
  await $.command.run({ command: 'work-mode', args: 'status' })
  const footer = await $.ui.mount({ plugin: 'ciel-work', component: 'SessionMode', props: { modes: [] }, surface: 'terminal' })
  expect((await footer.drawn()).children).toEqual(['work: run 0/2'])
  await footer.unmount()
  const ui = await $.ui.mount(BAND)
  expect((await ui.drawn()).props.flexDirection).toBe('column')
  expect((await ui.find({ key: 'branch-a' }))?.text).toContain('Cache')
  expect((await ui.find({ key: 'branch-b' }))?.text).toContain('DAG')
  await ui.unmount()
})
test('branch press opens read-only details with notes and evidence, nested node and parent', async ($, on) => {
  const s = inspection(on)
  await $.command.run({ command: 'work-mode', args: 'status' })
  const band = await $.ui.mount(BAND)
  await band.press({ key: 'branch-a' })
  expect(s.opened.id).toBe('work-details')
  const pane = await $.ui.mount(PANE)
  expect((await pane.find({ type: 'Text', text: /TTL policy/ }))?.text).toContain('TTL policy')
  expect((await pane.find({ type: 'Text', text: /expiry test passed/ }))?.text).toContain('expiry test passed')
  await pane.press({ key: 'node-leaf' })
  expect((await pane.find({ key: 'node-leaf' }))?.text).toContain('Verify expiry')
  expect(await pane.find({ key: 'node-a' })).toBeUndefined()
  await pane.press({ key: 'detail-parent' })
  expect((await pane.find({ key: 'node-a' }))?.text).toContain('Cache')
  expect(s.calls.every(c => c.tool === 'work_status')).toBe(true)
  expect(s.tools.length).toBe(0)
  await pane.unmount(); await band.unmount()
})
test('detail state is hidden after version or conversation changes and offline', async ($, on) => {
  const s = inspection(on)
  await $.command.run({ command: 'work-mode', args: 'view' })
  const pane = await $.ui.mount(PANE)
  s.snapshot.version++
  await $.command.run({ command: 'work-mode', args: 'status' })
  expect((await pane.find({ type: 'Text', text: /Plan changed/ }))?.text).toContain('refresh')
  await pane.press({ key: 'detail-refresh' })
  expect((await pane.find({ key: 'node-a' }))?.text).toContain('Cache')
  s.sessionId = 'another'
  await $.command.run({ command: 'work-mode', args: 'status' })
  expect(await pane.find({ key: 'node-a' })).toBeUndefined()
  s.offline = true
  await $.command.run({ command: 'work-mode', args: 'status' })
  expect(await pane.find({ key: 'node-a' })).toBeUndefined()
  await pane.unmount()
})
function hints(on, extra = {}) {
  const s = setup(on, extra); s.logs = []; s.toasts = []
  on('ui.log', (_, e) => { s.logs.push(e.text); return { value: undefined } })
  on('ui.toast', (_, e) => { s.toasts.push(e.text); return { value: undefined } })
  return s
}
test('interactive start logs one person-facing notice; -p and off do not', async ($, on) => {
  const s = hints(on)
  await $.session.start({ surface: null, isInteractive: false, cwd: '/isolated' })
  expect(s.logs.length).toBe(0)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  expect(s.logs.length).toBe(1)
  expect(s.logs[0]).toContain('/work-mode help')
  expect(s.logs[0]).toContain('LLM options → Work mode')
  s.snapshot.enabled = false
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  expect(s.logs.length).toBe(1)
})
test('a router still connecting at start is read again; band and notice follow once it answers', async ($, on) => {
  const s = hints(on); const clock = mock.clock(on)
  s.offline = true
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  const band = await $.ui.mount(BAND)
  expect(await band.find({ type: 'Text', text: /Work unavailable/ })).toBeDefined()
  expect(s.logs.length).toBe(0)
  await clock.advance(750)
  expect(s.logs.length).toBe(0)
  s.offline = false
  await clock.advance(1000)
  expect(s.logs.length).toBe(1)
  expect(await band.find({ type: 'Text', text: /no plan yet|Work planning/ })).toBeDefined()
  const reads = s.calls.length
  await clock.advance(20000)
  expect(s.calls.length).toBe(reads)
  expect(s.logs.length).toBe(1)
  await band.unmount()
})
test('start retries stop after a bounded window, on a new session start and when Work is off', async ($, on) => {
  const s = hints(on); const clock = mock.clock(on)
  s.offline = true
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  await clock.advance(16000)
  const reads = s.calls.length
  await clock.advance(60000)
  expect(s.calls.length).toBe(reads)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  s.offline = false; s.sessionId = 'session-b'
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  expect(s.logs.length).toBe(1)
  await clock.advance(16000)
  expect(s.logs.length).toBe(1)
  s.offline = true
  await $.session.start({ surface: null, isInteractive: false, cwd: '/isolated' })
  const quiet = s.calls.length
  await clock.advance(16000)
  expect(s.calls.length).toBe(quiet)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  s.offline = false; s.snapshot.enabled = false
  await clock.advance(16000)
  expect(s.logs.length).toBe(1)
})
test('planning denial explains itself and toasts once per plan version', async ($, on) => {
  const s = hints(on)
  const first = await $.tool.call({ tool: 'Edit', file_path: '/x' })
  expect(first.deny).toContain('work_execute')
  expect(first.deny).toContain('/work-mode help')
  await $.tool.call({ tool: 'Write', file_path: '/x', content: 'x' })
  expect((await $.tool.check({ tool: 'Bash', input: {} })).decision).toBe('deny')
  expect(s.toasts.length).toBe(1)
  s.snapshot.version++
  await $.tool.check({ tool: 'Bash', input: {} })
  expect(s.toasts.length).toBe(2)
  s.offline = true
  await $.tool.call({ tool: 'Edit', file_path: '/x' })
  expect(s.toasts.length).toBe(2)
})
test('help, readable bare command and raw status', async ($, on) => {
  const s = hints(on, { breakdown: [{ id: 'p1', title: '1. Parser', status: 'pending', done: 0, total: 2 }],
    next: { id: 'n1', title: 'Write tokenizer', status: 'pending' } })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  expect(s.registered.argumentHint).toContain('help')
  const help = (await $.command.run({ command: 'work-mode', args: 'help' })).text
  expect(help).toContain('its own numbered item')
  expect(help).toContain('added under that item')
  expect(help).toContain('Ctrl+X Tab')
  expect(help).toContain('LLM options')
  const bare = (await $.command.run({ command: 'work-mode', args: '' })).text
  expect(bare).toContain('planning · 0/2 steps')
  expect(bare).toContain('[ ] 1. Parser 0/2')
  expect(bare).toContain('Next: Write tokenizer')
  expect(bare.includes('{')).toBe(false)
  expect((await $.command.run({ command: 'work-mode', args: 'status' })).text).toContain('"version": 1')
  expect((await $.command.run({ command: 'work-mode', args: 'bogus' })).text).toContain('help|view|tree|status')
  s.snapshot.enabled = false
  expect((await $.command.run({ command: 'work-mode', args: '' })).text).toContain('LLM options → Work mode')
  expect(s.tools.length).toBe(0)
})
test('band shows a hint while no plan exists and the phase once one does', async ($, on) => {
  const s = inspection(on)
  s.snapshot.active_root = null; s.snapshot.breakdown = []; s.snapshot.total = 0
  await $.command.run({ command: 'work-mode', args: 'status' })
  const band = await $.ui.mount(BAND)
  expect((await band.find({ type: 'Text', text: /no plan yet/ }))?.text).toContain('/work-mode help')
  s.snapshot.active_root = { id: 'g1', title: 'Goal' }; s.snapshot.phase = 'planning'; s.snapshot.total = 2
  await $.command.run({ command: 'work-mode', args: 'status' })
  await band.redraw(BAND.props)
  expect((await band.find({ type: 'Text', text: /Work planning 0\/2/ }))?.text).toContain('Goal')
  await band.unmount()
})
test('triage guidance reaches the model; queue shows in band and summary without becoming focusable', async ($, on) => {
  const s = inspection(on)
  const r = await $.prompt.submit({ text: 'new request', wait: false })
  expect(r.context[0]).toContain('Triage every new user request')
  expect(r.context[0]).toContain('goes under that item as a child')
  s.snapshot.queue = Array.from({ length: 5 }, (_, i) => ({ id: 'q' + i, title: (i + 2) + '. Queued ' + i, status: 'pending', done: 0, total: 2 }))
  await $.command.run({ command: 'work-mode', args: 'status' })
  const band = await $.ui.mount(BAND)
  expect((await band.find({ type: 'Text', text: /Queued: \[ \] 2\. Queued 0/ }))?.text).toContain('0/2')
  expect((await band.find({ type: 'Text', text: /more queued/ }))?.text).toContain('+2 more queued')
  expect(await band.find({ type: 'Text', text: /Queued 3/ })).toBeUndefined()
  expect((await band.findAll({ type: 'Button' })).length).toBe(3)
  await band.unmount()
  const summary = (await $.command.run({ command: 'work-mode', args: '' })).text
  expect(summary).toContain('Queue (next first):')
  expect(summary).toContain('[ ] 6. Queued 4 0/2')
})
test('survey yields, disabled hides, long branch list retains every item', async ($, on) => {
  const s = inspection(on)
  s.snapshot.breakdown = Array.from({ length: 20 }, (_, i) => ({ id: 'b'+i, title: 'Branch '+i, status: 'pending', done: 0, total: 1 }))
  await $.command.run({ command: 'work-mode', args: 'status' })
  const band = await $.ui.mount(BAND)
  expect((await band.findAll({ type: 'Button' })).length).toBe(21)
  await band.redraw({ ...BAND.props, hasSurvey: true })
  expect((await band.findAll({ type: 'Button' })).length).toBe(0)
  s.snapshot.enabled = false
  await $.command.run({ command: 'work-mode', args: 'status' })
  await band.redraw(BAND.props)
  expect((await band.findAll({ type: 'Button' })).length).toBe(0)
  await band.unmount()
})

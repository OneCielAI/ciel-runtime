import { expect, test } from 'claude-code/testing'
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
test('compact footer and vertical branch buttons', async ($, on) => {
  inspection(on)
  await $.command.run({ command: 'work-mode', args: 'status' })
  const footer = await $.ui.mount({ plugin: 'ciel-work', component: 'SessionMode', props: { modes: [] }, surface: 'terminal' })
  expect((await footer.drawn()).children).toEqual(['work: run 0/2'])
  await footer.unmount()
  const ui = await $.ui.mount(BAND)
  expect((await ui.drawn()).props.flexDirection).toBe('column')
  expect((await ui.find({ key: 'branch-a' }))?.text).toContain('Cache 1/3')
  expect((await ui.find({ key: 'branch-b' }))?.text).toContain('DAG 0/2')
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

import { expect, mock, test } from 'claude-code/testing'
const T = 'mcp__ciel-runtime-router__'
function setup(on, extra = {}) {
  const s = { snapshot: { enabled: true, phase: 'planning', approval: 'auto', version: 1,
    active_root: { id: 'g1', title: 'Goal' }, done: 0, total: 2, ...extra },
    sessionId: 'session-a', calls: [], tools: [], registered: null, offline: false, answer: 'Approve', change: false, rendered: null, failText: false,
    waitStatus: null, activeReads: 0, maxReads: 0, nativeVerdict: { decision: 'allow' }, nativeChecks: [] }
  on('session.id', () => ({ value: s.sessionId }))
  on('mcp.call', async ($, e) => {
    s.calls.push(e)
    s.activeReads++; s.maxReads = Math.max(s.maxReads, s.activeReads)
    const snapshot = JSON.stringify(s.snapshot)
    try {
      if (s.waitStatus) await s.waitStatus()
      if (s.offline || (s.failText && e.args.format === 'text')) return { deny: 'offline' }
      return { value: { isError: false, content: [{ type: 'text', text: e.args.format === 'json'
        ? snapshot : 'Goal -> phase -> task' }] } }
    } finally { s.activeReads-- }
  })
  on('command.register', ($, e) => { s.registered = e; return { value: { command: e.name } } })
  on('session.start', () => ({ cwd: '/isolated' }))
  on('session.end', () => ({ sessionId: s.sessionId }))
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  on('turn.complete', (_, e) => ({ text: e.answer }))
  on('prompt.attachment', (_, e) => ({ text: e.text }))
  on('prompt.submit', ($, e) => ({ text: e.text, context: e.context }))
  on('tool.check', (_, e) => { s.nativeChecks.push(e); return s.nativeVerdict })
  on('ui.render', ($, e) => { s.rendered = e; return s.nativeTree || { type: 'Text', props: {}, children: e.props.modes } })
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
const NATIVE_PLAN = { type: 'plan_mode', text: 'Native planning instructions', origin: { kind: 'engine' },
  detail: { reminder: 'full', planFilePath: 'C:\\Users\\person\\.claude\\plans\\exact-plan.md', hasPlan: false } } as const
async function planAttachment($, extra = {}) {
  return $.prompt.attachment({ ...NATIVE_PLAN, ...extra })
}
test('only exact engine plan attachments exempt Edit and Write in the same native loop', async ($, on) => {
  const state = setup(on)
  const path = NATIVE_PLAN.detail.planFilePath
  await planAttachment($)
  const before = state.calls.length
  expect((await $.tool.call({ tool: 'Write', file_path: path, content: 'plan' })).deny).toBeUndefined()
  expect(state.calls.length).toBe(before)
  for (const tool of ['Edit', 'Write']) {
    expect((await $.tool.call({ tool, file_path: path, content: 'plan' })).deny).toBeUndefined()
    expect((await $.tool.check({ tool, input: { file_path: path } })).decision).toBe('allow')
    expect((await $.tool.call({ tool, file_path: path + '.other' })).deny).toContain('planning')
    expect((await $.tool.call({ tool, file_path: 'C:\\repo\\main.py' })).deny).toContain('planning')
    expect((await $.tool.call({ tool, file_path: path, agentId: 'child' })).deny).toContain('planning')
  }
  expect((await $.tool.call({ tool: 'NotebookEdit', notebook_path: path })).deny).toContain('planning')
  expect((await $.tool.call({ tool: 'apply_patch', patch: path })).deny).toContain('planning')
  expect((await $.tool.check({ tool: 'Bash', input: { command: 'write ' + path } })).decision).toBe('allow')
  await planAttachment($, { agentId: 'child', detail: { ...NATIVE_PLAN.detail, planFilePath: '/child-plan' } })
  expect((await $.tool.call({ tool: 'Write', file_path: '/child-plan', agentId: 'child' })).deny).toBeUndefined()
  expect((await $.tool.call({ tool: 'Write', file_path: '/child-plan' })).deny).toContain('planning')
})
test('native Windows plan facts accept slash spelling without widening the path', async ($, on) => {
  setup(on)
  const path = 'C:' + String.fromCharCode(92) + 'Plans' + String.fromCharCode(92) + 'Exact.md'
  await planAttachment($, { detail: { ...NATIVE_PLAN.detail, planFilePath: path } })
  expect((await $.tool.call({ tool: 'Write', file_path: 'c:/plans/exact.md' })).deny).toBeUndefined()
  expect((await $.tool.call({ tool: 'Write', file_path: 'c:/plans/../plans/exact.md' })).deny).toContain('planning')
  expect((await $.tool.call({ tool: 'Write', file_path: 'c:/plans/other.md' })).deny).toContain('planning')
})
test('no guessed paths, prose, plugin context or EnterPlanMode result can identify a plan file', async ($, on) => {
  setup(on)
  const path = NATIVE_PLAN.detail.planFilePath
  for (const extra of [{ detail: undefined, text: 'Write the plan to ' + path },
    { origin: { kind: 'hook', event: 'SessionStart' } }, { origin: { kind: 'plugin', event: 'prompt.submit' } }]) {
    await planAttachment($, extra)
    expect((await $.tool.call({ tool: 'Write', file_path: path })).deny).toContain('planning')
  }
  await $.tool.call({ tool: 'EnterPlanMode', planFilePath: path })
  expect((await $.tool.call({ tool: 'Write', file_path: path })).deny).toContain('planning')
})
test('native plan exemption clears on exit, missing facts and session changes; offline permits only that file', async ($, on) => {
  const s = setup(on)
  const path = NATIVE_PLAN.detail.planFilePath
  await planAttachment($)
  await $.prompt.attachment({ type: 'plan_mode_exit', text: 'Exit', origin: { kind: 'engine' },
    detail: { planFilePath: path, hasPlan: true } })
  expect((await $.tool.call({ tool: 'Write', file_path: path })).deny).toContain('planning')
  await planAttachment($)
  await planAttachment($, { detail: undefined })
  expect((await $.tool.call({ tool: 'Write', file_path: path })).deny).toContain('planning')
  await planAttachment($)
  s.offline = true
  expect((await $.tool.call({ tool: 'Write', file_path: path })).deny).toBeUndefined()
  expect((await $.tool.call({ tool: 'Write', file_path: path + '.other' })).deny).toContain('unavailable')
  s.offline = false; s.sessionId = 'other'
  expect((await $.tool.call({ tool: 'Write', file_path: path })).deny).toContain('planning')
  s.sessionId = 'session-a'
  expect((await $.tool.call({ tool: 'Write', file_path: path })).deny).toContain('planning')
  await planAttachment($)
  await $.session.end({ reason: 'clear' })
  expect((await $.tool.call({ tool: 'Write', file_path: path })).deny).toContain('planning')
})
test('shells delegate to native policy and disabled state clears edit guards', async ($, on) => {
  const s = setup(on)
  for (const tool of ['Bash', 'PowerShell']) {
    expect((await $.tool.check({ tool, input: {} })).decision).toBe('allow')
    expect((await $.tool.check({ tool, input: {}, isReadOnly: true })).decision).toBe('allow')
  }
  s.snapshot.enabled = false
  expect((await $.tool.call({ tool: 'Write', file_path: '/x', content: 'x' })).deny).toBeUndefined()
  expect((await $.prompt.submit({ text: 'x', wait: false })).context).toBeUndefined()
})
for (const tool of ['Bash', 'PowerShell']) {
  test(tool + ' never depends on Work status or rewrites native permission results', async ($, on) => {
    const s = setup(on)
    for (const state of ['planning', 'executing', 'disabled', 'offline', 'malformed']) {
      s.snapshot.enabled = state !== 'disabled'; s.offline = state === 'offline'
      s.snapshot.phase = state === 'malformed' ? 'bogus' : state === 'executing' ? 'executing' : 'planning'
      for (const agentId of [undefined, 'child']) {
        const input = { tool, command: 'harmless marker', agentId }
        const before = s.calls.length
        s.nativeVerdict = { decision: 'allow' }
        expect((await $.tool.call(input)).result).toBe('ok')
        expect(s.tools.at(-1).tool).toBe(input.tool)
        expect(s.tools.at(-1).command).toBe(input.command)
        expect(s.tools.at(-1).agentId).toBe(input.agentId)
        for (const isReadOnly of [undefined, true, false]) {
          for (const decision of ['allow', 'ask', 'deny']) {
            s.nativeVerdict = { decision, reason: 'native policy' }
            const check = { tool, input: { command: input.command }, agentId, isReadOnly }
            expect(await $.tool.check(check)).toEqual(s.nativeVerdict)
            expect(s.nativeChecks.at(-1).input).toEqual(check.input)
            expect(s.nativeChecks.at(-1).isReadOnly).toBe(isReadOnly)
            expect(s.nativeChecks.at(-1).agentId).toBe(agentId)
          }
        }
        expect(s.calls.length).toBe(before)
      }
    }
  })
}
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
test('offline shells delegate but execute and missing approval tree still fail closed', async ($, on) => {
  const s = setup(on, { approval: 'ask' }); s.offline = true
  expect((await $.tool.call({ tool: 'Bash', command: 'anything' })).deny).toBeUndefined()
  expect((await $.tool.call({ tool: T + 'work_execute' })).deny).toContain('unavailable')
  s.offline = false; s.failText = true
  expect((await $.tool.call({ tool: T + 'work_execute' })).deny).toContain('not obtained')
  expect(s.tools.map(t => t.tool)).toEqual(['Bash'])
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
test('Stop continues independent Work despite background tasks; excludes children, other conversations and offline', async ($, on) => {
  const s = continuing(on)
  expect((await $.classic.Stop({ ...STOP, background_tasks: [{ id: 'b' }] })).block).toContain('proceed only with independent Work')
  expect((await $.classic.Stop({ ...STOP, agent_id: 'child' })).block).toBeUndefined()
  expect((await $.classic.Stop({ ...STOP, session_id: 'other' })).block).toBeUndefined()
  s.offline = true
  expect((await $.classic.Stop(STOP)).block).toBeUndefined()
})
// .14 ARA: queued goals remained while every runnable step waited or was blocked; the stop was silent.
const STALLED = { continuation: { actionable: 0, next: null, waiting: [{ id: 'n76', title: 'Deploy', waiting_on: 'peer review' }],
  blocked: 8, held: 0 }, queue: [{ id: 'n1', title: 'Queued', status: 'in_progress', done: 22, total: 24 },
  { id: 'n42', title: 'Closed steps', status: 'blocked', done: 5, total: 5 }] }
test('a stop with unfinished but unrunnable Work explains why, once per state, without continuing', async ($, on) => {
  const s = continuing(on, STALLED)
  const first = await $.classic.Stop(STOP)
  expect(first.block).toBeUndefined()
  expect(s.logs.length).toBe(1)
  expect(s.logs[0]).toContain('none can run now')
  expect(s.logs[0]).toContain('n76 ◷ peer review')
  expect(s.logs[0]).toContain('8 blocked step(s)')
  expect(s.logs[0]).toContain('every step done but goal still open: n42')
  expect((await $.classic.Stop(STOP)).block).toBeUndefined()
  expect(s.logs.length).toBe(1)
  await $.prompt.submit({ text: 'injected', isMeta: true, wait: false })
  expect((await $.classic.Stop(STOP)).block).toBeUndefined()
  expect(s.logs.length).toBe(1)
  await $.prompt.submit({ text: 'why did it stop?', wait: false })
  expect((await $.classic.Stop(STOP)).block).toBeUndefined()
  expect(s.logs.length).toBe(2)
  s.snapshot.continuation = { ...STALLED.continuation, blocked: 9 }
  expect((await $.classic.Stop(STOP)).block).toBeUndefined()
  expect(s.logs[2]).toContain('9 blocked step(s)')
  expect(s.toasts.length).toBe(0)
  s.snapshot.continuation = { actionable: 0, next: null, waiting: [], blocked: 0, held: 2 }
  await $.classic.Stop(STOP)
  expect(s.logs[3]).toContain('2 goal(s) held behind the urgent goal')
})
test('a runnable item is still pushed after an explained stall, with the same no-progress bound', async ($, on) => {
  const s = continuing(on, STALLED)
  await $.classic.Stop(STOP)
  s.snapshot.continuation = { actionable: 1, next: { id: 'n2', title: 'Runnable step' }, waiting: [], blocked: 8, held: 0 }
  s.snapshot.version++
  expect((await $.classic.Stop(STOP)).block).toContain('The next item is n2: Runnable step')
  expect((await $.classic.Stop(STOP)).block).toContain('last continuation')
  expect((await $.classic.Stop(STOP)).block).toBeUndefined()
  expect(s.toasts.length).toBe(1)
  expect(s.logs.at(-1)).toContain('paused: the plan did not change')
  expect(s.logs.at(-1)).toContain('n2 Runnable step')
  expect((await $.classic.Stop(STOP)).block).toBeUndefined()
  expect(s.logs.filter(l => l.includes('paused')).length).toBe(1)
})
test('approval, background tasks and an unreadable status explain the stop; finished or off Work stays silent', async ($, on) => {
  const s = continuing(on, { ...STALLED, phase: 'planning', approval: 'ask', ready: true,
    continuation: { actionable: 1, next: { id: 'n2', title: 'Plan' }, waiting: [] } })
  expect((await $.classic.Stop(STOP)).block).toBeUndefined()
  expect(s.logs.at(-1)).toContain('waits for your approval')
  s.snapshot.phase = 'executing'
  s.snapshot.continuation = { actionable: 0, next: null, waiting: [{ id: 'n2', waiting_on: 'background result' }] }
  expect((await $.classic.Stop({ ...STOP, background_tasks: [{ id: 'b', type: 'subagent', status: 'running', description: 'x' }] })).block).toBeUndefined()
  expect(s.logs.at(-1)).toContain('1 background task(s) still running will wake this session')
  s.offline = true
  expect((await $.classic.Stop(STOP)).block).toBeUndefined()
  expect(s.logs.at(-1)).toContain('could not read the Work status')
  s.offline = false
  const before = s.logs.length
  s.snapshot.active_root = { id: 'g1', title: 'Goal', status: 'done' }; s.snapshot.queue = []
  s.snapshot.continuation = { actionable: 0, next: null, waiting: [], blocked: 0, held: 0 }
  expect((await $.classic.Stop(STOP)).block).toBeUndefined()
  s.snapshot.active_root = { id: 'g1', title: 'Goal' }; s.snapshot.queue = STALLED.queue
  for (const extra of [{ auto_continue: false }, { enabled: false }]) {
    Object.assign(s.snapshot, extra)
    expect((await $.classic.Stop(STOP)).block).toBeUndefined()
  }
  s.snapshot.enabled = true; s.snapshot.auto_continue = true
  expect((await $.classic.Stop({ ...STOP, agent_id: 'child' })).block).toBeUndefined()
  expect((await $.classic.Stop({ ...STOP, session_id: 'other' })).block).toBeUndefined()
  expect(s.logs.length).toBe(before)
})
test('background Work continuation reaches the same bounded block path, not person-only log', async ($, on) => {
  const s = continuing(on)
  const e = { ...STOP, background_tasks: [{ id: 'shell', type: 'local_bash', status: 'running' }] }
  const first = await $.classic.Stop(e)
  expect(first.block).toContain('The next item is leaf: Verify next')
  expect(first.block).toContain('Do not duplicate, poll, cancel or mark those tasks done')
  expect(s.logs.length).toBe(0)
  expect((await $.classic.Stop(e)).block).toContain('last continuation')
  expect((await $.classic.Stop(e)).block).toBeUndefined()
  expect(s.logs.at(-1)).toContain('plan did not change')
  s.snapshot.version++
  expect((await $.classic.Stop(e)).block).toContain('Verify next')
})
test('a Stop read superseded by a newer successful read is not reported as unavailable', async ($, on) => {
  const s = continuing(on, STALLED); const clock = mock.clock(on)
  let release
  s.waitStatus = () => new Promise(resolve => { release = resolve })
  const stop = $.classic.Stop(STOP)
  await clock.settle()
  expect(s.activeReads).toBe(1)
  s.waitStatus = null
  expect((await $.command.run({ command: 'work-mode', args: 'status' })).text).toContain('"version": 1')
  release()
  expect((await stop).block).toBeUndefined()
  expect(s.logs.some(l => l.includes('could not read'))).toBe(false)
})
test('auto planning continues decomposition but never crosses an approval boundary', async ($, on) => {
  continuing(on, { phase: 'planning', approval: 'auto', ready: false })
  expect((await $.classic.Stop(STOP)).block).toContain('No new scope or approval boundary')
})

const BAND = { plugin: 'ciel-work', component: 'AbovePrompt', surface: 'terminal', viewport: { columns: 80, rows: 40 }, props: {
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
  expect(await band.find({ key: 'child-leaf' })).toBeUndefined()
  expect((await band.find({ key: 'child-wait' }))?.text).toContain('◷ CI publish')
  // Hierarchy indentation survives the whitespace-collapsing clip.
  expect((await band.find({ key: 'child-wait' }))?.text).toMatch(/^  \[ \] Release/)
  expect((await band.find({ key: 'more-a' }))?.text).toContain('+2 more')
  await band.press({ key: 'work-tab-queue' })
  expect((await band.find({ key: 'queue-q' }))?.text).toContain('Held:')
  await band.press({ key: 'work-tab-completed' })
  expect((await band.find({ key: 'completed-leaf' }))?.text).toContain('[ A-11 ] Verify expiry')
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
  expect(await pane.find({ type: 'Text', text: /expiry test passed/ })).toBeUndefined()
  // A child of the inspected node keeps its indentation outside the clipped text.
  expect((await pane.find({ key: 'node-leaf' }))?.text).toMatch(/^  Verify expiry/)
  await pane.press({ key: 'node-leaf' })
  expect((await pane.find({ type: 'Text', text: /expiry test passed/ }))?.text).toContain('expiry test passed')
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
  expect(s.calls.length > reads).toBe(true)
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
const TURN_DONE = { turnId: 'turn-1', answer: 'done', durationMs: 1, isAborted: false, reason: 'answer' } as const
test('idle refresh reflects out-of-band edits, backs off and never starts a model turn', async ($, on) => {
  const s = hints(on); const clock = mock.clock(on)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  const band = await $.ui.mount(BAND)
  s.snapshot.active_root.title = 'Updated over HTTP'; s.snapshot.version++
  await clock.advance(1000)
  expect((await band.find({ type: 'Text', text: /Updated over HTTP/ }))?.text).toContain('Updated over HTTP')
  const reads = s.calls.length
  await clock.advance(31000)
  expect(s.calls.length - reads).toBe(5)
  await clock.advance(29999)
  expect(s.calls.length - reads).toBe(5)
  await clock.advance(1)
  expect(s.calls.length - reads).toBe(6)
  expect(s.tools.length).toBe(0)
  expect(s.calls.every(c => c.tool === 'work_status' && c.args.session_id === 'claude:session-a')).toBe(true)
  expect(s.logs.length).toBe(1)
  await band.unmount()
})
test('idle refresh pauses in a main turn but not a child turn, and stops at end or disabled', async ($, on) => {
  const s = hints(on); const clock = mock.clock(on)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  await $.turn.start({ turnId: 'child-turn', text: 'child', agentId: 'child' })
  await clock.advance(1000)
  expect(s.calls.length).toBe(2)
  await $.turn.start({ turnId: 'turn-1', text: 'run' })
  const busy = s.calls.length
  await clock.advance(120000)
  expect(s.calls.length).toBe(busy)
  await $.turn.complete({ ...TURN_DONE, agentId: 'child' })
  await clock.advance(1000)
  expect(s.calls.length).toBe(busy)
  await $.turn.complete(TURN_DONE)
  await clock.advance(1000)
  expect(s.calls.length).toBe(busy + 1)
  s.snapshot.enabled = false
  await clock.advance(2000)
  const disabled = s.calls.length
  await clock.advance(120000)
  expect(s.calls.length).toBe(disabled)
  s.snapshot.enabled = true
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  await $.session.end({ reason: 'clear' })
  const ended = s.calls.length
  await clock.advance(120000)
  expect(s.calls.length).toBe(ended)
})
test('idle refresh never overlaps a slow poll and discards its late response after end', async ($, on) => {
  const s = hints(on); const clock = mock.clock(on)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  let release
  s.waitStatus = () => new Promise(resolve => { release = resolve })
  await clock.advance(1000)
  expect(s.activeReads).toBe(1)
  const reads = s.calls.length
  await clock.advance(120000)
  expect(s.calls.length).toBe(reads)
  expect(s.maxReads).toBe(1)
  await $.session.end({ reason: 'clear' })
  s.waitStatus = null; release()
  await clock.settle()
  const band = await $.ui.mount(BAND)
  expect(await band.find({ type: 'Text', text: /Work unavailable/ })).toBeDefined()
  await clock.advance(120000)
  expect(s.calls.length).toBe(reads)
  await band.unmount()
})
test('late old-session poll cannot overwrite new-session state or spawn another timer', async ($, on) => {
  const s = hints(on); const clock = mock.clock(on)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  let release
  s.waitStatus = () => new Promise(resolve => { release = resolve })
  await clock.advance(1000)
  s.waitStatus = null; s.sessionId = 'session-b'; s.snapshot.version = 2; s.snapshot.active_root.title = 'Session B'
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  release(); await clock.settle()
  const band = await $.ui.mount(BAND)
  expect(await band.find({ type: 'Text', text: /Session B/ })).toBeDefined()
  const reads = s.calls.length
  await clock.advance(1000)
  expect(s.calls.length).toBe(reads + 1)
  expect(s.calls.at(-1).args.session_id).toBe('claude:session-b')
  await band.unmount()
})
test('offline idle refresh backs off and recovers without a prompt; startup retries stop at end', async ($, on) => {
  const s = hints(on); const clock = mock.clock(on)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  const band = await $.ui.mount(BAND)
  s.offline = true
  await clock.advance(31000)
  expect(s.calls.length).toBe(6)
  expect(await band.find({ type: 'Text', text: /Work unavailable/ })).toBeDefined()
  s.offline = false; s.snapshot.active_root.title = 'Recovered'; s.snapshot.version++
  await clock.advance(30000)
  expect(await band.find({ type: 'Text', text: /Recovered/ })).toBeDefined()
  await $.session.end({ reason: 'clear' })
  s.offline = true
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  await $.session.end({ reason: 'clear' })
  const ended = s.calls.length
  await clock.advance(120000)
  expect(s.calls.length).toBe(ended)
  await band.unmount()
})
test('silent identity changes stop the idle poll before any cross-session router read', async ($, on) => {
  const s = hints(on); const clock = mock.clock(on)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  s.sessionId = 'session-b'
  const reads = s.calls.length
  await clock.advance(120000)
  expect(s.calls.length).toBe(reads)
})
test('planning denial explains itself and toasts once per plan version', async ($, on) => {
  const s = hints(on)
  const first = await $.tool.call({ tool: 'Edit', file_path: '/x' })
  expect(first.deny).toContain('work_execute')
  expect(first.deny).toContain('/work-mode help')
  await $.tool.call({ tool: 'Write', file_path: '/x', content: 'x' })
  expect((await $.tool.check({ tool: 'Write', input: { file_path: '/x' } })).decision).toBe('deny')
  expect(s.toasts.length).toBe(1)
  s.snapshot.version++
  await $.tool.check({ tool: 'Write', input: { file_path: '/x' } })
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
test('triage guidance reaches the model; queue tab is read-only and summary keeps root order', async ($, on) => {
  const s = inspection(on)
  const r = await $.prompt.submit({ text: 'new request', wait: false })
  expect(r.context[0]).toContain('Triage every new user request')
  expect(r.context[0]).toContain('goes under that item as a child')
  s.snapshot.queue = Array.from({ length: 5 }, (_, i) => ({ id: 'q' + i, title: (i + 2) + '. Queued ' + i, status: 'pending', done: 0, total: 2 }))
  await $.command.run({ command: 'work-mode', args: 'status' })
  const band = await $.ui.mount(BAND)
  expect(await band.find({ key: 'queue-q0' })).toBeUndefined()
  const reads = s.calls.length
  await band.press({ key: 'work-tab-queue' })
  expect((await band.find({ key: 'queue-q0' }))?.text).toContain('2. Queued 0 0/2')
  expect((await band.find({ key: 'queue-q4' }))?.text).toContain('6. Queued 4')
  expect(s.calls.length).toBe(reads)
  expect(s.tools.length).toBe(0)
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
  const found = []
  for (let i = 0; i < 4; i++) {
    for (let j = 0; j < 20; j++) if (await band.find({ key: 'branch-b' + j })) found.push(j)
    if (i < 3) await band.press({ key: 'work-next' })
  }
  expect(found.length).toBe(20)
  expect(new Set(found).size).toBe(20)
  await band.redraw({ ...BAND.props, hasSurvey: true })
  expect((await band.findAll({ type: 'Button' })).length).toBe(0)
  s.snapshot.enabled = false
  await $.command.run({ command: 'work-mode', args: 'status' })
  await band.redraw(BAND.props)
  expect((await band.findAll({ type: 'Button' })).length).toBe(0)
  await band.unmount()
})

test('hybrid prompt keeps bounded valid active summary without queued title note or subtree', async ($, on) => {
  const s = setup(on, { phase: 'executing',
    active_root: { id: 'active', title: 'a'.repeat(10000), note: 'ACTIVE-NOTE' },
    breakdown: [{ id: 'branch', title: 'ACTIVE-BRANCH-NOTE' }],
    queue: Array.from({ length: 20 }, (_, i) => ({ id: 'q' + i, title: 'QUEUED-PRIVATE-TITLE',
      note: 'QUEUED-PRIVATE-NOTE', children: [{ title: 'QUEUED-PRIVATE-STEP' }] })) })
  const text = (await $.prompt.submit({ text: 'RAW-USER-REQUEST', wait: false })).context.at(-1)
  expect(text).toContain('hybrid per-item')
  expect(text).toContain('not native Plan Mode')
  expect(text).toContain('within_scope=true')
  expect(text).toContain('raw user messages remain in native history')
  expect(text).not.toContain('QUEUED-PRIVATE')
  expect(text).not.toContain('ACTIVE-NOTE')
  const data = JSON.parse(text.slice(text.lastIndexOf(String.fromCharCode(10)) + 1))
  expect(data.phase).toBe('executing')
  expect(data.active_root.title.length <= 180).toBe(true)
  expect(data.queue_count).toBe(20)
  expect(data.queued_ids.length).toBe(5)
  expect(text.length < 4400).toBe(true)
})
test('ask approval reads exact active goal only and never overrides native edit denial', async ($, on) => {
  const s = setup(on, { approval: 'ask', phase: 'executing' })
  s.nativeVerdict = { decision: 'deny', reason: 'native policy' }
  expect(await $.tool.check({ tool: 'Write', input: { file_path: '/x' } })).toEqual(s.nativeVerdict)
  s.snapshot.phase = 'planning'
  await $.tool.call({ tool: T + 'work_execute' })
  expect(s.calls.find(c => c.args.format === 'text').args.root_id).toBe('g1')
})

test('Stop moves on to the runnable queued goal when every active step waits', async ($, on) => {
  continuing(on, { active_root: { id: 'g1', title: 'waiting goal', status: 'in_progress' },
    continuation: { actionable: 1, next: { id: 'q-leaf', title: 'Queued step', root_id: 'q1', phase: 'planning' },
      waiting: [{ id: 'g1-leaf', title: 'Review', waiting_on: 'external review' }] } })
  const block = (await $.classic.Stop(STOP)).block
  expect(block).toContain('call work_select with root_id q1')
  expect(block).toContain('call work_execute before changing files')
  expect(block).not.toContain('user-priority rule')
})
test('Stop keeps the active goal when its own step is next', async ($, on) => {
  continuing(on, { active_root: { id: 'g1', title: 'active', status: 'in_progress' },
    continuation: { actionable: 1, next: { id: 'g1-leaf', title: 'Active step', root_id: 'g1', phase: 'executing' }, waiting: [] } })
  const block = (await $.classic.Stop(STOP)).block
  expect(block).toContain('Active step')
  expect(block).not.toContain('work_select')
})
test('queued planning target cannot inherit active approval during continuation', async ($, on) => {
  continuing(on, { phase: 'executing', approval: 'ask', auto_continue: true,
    continuation: { actionable: 1, next: { id: 'queued-leaf', title: 'private', root_id: 'q1', phase: 'planning' } } })
  expect((await $.classic.Stop(STOP)).block).toBeUndefined()
})

test('tabs classify by status not ratios and completion counts follow the latest snapshot', async ($, on) => {
  const s = inspection(on)
  s.snapshot.breakdown = [
    { id: 'open', title: 'Open despite ratio', status: 'in_progress', done: 1, total: 1 },
    { id: 'empty', title: 'Empty open', status: 'pending', done: 0, total: 0 },
    { id: 'done', title: 'Verified branch', status: 'done', done: 0, total: 0 }]
  s.snapshot.completed_active = [{ id: 'done', title: 'Verified branch', status: 'done' },
    { id: 'deep', title: 'Deep completed leaf', status: 'done' }]
  s.snapshot.root_index = [{ id: 'g1', title: 'Goal', status: 'in_progress' },
    { id: 'past', title: 'Completed root', status: 'done' }]
  await $.command.run({ command: 'work-mode', args: 'status' })
  const band = await $.ui.mount(BAND)
  expect(await band.find({ key: 'branch-open' })).toBeDefined()
  expect(await band.find({ key: 'branch-empty' })).toBeDefined()
  expect(await band.find({ key: 'branch-done' })).toBeUndefined()
  expect((await band.find({ key: 'work-tab-completed' }))?.text).toContain('Completed 3')
  const reads = s.calls.length
  await band.press({ key: 'work-tab-completed' })
  expect(await band.find({ key: 'finished-past' })).toBeDefined()
  expect(await band.find({ key: 'completed-deep' })).toBeDefined()
  expect(s.calls.length).toBe(reads)
  s.snapshot.version++; s.snapshot.completed_active = []; s.snapshot.root_index = [s.snapshot.root_index[0]]
  await $.command.run({ command: 'work-mode', args: 'status' })
  expect((await band.find({ key: 'work-tab-completed' }))?.text).toContain('Completed 0')
  expect(await band.find({ key: 'completed-deep' })).toBeUndefined()
  s.offline = true
  await $.command.run({ command: 'work-mode', args: 'status' })
  expect(await band.find({ key: 'finished-past' })).toBeUndefined()
  expect(await band.find({ type: 'Text', text: /Work unavailable/ })).toBeDefined()
  await band.unmount()
})
test('bounded root index pages every queued/history root; details only read the pressed root', async ($, on) => {
  const s = inspection(on)
  const queue = Array.from({ length: 25 }, (_, i) => ({ id: 'q' + i, title: 'Queued ' + i, status: 'pending', children: [{ title: 'NO SUBTREE' }] }))
  const history = Array.from({ length: 24 }, (_, i) => ({ id: 'h' + i, title: 'Finished ' + i, status: 'done' }))
  s.snapshot.root_index = [s.snapshot.active_root, ...queue, ...history]
  s.snapshot.queue = queue.slice(0, 20)
  await $.command.run({ command: 'work-mode', args: 'status' })
  const band = await $.ui.mount(BAND)
  const reads = s.calls.length
  await band.press({ key: 'work-tab-queue' })
  const found = []
  for (let page = 0; page < 5; page++) {
    for (let i = 0; i < 25; i++) if (await band.find({ key: 'queue-q' + i })) found.push(i)
    if (page < 4) await band.press({ key: 'work-next' })
  }
  expect(new Set(found).size).toBe(25)
  expect(s.calls.length).toBe(reads)
  expect(await band.find({ text: /NO SUBTREE/ })).toBeUndefined()
  await band.press({ key: 'work-tab-completed' })
  for (let i = 0; i < 4; i++) await band.press({ key: 'work-next' })
  expect(await band.find({ key: 'finished-h23' })).toBeDefined()
  // The exact-goal detail mock returns only this root; no queued nodes are fetched.
  s.snapshot.nodes = [{ id: 'h23', parent_id: null, title: 'Finished 23', status: 'done', evidence: 'archived proof' }]
  await band.press({ key: 'finished-h23' })
  expect(s.calls.at(-1).args).toEqual({ format: 'json', detail: true, root_id: 'h23', session_id: 'claude:session-a' })
  const pane = await $.ui.mount(PANE)
  expect(await pane.find({ text: /archived proof/ })).toBeDefined()
  expect(s.calls.every(c => c.tool === 'work_status')).toBe(true)
  await pane.unmount(); await band.unmount()
})
// Claude Code 2.1.311 refuses an engine node under a Box with display, overflow, position, a size
// or an offset; the AbovePrompt hook is then skipped and no Work row shows (actual CLI fixture).
const CONCEALING = ['display', 'overflow', 'position', 'width', 'height', 'minWidth', 'minHeight', 'top', 'left', 'right', 'bottom']
function enginePath(node, path = []) {
  if (!node || typeof node !== 'object') return null
  if (node.type === 'engine') return [...path, node]
  for (const child of node.children || []) {
    const found = enginePath(child, [...path, node])
    if (found) return found
  }
  return null
}
test('whole band cap follows host maxRows and viewport; only Work rows are bounded, native follows', async ($, on) => {
  const s = inspection(on)
  s.snapshot.breakdown = Array.from({ length: 20 }, (_, i) => ({ id: 'b' + i, title: 'Branch ' + i, status: 'pending', done: 0, total: 1 }))
  s.nativeTree = { type: 'Box', props: { flexDirection: 'column' }, children: Array.from({ length: 25 }, (_, i) =>
    ({ type: 'Text', props: {}, children: ['TEAM/NATIVE ' + i] })) }
  await $.command.run({ command: 'work-mode', args: 'status' })
  // [maxRows, viewport rows, Work panel rows]: a quarter of the viewport, at most ten, two kept for the rest.
  for (const [maxRows, viewportRows, expected] of [[16, 40, 8], [12, 32, 6], [8, 24, 1], [4, 12, 1], [2, 8, 0]]) {
    const band = await $.ui.mount({ ...BAND, viewport: { columns: 80, rows: viewportRows }, props: { ...BAND.props, maxRows } })
    if (expected) {
      const drawn = await band.drawn()
      expect(drawn.props.height).toBeUndefined()
      expect((await band.find({ key: 'work-panel' }))?.props.height).toBe(expected)
      expect(drawn.children.length).toBe(2)
    } else expect((await band.drawn()).children).toEqual(s.nativeTree.children)
    expect(await band.find({ text: 'TEAM/NATIVE 24' })).toBeDefined()
    await band.unmount()
  }
  const band = await $.ui.mount(BAND)
  const reads = s.calls.length
  // Paging and the inspector have letter hotkeys while the band holds the focus (never digits).
  expect((await band.find({ key: 'work-next' }))?.props.hotkey).toBe('n')
  expect((await band.find({ key: 'work-all' }))?.props.hotkey).toBe('w')
  for (const key of ['work-tab-queue', 'work-tab-completed', 'work-tab-progress', 'work-next']) {
    await band.press({ key })
    expect(await band.find({ text: 'TEAM/NATIVE 24' })).toBeDefined()
  }
  expect((await band.find({ key: 'work-prev' }))?.props.hotkey).toBe('p')
  expect(await band.find({ key: 'work-native-open' })).toBeUndefined()
  expect(s.calls.length).toBe(reads)
  await band.redraw({ ...BAND.props, hasSurvey: true })
  expect((await band.drawn()).children).toEqual(s.nativeTree.children)
  await band.unmount()
})
test('the host accepts the band around an engine-drawn native element and a companion tree', async ($, on) => {
  const s = inspection(on)
  s.snapshot.breakdown = Array.from({ length: 20 }, (_, i) => ({ id: 'b' + i, title: 'Branch ' + i, status: 'pending', done: 0, total: 1 }))
  s.nativeTree = { type: 'engine', ref: 0 }
  await $.command.run({ command: 'work-mode', args: 'status' })
  const band = await $.ui.mount(BAND)
  // A refused tree is skipped by the host: the tabs would be absent and only the engine node drawn.
  expect(await band.find({ key: 'work-tab-progress' })).toBeDefined()
  for (const key of [null, 'work-tab-queue', 'work-tab-completed', 'work-tab-progress', 'work-next']) {
    if (key) await band.press({ key })
    const path = enginePath(await band.drawn())
    expect(path).not.toBeNull()
    for (const ancestor of path.slice(0, -1)) {
      expect(Object.keys(ancestor.props || {}).filter(name => CONCEALING.includes(name))).toEqual([])
    }
  }
  await band.redraw({ ...BAND.props, hasSurvey: true })
  expect(await band.drawn()).toEqual({ type: 'engine', ref: 0 })
  await band.unmount()
  s.nativeTree = { type: 'Box', props: { flexDirection: 'column' }, children: [{ type: 'engine', ref: 0 },
    { type: 'Text', props: {}, children: ['COMPANION-ROW'] }] }
  const composed = await $.ui.mount(BAND)
  expect(await composed.find({ key: 'work-tab-progress' })).toBeDefined()
  expect(await composed.find({ text: 'COMPANION-ROW' })).toBeDefined()
  expect(enginePath(await composed.drawn())).not.toBeNull()
  await composed.unmount()
})
test('tiny band opens inspector with all tab lists and exact details on demand', async ($, on) => {
  const s = inspection(on)
  s.snapshot.queue = Array.from({ length: 13 }, (_, i) => ({ id: 'q' + i, title: 'Queued ' + i, status: 'pending' }))
  await $.command.run({ command: 'work-mode', args: 'status' })
  const band = await $.ui.mount({ ...BAND, viewport: { columns: 42, rows: 20 }, props: { ...BAND.props, bodyColumns: 42, maxRows: 8 } })
  expect(await band.find({ key: 'work-tab-progress' })).toBeUndefined()
  expect(await band.find({ key: 'work-all' })).toBeDefined()
  await band.press({ key: 'work-all' })
  const pane = await $.ui.mount(PANE)
  const reads = s.calls.length
  await pane.press({ key: 'detail-tab-queue' })
  expect(await pane.find({ key: 'detail-queue-q0' })).toBeDefined()
  await pane.press({ key: 'detail-next' })
  expect(await pane.find({ key: 'detail-queue-q12' })).toBeDefined()
  expect(s.calls.length).toBe(reads)
  await pane.press({ key: 'detail-prev' })
  expect(await pane.find({ key: 'detail-queue-q0' })).toBeDefined()
  await pane.unmount(); await band.unmount()
})
test('tab and paging state resets at conversation change and end; no work mutations', async ($, on) => {
  const s = inspection(on)
  s.snapshot.queue = Array.from({ length: 20 }, (_, i) => ({ id: 'q' + i, title: 'Queued ' + i, status: 'pending' }))
  await $.command.run({ command: 'work-mode', args: 'status' })
  const band = await $.ui.mount(BAND)
  await band.press({ key: 'work-tab-queue' }); await band.press({ key: 'work-next' })
  s.sessionId = 'session-b'
  // Before a new snapshot lands, the old session tree is not drawn.
  await band.redraw(BAND.props)
  expect(await band.find({ key: 'queue-q5' })).toBeUndefined()
  await $.command.run({ command: 'work-mode', args: 'status' })
  expect((await band.find({ key: 'work-tab-progress' }))?.text).toContain('[Progress')
  expect(await band.find({ key: 'branch-a' })).toBeDefined()
  await band.press({ key: 'work-tab-queue' })
  await $.session.end({ reason: 'clear' })
  await $.command.run({ command: 'work-mode', args: 'status' })
  expect((await band.find({ key: 'work-tab-progress' }))?.text).toContain('[Progress')
  expect(s.tools.length).toBe(0)
  expect(s.calls.every(c => c.tool === 'work_status')).toBe(true)
  await band.unmount()
})
test('inspector bounds long notes and trees with paging; every note chunk stays reachable', async ($, on) => {
  const s = inspection(on)
  s.snapshot.nodes = [{ id: 'g1', parent_id: null, title: 'Goal', status: 'in_progress', note: 'x'.repeat(1950) + 'END OF NOTE' },
    ...Array.from({ length: 40 }, (_, i) => ({ id: 'leaf' + i, parent_id: 'g1', title: 'Leaf ' + i, status: 'pending' }))]
  await $.command.run({ command: 'work-mode', args: 'view' })
  const pane = await $.ui.mount(PANE)
  let end = false; let last = false; let pages = 0
  do {
    if (await pane.find({ text: /END OF NOTE/ })) end = true
    if (await pane.find({ key: 'node-leaf39' })) last = true
    expect((await pane.findAll({ type: 'Button' })).length <= 17).toBe(true)
    pages++
    if (!await pane.find({ key: 'detail-next' })) break
    await pane.press({ key: 'detail-next' })
  } while (pages < 20)
  expect(end).toBe(true); expect(last).toBe(true)
  expect(pages < 20).toBe(true)
  await pane.unmount()
})
test('exact wide-character detail is paged without losing text and snapshot shrink clamps pages', async ($, on) => {
  const s = inspection(on)
  const note = '한글'.repeat(100) + '\n마지막 줄'
  s.snapshot.nodes[1].note = note
  s.snapshot.breakdown = Array.from({ length: 22 }, (_, i) => ({ id: 'b' + i, title: 'Branch ' + i, status: 'pending' }))
  await $.command.run({ command: 'work-mode', args: 'status' })
  const band = await $.ui.mount(BAND)
  for (let i = 0; i < 4; i++) await band.press({ key: 'work-next' })
  s.snapshot.version++; s.snapshot.breakdown = [{ id: 'a', title: 'Cache', status: 'in_progress' }]
  await $.command.run({ command: 'work-mode', args: 'status' })
  expect(await band.find({ key: 'branch-a' })).toBeDefined()
  expect(await band.find({ key: 'work-next' })).toBeUndefined()
  await band.press({ key: 'branch-a' })
  const pane = await $.ui.mount({ ...PANE, props: { ...PANE.props, bodyColumns: 30 } })
  let captured = ''
  for (let i = 0; i < 12; i++) {
    for (const row of await pane.findAll({ type: 'Text' })) {
      if (row.text.startsWith('Note:') || /한|글|마지막/.test(row.text)) captured += row.text
    }
    if (!await pane.find({ key: 'detail-next' })) break
    await pane.press({ key: 'detail-next' })
  }
  expect(captured).toBe('Note: ' + note.replace('\n', ''))
  await pane.unmount(); await band.unmount()
})

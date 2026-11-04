// Drives the packaged ciel-work register.js with a scripted host: no Claude Code,
// no router. Each scenario prints one JSON line describing the gate decisions.
import { register } from './register.js'

const handlers = {}
register((name, filter, handler) => { handlers[name] = typeof filter === 'function' ? filter : handler })

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const snapshot = (phase = 'executing', version = 5) => ({ enabled: true, phase, approval: 'auto', version,
  done: 0, total: 1, active_root: { id: 'n1', title: 'goal', status: 'in_progress' }, auto_continue: true,
  continuation: { actionable: 0, waiting: [] }, queue: [] })
const text = value => ({ content: [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value) }] })

function host(respond) {
  const state = { session: 'S1', calls: 0, logs: [] }
  const $ = {
    session: { id: async () => state.session },
    mcp: { call: async (server, tool, args) => { state.calls++; return respond(tool, args, state) } },
    ui: { invalidate() {}, toast() {}, log(t) { state.logs.push(t) }, open: async () => {}, ask: async () => 'Approve' },
    clock: { after() { return { cancel() {} } } },
    env: { get: async () => 'host-token' },
    agent: { list: async () => state.agents || [] },
    command: { register: async () => {} },
  }
  return { $, state }
}
function next(value) {
  const fn = async () => value
  fn.origin = { plugin: 'engine' }
  return fn
}
const check = ($, agentId) => handlers['tool.check']($, { tool: 'Edit', agentId,
  input: { file_path: 'C:/w/a.py', old_string: 'a', new_string: 'b' } }, next({ decision: 'allow' }))
const call = ($, agentId) => handlers['tool.call']($, { tool: 'Edit', agentId,
  file_path: 'C:/w/a.py', old_string: 'a', new_string: 'b' }, next({ ran: true }))
const verdict = r => r?.decision === 'deny' ? 'deny: ' + r.reason : r?.deny ? 'deny: ' + r.deny : 'allow'

const scenarios = {
  // One explicit assignment row per fork: item id/title/path, the exact Agent
  // name, host id once bound, lease state and slot.
  async 'parallel-assignment-labels'() {
    const board = { ...snapshot(), nodes: [
      { id: 'n1', parent_id: null, title: 'Product goal', ref: 'PRD', status: 'in_progress' },
      { id: 'n2', parent_id: 'n1', title: 'Checkout phase', status: 'in_progress' },
      { id: 'n227', parent_id: 'n2', title: 'Fix payment retry', ref: 'PAY-227', status: 'pending' },
      { id: 'n229', parent_id: 'n2', title: 'Add retry backoff', ref: 'PAY-229', status: 'pending' },
    ], active_root: { id: 'n1', title: 'Product goal', status: 'in_progress' } }
    const leases = {
      L1: { id: 'L1', item_id: 'n227', item_title: 'Fix payment retry', item_ref: 'PAY-227',
        goal_title: 'Product goal', item_path: ['Product goal', 'Checkout phase', 'Fix payment retry'],
        priority: 'P1', role: 'worker', state: 'running', slot: true, agent_name: 'work-alpha', host_id: 'host-1', plan_version: 7 },
      L2: { id: 'L2', item_id: 'n228', priority: 'P2', role: 'reviewer', state: 'reserved', slot: true, plan_version: 7 },
    }
    const parallel = { version: 12, active_device: 2,
      policy: { enabled: true, paused: false, max_active: 3, p1_reserved: 1, launches_per_minute: 4, max_rework: 2 }, leases }
    const { $ } = host(async (tool, args) => {
      if (tool === 'work_parallel_status') return text(parallel)
      if (tool === 'work_status' && args.detail) return text({ ...board, version: 5, root_id: 'n1', nodes: board.nodes })
      if (tool === 'work_status') return text(board)
      if (tool === 'work_parallel_event' && args.event === 'reserve') {
        return text({ lease_id: 'L9', version: 13, plan_version: 5,
          item_title: 'Add retry backoff', item_ref: 'PAY-229', goal_id: 'n1', goal_title: 'Product goal',
          item_path: ['Product goal', 'Checkout phase', 'Add retry backoff'] })
      }
      if (tool === 'work_parallel_event' && args.event === 'bind') {
        leases.L9 = { id: 'L9', item_id: 'n229', item_title: 'Add retry backoff', item_ref: 'PAY-229',
          goal_title: 'Product goal', item_path: ['Product goal', 'Checkout phase', 'Add retry backoff'],
          priority: 'P1', role: 'worker', state: 'running', slot: true,
          agent_name: args._host?.agent_name, host_id: args._host?.host_id, plan_version: 5 }
        return text({ version: 14, leases })
      }
      return text(board)
    })
    $.session.repo = async () => null
    $.session.root = async () => '/repo'
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    // L1 already holds the n227 worker, so the dispatch under test targets the
    // next leaf. Re-dispatching n227 here would hit the resume-instead guard.
    const run = await handlers['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_run',
      item_id: 'n229', priority: 'P1', role: 'worker', expected_version: 12 }, next({}))
    if (run.deny || typeof run.result !== 'string') throw new Error('dispatch did not return a result: ' + JSON.stringify(run))
    const dispatch = JSON.parse(run.result)
    let spawnedEvent = null
    const captureSpawn = async (e) => { spawnedEvent = e; return { agentId: 'host-9', model: 'fixture' } }
    captureSpawn.origin = { plugin: 'engine' }
    const spawned = await handlers['agent.spawn']($, { tool_use_id: 'spawn-1', prompt: dispatch.prompt,
      description: dispatch.description, subagentType: 'fork', fork: true, background: true, name: dispatch.name }, captureSpawn)
    const shown = await handlers['command.run']($, { args: 'parallel' })
    return { spawned, spawnedEvent, dispatch, shown: shown.text || '' }
  },
  // Mirrors Kobi 2026-11-04: work_parallel_run returned a name, but the model
  // called Agent without it. The gate must name that exact omission, and the
  // user-facing parallel view must expose the pending name without a router read.
  async 'parallel-missing-agent-name'() {
    const parallel = { version: 11, active_device: 0,
      policy: { enabled: true, paused: false, max_active: 3, p1_reserved: 1, launches_per_minute: 4, max_rework: 2 }, leases: {} }
    const { $ } = host(async (tool) => tool === 'work_parallel_status' ? text(parallel) : text(snapshot()))
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    const runRaw = await handlers['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_run',
      item_id: 'n227', priority: 'P1', role: 'worker', expected_version: 11 }, next({}))
    if (runRaw.deny || typeof runRaw.result !== 'string') throw new Error('dispatch did not return a result: ' + JSON.stringify(runRaw))
    const run = runRaw
    const dispatch = JSON.parse(run.result)
    const denied = await handlers['agent.spawn']($, { tool_use_id: 'call-agent', prompt: dispatch.prompt,
      description: dispatch.description, subagentType: 'fork', fork: true, background: true }, next({ agentId: 'must-not-start' }))
    const shown = await handlers['command.run']($, { args: 'parallel' })
    const note = await handlers['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_event',
      item_id: 'n227', plan_version: 305, event: 'Native fork dispatch failed' }, next({}))
    return { dispatch, denied: verdict(denied), shown: shown.text || '', note: verdict(note) }
  },
  async 'parallel-auto-review'() {
    const lease = { id: 'L1', item_id: 'n2', role: 'worker', state: 'submitted', host_id: 'agent-old', slot: true, plan_version: 7, artifact: 'a'.repeat(64), evidence: 'tested' }
    const { $, state } = host(async (tool, args) => {
      if (tool === 'work_status') return text(snapshot())
      if (tool === 'work_parallel_status') return text({ version: 9, policy: { enabled: true }, leases: { L1: lease } })
      if (args.event === 'reconcile') { lease.slot = false; return text({}) }
      return text({})
    })
    state.agents = [{ id: 'agent-old', status: 'completed' }]
    $.agent.spawn = async (args) => { state.spawned = args; return { agentId: 'reviewer' } }
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    await handlers['turn.complete']($, {}, next(undefined))
    return { spawned: state.spawned, released: !lease.slot }
  },
  async 'parallel-recovery-context'() {
    const lease = { id: 'L1', item_id: 'n2', role: 'worker', state: 'running', host_id: 'agent-old', slot: true, plan_version: 7 }
    const { $, state } = host(async (tool) => tool === 'work_parallel_status'
      ? text({ version: 9, policy: { enabled: true }, leases: { L1: lease } }) : text(snapshot()))
    state.agents = []
    let seen = null
    const capture = async (e) => { seen = e; return e }
    capture.origin = { plugin: 'engine' }
    await handlers['prompt.submit']($, { context: [] }, capture)
    state.agents = [{ id: 'agent-old', status: 'running' }]
    let live = null
    const captureLive = async (e) => { live = e; return e }
    captureLive.origin = { plugin: 'engine' }
    await handlers['prompt.submit']($, { context: [] }, captureLive)
    const duplicate = await handlers['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_run',
      item_id: 'n2', priority: 'P1', role: 'worker' }, next({}))
    return { context: JSON.stringify(seen), live: JSON.stringify(live), duplicate: verdict(duplicate) }
  },
  // A parent restart has no in-memory map; the durable lease alone restores worker identity.
  async 'parallel-restart-rebind'() {
    const lease = { id: 'L1', role: 'worker', state: 'running', host_id: 'agent-old', slot: true, plan_version: 7 }
    const { $, state } = host(async (tool, args) => {
      if (tool === 'work_status') return text(snapshot())
      if (tool === 'work_parallel_status') return text({ version: 9, policy: { enabled: true }, leases: { L1: lease } })
      if (tool === 'work_parallel_event') { state.event = args; return text({ version: 10, leases: {} }) }
      return text({})
    })
    state.agents = []
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    const result = await handlers['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_event',
      agentId: 'agent-old', event: 'result', lease_id: 'L1', plan_version: 7, artifact: 'a'.repeat(64), evidence: 'r' }, next({}))
    await handlers['turn.complete']($, {}, next(undefined))
    return { result: verdict(result), event: state.event }
  },
  async 'parallel-session-start-releases-orphan-reserved'() {
    const leases = {
      L1: { id: 'L1', item_id: 'n227', priority: 'P1', role: 'worker', state: 'reserved', slot: true, host_id: null, plan_version: 5 },
      L2: { id: 'L2', item_id: 'n228', priority: 'P1', role: 'worker', state: 'running', slot: true, host_id: 'agent-live', plan_version: 5 },
    }
    const calls = []
    const { $, state } = host(async (tool, args) => {
      if (tool === 'work_parallel_status') return text({ version: 12, policy: { enabled: true }, leases })
      if (tool === 'work_parallel_event') {
        calls.push(args)
        if (args.event === 'release') { delete leases.L1; return text({ version: 13, leases }) }
        return text({ version: 12, leases })
      }
      return text(snapshot())
    })
    state.agents = [{ id: 'agent-live', status: 'running' }]
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    return { calls, leases }
  },
  async 'parallel-spawn-denied-releases-unbound'() {
    const parallel = { version: 11, active_device: 0,
      policy: { enabled: true, paused: false, max_active: 3, p1_reserved: 1, launches_per_minute: 4, max_rework: 2 }, leases: {} }
    const calls = []
    const { $ } = host(async (tool, args) => {
      if (tool === 'work_parallel_status') return text(parallel)
      if (tool === 'work_parallel_event') {
        calls.push(args)
        if (args.event === 'reserve') return text({ lease_id: 'L9', version: 12, plan_version: 5 })
        if (args.event === 'release') return text({ lease_id: 'L9', version: 13, released: true, item_id: 'n227', item_kept: true })
      }
      return text(snapshot())
    })
    $.session.repo = async () => null
    $.session.root = async () => '/repo'
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    const run = await handlers['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_run',
      item_id: 'n227', priority: 'P1', role: 'worker', expected_version: 11 }, next({}))
    const dispatch = JSON.parse(run.result)
    const deniedNext = async () => ({ deny: 'Subagent spawn denied by fixture' })
    deniedNext.origin = { plugin: 'engine' }
    const denied = await handlers['agent.spawn']($, { tool_use_id: 'spawn-2', prompt: dispatch.prompt,
      description: dispatch.description, subagentType: 'fork', fork: true, background: true, name: dispatch.name }, deniedNext)
    return { denied: verdict(denied), calls }
  },
  async 'stop-pause-discovery'() {
    const ready = { ...snapshot(), focus: { node_ids: [] },
      continuation: { actionable: 1, waiting: [], blocked: 0, held: 0, next: { id: 'n240', title: 'next item', root_id: 'n1', phase: 'executing' } },
      discovery: { enabled: true, instruction: '이 세션의 검색 수단으로 미결 아이템을 확인하라. 기존 승인 유지.' } }
    const { $ } = host(async () => text(ready))
    await handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    await handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    const third = await handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    return { third: third?.block || '' }
  },
  async 'stop-discovery-approval'() {
    const ready = { ...snapshot('planning'), approval: 'ask', ready: true,
      discovery: { enabled: true, instruction: '와키 미결 아이템: 우선순위, 최신순. 기존 승인 유지.' } }
    const { $ } = host(async () => text(ready))
    const first = await handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    const second = await handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    return { first: first?.block || '', second: Boolean(second?.block) }
  },
  async 'stop-focus-unrunnable'() {
    // Focused items wait, but another approved goal has runnable steps: continue there.
    const ready = { ...snapshot(), focus: { node_ids: ['n1'], exhausted: false },
      continuation: { actionable: 2, waiting: [{ id: 'n1', title: 'goal', waiting_on: 'approval' }], blocked: 0, held: 0,
        next: { id: 'n5', title: 'other step', root_id: 'n3', phase: 'executing' } },
      queue: [{ id: 'n3', title: 'other goal', status: 'pending', done: 0, total: 2 }] }
    const { $ } = host(async () => text(ready))
    const result = await handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    return { text: result?.block || '' }
  },
  async 'stop-discovery-focus'() {
    // Nothing is runnable and focus is set: discovery still runs.
    const ready = { ...snapshot(), focus: { node_ids: ['n1'], exhausted: false },
      discovery: { enabled: true, instruction: '와키 미결 아이템: 우선순위, 최신순. 기존 승인 유지.' } }
    const { $ } = host(async () => text(ready))
    const result = await handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    return { text: result?.block || '' }
  },
  async 'stop-discovery-blocked'() {
    const ready = { ...snapshot(), discovery: { enabled: true,
      instruction: '와키 미결 아이템: 우선순위, 최신순. 기존 블록 유지.' } }
    const { $ } = host(async () => text(ready))
    const result = await handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    return { text: result?.block || '' }
  },
  // Two agents gate their own Edit while the router answers the earlier read last.
  async 'concurrent-check'() {
    const delays = [60, 10]
    const { $ } = host(async () => { await sleep(delays.shift() ?? 0); return text(snapshot()) })
    const first = check($, 'agent-a')
    await sleep(10)
    const second = check($, 'agent-b')
    return { first: verdict(await first), second: verdict(await second) }
  },
  async 'concurrent-call'() {
    const delays = [60, 10]
    const { $ } = host(async () => { await sleep(delays.shift() ?? 0); return text(snapshot()) })
    const first = call($, 'agent-a')
    await sleep(10)
    const second = call($, undefined)
    return { first: verdict(await first), second: verdict(await second) }
  },
  // A main-thread turn starting while a subagent's gate read is in flight.
  async 'turn-start-during-check'() {
    const { $ } = host(async () => { await sleep(40); return text(snapshot()) })
    const pending = check($, 'agent-a')
    await sleep(10)
    await handlers['turn.start']($, {}, next(undefined))
    return { first: verdict(await pending) }
  },
  async 'concurrent-planning'() {
    const delays = [60, 10]
    const { $ } = host(async () => { await sleep(delays.shift() ?? 0); return text(snapshot('planning')) })
    const first = check($, 'agent-a')
    await sleep(10)
    const second = check($, 'agent-b')
    return { first: verdict(await first), second: verdict(await second) }
  },
  // Each gate stands on its own read: the failed one still denies, the verified one allows.
  async 'concurrent-mixed'() {
    const plan = [[60, null], [10, snapshot()]]
    const { $ } = host(async () => {
      const [delay, value] = plan.shift(); await sleep(delay)
      return value ? text(value) : { isError: true, content: [{ type: 'text', text: 'store unreadable' }] }
    })
    const first = check($, 'agent-a')
    await sleep(10)
    const second = check($, 'agent-b')
    return { first: verdict(await first), second: verdict(await second) }
  },
  async 'execute-during-edit'() {
    const delays = [60, 10]
    const { $ } = host(async (tool) => {
      if (tool === 'work_execute') return text('ok')
      await sleep(delays.shift() ?? 0); return text(snapshot('planning'))
    })
    const execute = handlers['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_execute' }, next({ ran: true }))
    await sleep(10)
    const edit = check($, 'agent-b')
    return { first: verdict(await execute), second: verdict(await edit) }
  },
  async 'planning'() {
    const { $ } = host(async () => text(snapshot('planning')))
    return { first: verdict(await check($, 'agent-a')) }
  },
  async 'session-end-during-check'() {
    const { $ } = host(async () => { await sleep(40); return text(snapshot()) })
    const pending = check($, 'agent-a')
    await sleep(10)
    await handlers['session.end']($, {}, next(undefined))
    return { first: verdict(await pending) }
  },
  async 'stale-snapshot'() {
    const { $ } = host(async () => text(snapshot('executing', 4)))
    const { $: newer } = host(async () => text(snapshot('planning', 5)))
    await check(newer, 'agent-b')
    return { first: verdict(await check($, 'agent-a')) }
  },
  async 'router-timeout'() {
    const { $, state } = host(async () => { throw new Error('Timeout: PRIVATE-TOKEN') })
    return { first: verdict(await check($, 'agent-a')), logs: state.logs }
  },
  async 'router-error'() {
    const { $ } = host(async () => ({ isError: true, content: [{ type: 'text', text: 'boom' }] }))
    return { first: verdict(await check($, 'agent-a')) }
  },
  async 'router-throws'() {
    const { $ } = host(async () => { throw new Error('no connected MCP tool') })
    return { first: verdict(await call($, 'agent-a')) }
  },
  async 'invalid-snapshot'() {
    const { $ } = host(async () => text({ enabled: true, phase: 'executing' }))
    return { first: verdict(await check($, 'agent-a')) }
  },
  async 'not-json'() {
    const { $ } = host(async () => text('Work phase: executing'))
    return { first: verdict(await check($, 'agent-a')) }
  },
  // The conversation changes while the read is in flight: the gate must not use it.
  async 'identity-change'() {
    const { $, state } = host(async () => { state.session = 'S2'; return text(snapshot()) })
    return { first: verdict(await check($, 'agent-a')) }
  },
  async 'disabled'() {
    const { $ } = host(async () => text({ enabled: false }))
    return { first: verdict(await check($, 'agent-a')) }
  },
  // A Stop whose read is overtaken by a new main turn must not continue the old turn.
  async 'stop-superseded'() {
    const ready = { ...snapshot(), continuation: { actionable: 1, next: { id: 'n2', title: 'x', root_id: 'n1' }, waiting: [] } }
    const { $ } = host(async () => { await sleep(40); return text(ready) })
    const pending = handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    await sleep(10)
    await handlers['turn.start']($, {}, next(undefined))
    const result = await pending
    return { block: Boolean(result?.block) }
  },
  // A UI/inspector refresh is not a new model turn and must not cancel Stop.
  async 'stop-display-refresh'() {
    const ready = { ...snapshot(), continuation: { actionable: 1, next: { id: 'n3', title: 'queued', root_id: 'n2', phase: 'executing' }, waiting: [] } }
    const delays = [60, 5]
    const { $, state } = host(async () => { await sleep(delays.shift() ?? 0); return text(ready) })
    const pending = handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    await sleep(10)
    await handlers['command.run']($, { args: 'status' })
    const result = await pending
    return { block: Boolean(result?.block), text: result?.block || '', logs: state.logs }
  },
  async 'stop-session-end'() {
    const ready = { ...snapshot(), continuation: { actionable: 1, next: { id: 'n2', title: 'x', root_id: 'n1' }, waiting: [] } }
    const { $ } = host(async () => { await sleep(40); return text(ready) })
    const pending = handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    await sleep(10)
    await handlers['session.end']($, {}, next(undefined))
    return { block: Boolean((await pending)?.block) }
  },
  async 'stop-continues'() {
    const ready = { ...snapshot(), continuation: { actionable: 1, next: { id: 'n2', title: 'x', root_id: 'n1' }, waiting: [] } }
    const { $ } = host(async () => text(ready))
    const result = await handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    return { block: Boolean(result?.block) }
  },
}

const name = process.argv[2]
process.stdout.write(JSON.stringify({ scenario: name, ...(await scenarios[name]()) }) + '\n')

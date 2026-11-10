// Drives the packaged ciel-work register.js with a scripted host: no Claude Code,
// no router. Each scenario prints one JSON line describing the gate decisions.
import { register } from './register.js'
import { atom, read, update } from 'claude-code'

const handlers = {}
const sideHandlers = {}
register((name, filter, handler) => {
  if (filter?.command === 'btw_with_mcp' || filter?.requestId === 'ciel-side-intake') sideHandlers[name] = handler
  else handlers[name] = typeof filter === 'function' ? filter : handler
})
// The engine's claude-code helpers are stubbed by the pytest fixture: atoms
// live in a shared map keyed by plugin+key. Probe the detail pane state there.
const detailAtom = atom({ plugin: 'ciel-work', key: 'detail' }, null)
const readDetailAtom = $ => read($, detailAtom)
const writeDetailAtom = async ($, v) => update($, detailAtom, () => v)

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const snapshot = (phase = 'executing', version = 5) => ({ enabled: true, phase, approval: 'auto', version,
  done: 0, total: 1, active_root: { id: 'n1', title: 'goal', status: 'in_progress' }, auto_continue: true,
  continuation: { actionable: 0, waiting: [] }, queue: [] })
const text = value => ({ content: [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value) }] })

function host(respond) {
  const state = { session: 'S1', calls: 0, logs: [] }
  const $ = {
    session: { id: async () => state.session, model: async () => 'fixture-main' },
    mcp: { connect: async () => ({}), call: async (server, tool, args) => { state.calls++; return respond(tool, args, state) } },
    ui: { invalidate() {}, toast() {}, log(t) { state.logs.push(t) }, open: async () => {}, ask: async () => 'Approve' },
    clock: { after() { return { cancel() {} } } },
    env: { get: async () => 'host-token' },
    tool: { list: async () => [] },
    agent: { list: async () => state.agents || [], register: async () => ({}),
      spawn: async args => { if (args.subagentType !== 'ciel-work:side-intake') return {deny:'fixture'}; state.sideSpawn = args; return {agentId:'side1'} } },
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
  async 'side-receipt-ready-is-consumed-and-claims-now'() {
    let claims = 0
    const { $, state } = host(async (tool) => {
      if (tool === 'work_status') return text(snapshot())
      if (tool === 'work_parallel_status') return text({ version:1, policy:{enabled:false}, leases:{} })
      if (tool === 'work_side_claim') { claims++; return text({ claim:null, settings:{}, handoff_replies:[], main_wakes:[] }) }
      if (tool === 'work_side_status') return text({ side_inbox_receipts:[] })
      return text({})
    })
    await handlers['session.start']($, { isInteractive:false }, next({cwd:'C:/w'}))
    const before = claims
    // Owned Claude Code 2.1.321 probe (2026-11-10): the socket frame reaches
    // session.receive with origin peer, whether main is idle or busy.
    const ready = await handlers['session.receive']($, { origin:{kind:'peer'},
      text:'[ciel-side-receipt-ready] '+'a'.repeat(64) }, next({text:'leaked'}))
    await sleep(20)
    const malformed = await handlers['session.receive']($, { origin:{kind:'task-notification'},
      text:'[ciel-side-receipt-ready] not-a-receipt' }, next({text:'passed'}))
    const agentTarget = await handlers['session.receive']($, { origin:{kind:'task-notification'}, agentId:'side1',
      text:'[ciel-side-receipt-ready] '+'b'.repeat(64) }, next({text:'agent-passed'}))
    const afterReceive = claims
    // The session socket's user frame reaches prompt.submit as a peer prompt.
    const submitted = await handlers['prompt.submit']($, { origin:{kind:'peer'},
      text:'[ciel-side-receipt-ready] '+'c'.repeat(64), context:[] }, next({text:'leaked-to-main'}))
    await sleep(20)
    const typed = await handlers['prompt.submit']($, { origin:{kind:'composer'},
      text:'[ciel-side-receipt-ready] '+'d'.repeat(64), context:[] }, next({text:'typed-passes'}))
    return { before, after:afterReceive, afterSubmit:claims, ready, malformed, agentTarget, submitted, typed }
  },
  async 'side-status-single-flight-and-throttled'() {
    let statusCalls = 0
    let release
    const gate = new Promise(resolve => { release = resolve })
    const timers = []
    const { $ } = host(async tool => {
      if (tool === 'work_status') return text({ ...snapshot(), side_intake_enabled: true })
      if (tool === 'work_side_claim') return text({ claim: null, settings: {} })
      if (tool === 'work_side_status') { statusCalls++; if (statusCalls === 1) await gate; return text({ enabled: true, side_inbox_receipts: [] }) }
      return text({})
    })
    $.clock.after = (delay, callback) => { timers.push({ delay, callback }); return { cancel() {} } }
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    const tick = async () => { const timer = timers.filter(t => t.delay === 2000).at(-1); await timer.callback(); await sleep(10) }
    await tick()                       // first refresh starts and hangs
    await tick()                       // still in flight: no second fetch
    const whileInFlight = statusCalls
    const ticksWhileInFlight = timers.filter(t => t.delay === 2000).length
    release(); await sleep(10)
    await tick()                       // nothing changed within 15 s: throttled
    const afterThrottle = statusCalls
    await handlers['session.receive']($, { origin: { kind: 'peer' }, text: '[ciel-side-receipt-ready] ' + 'e'.repeat(64) }, next({}))
    await sleep(10)
    await tick()                       // a receipt arrived: refreshed at once
    return { whileInFlight, ticksWhileInFlight, afterThrottle, afterSignal: statusCalls }
  },
  async 'side-pump-drains-burst-into-free-slots'() {
    // Three events wait. One tick must start all that fit (max_active 2 plus
    // the P1 reserve) instead of one event per 2 s tick.
    const queue = ['e1', 'e2', 'e3', 'e4'].map(id => id.padEnd(64, '0'))
    const claims = []
    const spawned = []
    const timers = []
    const { $ } = host(async (tool, args) => {
      if (tool === 'work_status') return text({ ...snapshot(), side_intake_enabled: true })
      if (tool === 'work_side_status') return text({ enabled: true, side_inbox_receipts: [] })
      if (tool === 'work_side_claim') {
        claims.push({ urgent_only: args.urgent_only === true })
        const id = queue.shift()
        return text(id ? { claim: { receipt_id: id, generation: 'g-' + id.slice(0, 2), text: 'event ' + id.slice(0, 2), route: {},
          lease_until: Date.now() / 1000 + 600, workload: { version: 5, truncated: false, nodes: [], queue_order: [] } },
          settings: { max_active: 2 } } : { claim: null, settings: { max_active: 2 } })
      }
      return text({})
    })
    $.clock.after = (delay, callback) => { timers.push({ delay, callback }); return { cancel() {} } }
    $.agent.spawn = async args => { const agentId = 'side-' + (spawned.length + 1); spawned.push(args.description); return { agentId } }
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    const timer = timers.find(t => t.delay === 2000)
    if (!timer) throw new Error('side pump not scheduled')
    await timer.callback()
    return { claims, spawned, left: queue.length }
  },
  async 'side-advice-stop-queued-wake'() {
    const {$}=host(async()=>text({...snapshot(),auto_continue:false,side_handoffs:[{event_id:'queued',item_id:'n2',kind:'resume',summary:'S',question:'',
      waits:[],wake:{state:'queued'}}]}))
    return await handlers['classic.Stop']($,{session_id:'S1'},next({}))
  },
  async 'side-handoff-conflict'() {
    const conflict='the plan changed (stored version 1688, expected 1685); call work_status and retry'
    const { $, state } = host(async (tool, args) => {
      if (tool === 'work_side_handoff') { state.handoff = args; return { isError: true, ...text(conflict) } }
      return text(snapshot())
    })
    const result = await handlers['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_side_handoff',
      tool_use_id: 't1', event_id: 'e1', expected_version: 1685, answer: 'stale' }, next({ ran: true }))
    return { result, sent: state.handoff }
  },
  async 'side-review-stop-once'() {
    const reviews = [{ receipt_id: 'r'.repeat(64), source: 'message:c:119649', kind: 'status', summary: 'queue listed', reply: 'queue text' }]
    const sent = []
    const { $ } = host(async (tool, args) => {
      if (tool === 'work_side_review') { sent.push(args); return text({ version: 9, reviews: [] }) }
      return text({ ...snapshot(), auto_continue: false, side_reviews: reviews })
    })
    const first = await handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    const second = await handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    const reviewed = await handlers['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_side_review',
      receipt_ids: [reviews[0].receipt_id], verdict: 'ok', expected_version: 8 }, next({ ran: true }))
    return { first, second, reviewed, sent }
  },
  async 'side-review-delivery-states'() {
    const row = (id, delivery) => ({ receipt_id: id.repeat(64), source: 'message:c:' + id, kind: 'info', summary: 'repo check ' + id,
      reply: 'answer ' + id, ...(delivery ? { delivery } : {}) })
    let reviews = [row('a', 'submitted'), row('b', 'route_unavailable'), row('c', 'submitting')]
    const { $ } = host(async () => text({ ...snapshot(), auto_continue: false, side_reviews: reviews }))
    const first = await handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    const repeat = await handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    reviews = [row('c', 'failed')]
    const failedLater = await handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    return { first, repeat, failedLater }
  },
  async 'side-advice-stop'() {
    const {$}=host(async()=>text({...snapshot(),auto_continue:false,side_handoffs:[{event_id:'event',item_id:'n2',kind:'resume',summary:'P1 deadline',question:'Need decision',
      waits:[{id:'n3',waiting_on:'Daniel approval 118269'}],gates:[{id:'n3',waiting_on:'Daniel approval 118269',blocked:false}],wake:{state:'pending',requested_at:1}}]}))
    const first = await handlers['classic.Stop']($,{session_id:'S1'},next({}))
    const second = await handlers['classic.Stop']($,{session_id:'S1'},next({}))
    return {...first,second}
  },
  async 'side-notification-isolation'() {
    const { $, state } = host(async tool => tool === 'work_status'
      ? text({ ...snapshot(), side_intake_enabled: true })
      : tool === 'work_side_status' ? text({ enabled: true, side_inbox_receipts: [] }) : text({}))
    state.agents = [{ id:'owned-side', spawnedBy:'ciel-work', type:'fork', status:'completed', description:'Side SSE abcdef123456' }]
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    // startSide retains a bounded tombstone after stopping this prior side actor,
    // so its delayed TaskStop notification is still consumed.
    const owned = await handlers['session.receive']($, { origin:{kind:'task-notification'}, text:'<task-notification><task-id>owned-side</task-id></task-notification>' }, next({text:'passed'}))
    const foreign = await handlers['session.receive']($, { origin:{kind:'task-notification'}, text:'<task-notification><task-id>foreign</task-id></task-notification>' }, next({text:'passed'}))
    const quoted = await handlers['session.receive']($, { origin:{kind:'peer'}, text:'owned-side' }, next({text:'passed'}))
    return {owned,foreign,quoted}
  },
  async 'side-mailbox-timeout-rearms'() {
    const timers = []
    const { $ } = host(async tool => {
      if (tool === 'work_status') return text({ ...snapshot(), side_intake_enabled: true })
      if (tool === 'work_side_status') return new Promise(() => {})
      return text({})
    })
    const notices = []
    $.ui.status = text => notices.push(text)
    $.clock.after = (delay, callback) => { timers.push({ delay, callback }); return { cancel() {} } }
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    await timers.find(t => t.delay === 2000).callback()
    return { notices, rearmed: timers.filter(t => t.delay === 2000).length }
  },
  async 'side-mailbox-compact-recovery'() {
    const timers = []
    let drains = 0
    const { $ } = host(async tool => {
      if (tool === 'work_status') return text({ ...snapshot(), side_intake_enabled: true })
      if (tool === 'work_side_status') return text({ enabled: true, side_inbox_receipts: [] })
      if (tool === 'work_side_claim') { drains++; return text({claim:null}) }
      return text({})
    })
    $.clock.after = (delay, callback) => { timers.push({ delay, callback }); return { cancel() {} } }
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    let settle
    const compact = handlers['session.compact']($, { trigger: 'manual' },
      async () => new Promise((resolve, reject) => { settle = reject }))
    while (!settle) await Promise.resolve()
    await timers.find(t => t.delay === 2000).callback()
    const during = drains
    settle(new Error('compact fixture failure'))
    try { await compact } catch {}
    await timers.filter(t => t.delay === 2000)[1].callback()
    return { during, after: drains }
  },
  async 'side-mailbox-mod-drain'() {
    const timers = []
    const tools = []
    let statusFinished = false
    let claimed = false
    const { $, state } = host(async tool => {
      tools.push(tool)
      if (tool === 'work_status') return text({ ...snapshot(), side_intake_enabled: true })
      if (tool === 'work_side_status') { statusFinished = true; return text({ enabled: true, side_inbox_receipts: [
        { receipt_id: 'mail1', stage: 'received', recap: 'original request', item_id: null }] }) }
      // The inbox hands an event out once; a claimed event is processing.
      if (tool === 'work_side_claim') {
        if (claimed) return text({claim:null})
        claimed = true
        return text({claim:{receipt_id:'mail1',generation:'g1',text:'original request',route:{},workload:{version:5,truncated:false,nodes:[],queue_order:[]}}})
      }
      return text({})
    })
    $.clock.after = (delay, callback) => { timers.push({ delay, callback }); return { cancel() {} } }
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    const timer = timers.find(t => t.delay === 2000)
    if (!timer) throw new Error('mailbox consumer not scheduled')
    await timer.callback()
    // The pane refresh runs beside the tick and no longer delays it.
    await sleep(20)
    return { tools, logs: state.logs, rearmed: timers.filter(t => t.delay === 2000).length,
      claimedBeforeStatus: tools.indexOf('work_side_claim') < tools.indexOf('work_side_status'), statusFinished }
  },
  async 'live-settings-command'() {
    const changes = []
    const { $ } = host(async (tool, args) => {
      if (tool === 'work_live_settings') {
        changes.push(args)
        return text({ settings: [{ key: 'work_mode', label: 'Work mode', value: args.value || false }] })
      }
      return text(snapshot())
    })
    const result = await handlers['command.run']($, { args: 'settings work_mode=on' })
    return { text: result.text, key: changes[0].key, value: changes[0].value }
  },
  async 'side-intake-external-bridge'() {
    let queued = 0, main = 0
    const { $ } = host(async (tool, args) => {
      if (tool === 'work_status') return text(snapshot())
      if (tool === 'work_side_intake') { queued++; return text({ accepted: true, item_id: 'n50', recap: args.text }) }
      return text({})
    })
    const runMain = async () => { main++; return {} }
    const result = await handlers['prompt.submit']($, { text: '/btw_with_mcp {"event_key":"source:1","text":"request"}', origin: { kind: 'peer' } }, runMain)
    return { queued, main, drop: result.drop }
  },
  async 'side-intake-command'() {
    const seen = []
    const { $ } = host(async (tool, args) => {
      seen.push({ tool, args })
      if (tool === 'work_status') return text(snapshot())
      if (tool === 'work_side_intake') return text({ accepted: true, item_id: 'n50', recap: args.text, duplicate: false })
      return text({})
    })
    const result = await sideHandlers['command.run']($, { args: JSON.stringify({ event_key: 'source:1', text: 'request' }) })
    return { text: result.text, tools: seen.map(r => r.tool), token: Boolean(seen.find(r => r.tool === 'work_side_intake')?.args?._host?.token) }
  },
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
      policy: { enabled: true, paused: false, priority_limits: { P1: 5, P2: 3, P3: 1, P4: 1, P5: 1 }, launches_per_minute: 4, max_rework: 2 }, leases }
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
    $.agent.spawn = async (args) => { leases.L9 = { id: 'L9', item_id: 'n229', item_title: 'Add retry backoff', item_ref: 'PAY-229',
        goal_title: 'Product goal', item_path: ['Product goal', 'Checkout phase', 'Add retry backoff'],
        priority: 'P1', role: 'worker', state: 'running', slot: true,
        agent_name: args.name, host_id: 'host-9', plan_version: 5 }
      return { agentId: 'host-9', model: 'fixture' } }
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    // The mod starts the fork itself; the model never copies an Agent name.
    const run = await handlers['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_run',
      item_id: 'n229', priority: 'P1', role: 'worker', expected_version: 12 }, next({}))
    if (run.deny || typeof run.result !== 'string') throw new Error('dispatch did not return a result: ' + JSON.stringify(run))
    const dispatch = JSON.parse(run.result)
    const shown = await handlers['command.run']($, { args: 'parallel' })
    return { spawned: { agentId: dispatch.agent_id }, spawnedEvent: { description: dispatch.description, name: dispatch.name }, dispatch, shown: shown.text || '' }
  },
  // Direct spawn: work_parallel_run itself starts the fork; the model never
  // copies an Agent name. A denied spawn releases the unbound reservation and
  // keeps the Work item queued.
  async 'parallel-direct-spawn-started'() {
    const parallel = { version: 11, active_device: 0,
      policy: { enabled: true, paused: false, priority_limits: { P1: 5, P2: 3, P3: 1, P4: 1, P5: 1 }, launches_per_minute: 4, max_rework: 2 }, leases: {} }
    const calls = []
    const { $, state } = host(async (tool, args) => {
      if (tool === 'work_parallel_status') return text(parallel)
      if (tool === 'work_parallel_event') {
        calls.push(args)
        if (args.event === 'reserve') return text({ lease_id: 'L9', version: 12, plan_version: 5 })
        if (args.event === 'bind') return text({ version: 13, leases: {} })
      }
      return text(snapshot())
    })
    $.session.repo = async () => null
    $.session.root = async () => '/repo'
    $.agent.spawn = async (args) => { state.spawned = args; return { agentId: 'host-9', model: 'fixture' } }
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    const run = await handlers['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_run',
      item_id: 'n227', priority: 'P1', role: 'worker', expected_version: 11 }, next({}))
    return { run: JSON.parse(run.result), spawned: state.spawned, calls }
  },
  async 'parallel-direct-spawn-through-own-hook'() {
    // The engine runs ciel-work's agent.spawn hook for the host's own spawn
    // (owned run 2026-11-09, "agent.spawn nested in ciel-work"). The router
    // refuses a second reserve for the same item, as work_parallel.py does.
    const parallel = { version: 11, active_device: 0,
      policy: { enabled: true, paused: false, priority_limits: { P1: 1, P2: 1, P3: 1, P4: 1, P5: 1 }, launches_per_minute: 4, max_rework: 2 }, leases: {} }
    const calls = []
    let reserved = 0
    const { $, state } = host(async (tool, args) => {
      if (tool === 'work_parallel_status') return text(parallel)
      if (tool === 'work_parallel_event') {
        calls.push(args.event)
        if (args.event === 'reserve') {
          if (++reserved > 1) return { content: [{ type: 'text', text: 'active slot capacity reached' }], isError: true }
          return text({ lease_id: 'L9', version: 12, plan_version: 5 })
        }
        if (args.event === 'bind') return text({ version: 13, leases: {} })
        if (args.event === 'release') return text({ lease_id: 'L9', version: 13, released: true })
      }
      return text(snapshot())
    })
    $.session.repo = async () => null
    $.session.root = async () => '/repo'
    $.agent.spawn = async (args) => {
      const own = async (input) => { state.spawned = input; return { agentId: 'host-9', model: 'fixture' } }
      own.origin = { plugin: 'ciel-work' }
      return handlers['agent.spawn']($, { ...args, fork: true }, own)
    }
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    const run = await handlers['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_run',
      item_id: 'n227', priority: 'P2', role: 'worker', expected_version: 11 }, next({}))
    return { run: JSON.parse(run.result), spawned: state.spawned || null, calls }
  },
  async 'parallel-direct-spawn-denied-releases'() {
    const parallel = { version: 11, active_device: 0,
      policy: { enabled: true, paused: false, priority_limits: { P1: 5, P2: 3, P3: 1, P4: 1, P5: 1 }, launches_per_minute: 4, max_rework: 2 }, leases: {} }
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
    $.agent.spawn = async () => ({ deny: 'Subagent spawn denied by fixture' })
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    const run = await handlers['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_run',
      item_id: 'n227', priority: 'P1', role: 'worker', expected_version: 11 }, next({}))
    return { run: JSON.parse(run.result), calls }
  },
  // Mirrors Kobi 2026-11-04: work_parallel_run returned a name, but the model
  // called Agent without it. The gate must name that exact omission, and the
  // user-facing parallel view must expose the pending name without a router read.
  async 'parallel-missing-agent-name'() {
    const parallel = { version: 11, active_device: 0,
      policy: { enabled: true, paused: false, priority_limits: { P1: 5, P2: 3, P3: 1, P4: 1, P5: 1 }, launches_per_minute: 4, max_rework: 2 }, leases: {} }
    const { $ } = host(async (tool) => tool === 'work_parallel_status' ? text(parallel) : text(snapshot()))
    $.session.repo = async () => null
    $.session.root = async () => '/repo'
    $.agent.spawn = async () => ({ deny: 'fixture has no direct spawn' })
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    const runRaw = await handlers['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_run',
      item_id: 'n227', priority: 'P1', role: 'worker', expected_version: 11 }, next({}))
    if (runRaw.deny || typeof runRaw.result !== 'string') throw new Error('dispatch did not return a result: ' + JSON.stringify(runRaw))
    const run = runRaw
    const dispatch = JSON.parse(run.result)
    const denied = await handlers['agent.spawn']($, { tool_use_id: 'call-agent', prompt: 'Execute only approved Work item n227.',
      description: 'Work n227', subagentType: 'fork', fork: true, background: true }, next({ agentId: 'must-not-start' }))
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
  async 'parallel-waiting-releases-slot-and-sendmessage-reacquires'() {
    const lease = { id: 'L1', item_id: 'n2', role: 'worker', state: 'waiting_input', host_id: 'agent-wait', slot: true, plan_version: 7 }
    const calls = []
    const { $, state } = host(async (tool, args) => {
      if (tool === 'work_status') return text(snapshot())
      if (tool === 'work_parallel_status') return text({ version: 9, policy: { enabled: true, paused: false }, leases: { L1: lease } })
      if (tool === 'work_parallel_event') {
        calls.push(args)
        if (args.event === 'wait') lease.slot = false
        if (args.event === 'reacquire') lease.slot = true
        return text({ version: 10, leases: { L1: lease } })
      }
      return text({})
    })
    state.agents = [{ id: 'agent-wait', status: 'completed' }]
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    await handlers['turn.complete']($, {}, next(undefined))
    const afterWait = { slot: lease.slot, calls: calls.map(c => c.event) }
    calls.length = 0
    const resumed = await handlers['tool.call']($, { tool: 'SendMessage', to: 'agent-wait', message: 'continue' }, next({ sent: true }))
    return { afterWait, resumed: Boolean(resumed?.sent), slot: lease.slot, calls: calls.map(c => c.event) }
  },
  async 'parallel-unreported-stops-and-fills'() {
    const leases = { L1: { id: 'L1', item_id: 'n2', role: 'worker', state: 'running', host_id: 'agent-old', slot: true, plan_version: 7 } }
    const calls = [], tools = []
    const { $, state } = host(async (tool, args) => {
      if (tool === 'work_status') return text(snapshot())
      if (tool === 'work_parallel_status') return text({ version: 9, policy: { enabled: true, paused: false }, leases })
      if (tool === 'work_parallel_event') {
        calls.push(args.event)
        if (args.event === 'unreported') { leases.L1.state = 'unreported'; leases.L1.slot = false; return text({ version: 10 }) }
        if (args.event === 'reserve_next' && calls.filter(e => e === 'reserve_next').length === 1)
          return text({ lease_id: 'L2', version: 11, plan_version: 7, item_id: 'n3', item_title: 'Next',
            item_note: 'exact note', item_path: ['Goal', 'Next'], workspace: { path: '/wt/n3' }, worker_token: 'b'.repeat(64) })
        if (args.event === 'reserve_next') return { content: [{ type: 'text', text: 'no eligible candidate' }], isError: true }
        if (args.event === 'bind') return text({ version: 12 })
      }
      return text({})
    })
    $.tool.call = async args => { tools.push(args); return {} }
    $.session.repo = async () => ({})
    $.session.root = async () => '/repo'
    $.agent.spawn = async args => { state.spawned = args; return { agentId: 'agent-new' } }
    state.agents = [{ id: 'agent-old', status: 'completed' }]
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    await handlers['turn.complete']($, { agentId: 'agent-old', reason: 'answer', answer: 'done' }, next(undefined))
    const consumed = await handlers['session.receive']($, { text: '<task-notification><task-id>agent-old</task-id></task-notification>' }, next({ text: 'passed' }))
    return { calls, tools, spawned: state.spawned, consumed }
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
  // SendMessage returns before the engine emits the deferred restart spawn.
  // The exact durable lease/name authorization must survive that boundary.
  async 'parallel-restart-sendmessage-deferred-spawn'() {
    const lease = { id: 'L1', item_id: 'n2', role: 'worker', state: 'running', host_id: 'agent-old',
      agent_name: 'work-auto-exact', dispatch_name: 'work-auto-exact', slot: true, plan_version: 7 }
    const { $, state } = host(async (tool) => tool === 'work_parallel_status'
      ? text({ version: 9, policy: { enabled: true, paused: false }, leases: { L1: lease } }) : text(snapshot()))
    state.agents = []
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    const sent = await handlers['tool.call']($, { tool: 'SendMessage', to: 'agent-old', message: 'continue' }, next({ sent: true }))
    const spawnNext = next({ agentId: 'agent-new' }); spawnNext.origin = { plugin: 'engine' }
    const spawned = await handlers['agent.spawn']($, { name: 'work-auto-exact', subagentType: 'fork', fork: true,
      prompt: 'resume', description: 'resume', background: true }, spawnNext)
    const duplicate = await handlers['agent.spawn']($, { name: 'work-auto-exact', subagentType: 'fork', fork: true,
      prompt: 'duplicate', description: 'duplicate', background: true }, spawnNext)
    return { sent: Boolean(sent.sent), spawned: spawned.agentId || '', duplicate: verdict(duplicate) }
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
      policy: { enabled: true, paused: false, priority_limits: { P1: 5, P2: 3, P3: 1, P4: 1, P5: 1 }, launches_per_minute: 4, max_rework: 2 }, leases: {} }
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
  async 'side-receipt-redacted-refresh'() {
    let hostOnly = true
    const full = {receipt_id:'mail1',item_id:'n1',stage:'processed',original:'Private external question',
      recap:'Private external question',result:{action:'answer',summary:'Looked up facts',reply:'Verified answer',delivery:'submitted',handoff:'not_required',version:5,
        comparisons:{profile1:{state:'answered',profile:{mode:'recent',model:'retired-comparison'}}}}}
    const { $ } = host(async () => text({...snapshot(),side_inbox_receipts:[hostOnly ? full : {receipt_id:'mail1',stage:'processed',item_id:'n1',external_ack:'not_sent'}]}))
    $.ui.resolve = () => ({Box: props=>props,Text: props=>props,Button: props=>props})
    await handlers['command.run']($, {args:'status'})
    hostOnly = false
    await handlers['command.run']($, {args:'status'})
    const pane = await sideHandlers['ui.render']($,{component:'Pane',requestId:'ciel-side-intake'},next(null))
    return {pane}
  },
  async 'side-uncertain-retry-cancel-and-confirm'() {
    const calls = []
    const answers = ['Cancel','Send again (may duplicate)']
    const receipt = {receipt_id:'mail1',stage:'processed',original:'Q',
      result:{action:'answer',summary:'S',reply:'R',delivery:'uncertain',handoff:'not_required',version:1}}
    const {$} = host(async(tool,args)=>{calls.push({tool,args});
      if (tool === 'work_status') return text({...snapshot(),side_inbox_receipts:[receipt]})
      if (tool === 'work_side_status') return text({enabled:true,side_inbox_receipts:[receipt]})
      if (tool === 'work_side_claim') return text({claim:null})
      return text({receipt})})
    $.ui.ask = async()=>answers.shift()
    $.ui.resolve = () => ({Box:props=>props,Text:props=>props,Button:props=>props})
    await handlers['command.run']($,{args:'status'})
    const pane = await sideHandlers['ui.render']($,{component:'Pane',requestId:'ciel-side-intake'},next(null))
    const button = pane.children.flatMap(row=>row.children || []).find(child=>child.label === 'Retry reply…')
    await button.onPress()
    const before = calls.filter(row=>row.tool === 'work_side_retry_reply').length
    await button.onPress()
    return {before,retries:calls.filter(row=>row.tool === 'work_side_retry_reply').map(row=>row.args)}
  },
  async 'side-receipt-visible'() {
    const board = { ...snapshot(), side_intake_receipts: [{ item_id: 'n77', recap: 'request recap' }] }
    const { $, state } = host(async () => text(board))
    await check($)
    await handlers['command.run']($, { args: 'status' })
    await handlers['command.run']($, { args: 'status' })
    return { logs: state.logs.filter(s => s.includes('mod queued')) }
  },
  async 'parallel-complete-off'() {
    let parallelCalls = 0
    const { $ } = host(async tool => {
      if (tool === 'work_parallel_status') { parallelCalls++; throw Error('Work mode is off') }
      return text({ enabled: false })
    })
    await handlers['turn.complete']($, {}, next({ text: 'done' }))
    return { parallelCalls }
  },
  async 'stop-identical-blockers-version-change'() {
    const ready = { ...snapshot(), continuation: { actionable: 0, waiting: [{ id: 'n586', waiting_on: 'connector' }], blocked: 0 }, discovery: { enabled: false } }
    const { $ } = host(async () => text(ready))
    const event = { session_id: 'S1' }
    const first = await handlers['classic.Stop']($, event, next({}))
    ready.version++
    const second = await handlers['classic.Stop']($, event, next({}))
    ready.continuation.waiting[0].waiting_on = 'new dependency'
    const changed = await handlers['classic.Stop']($, event, next({}))
    return { first: first?.block || '', second: second?.block || '', changed: changed?.block || '' }
  },
  async 'stop-background-wait-recheck'() {
    const ready = { ...snapshot(), continuation: { actionable: 0, waiting: [{ id: 'n586', waiting_on: 'publish and external connector contract' }], blocked: 0 }, discovery: { enabled: false } }
    const { $ } = host(async () => text(ready))
    const event = { session_id: 'S1', background_tasks: [{ id: 'publish' }] }
    const first = await handlers['classic.Stop']($, event, next({}))
    const second = await handlers['classic.Stop']($, event, next({}))
    return { text: first?.block || '', second: second?.block || '' }
  },
  async 'stop-background-blocked-recheck'() {
    const ready = { ...snapshot(), continuation: { actionable: 0, waiting: [], blocked: 1, held: 0,
      blocked_items: [{ id: 'n92', title: 'independent investigation', waiting_on: 'evidence', root_id: 'n1' }] },
      discovery: { enabled: false } }
    const { $, state } = host(async () => text(ready))
    const event = { session_id: 'S1', background_tasks: [{ id: 'unrelated-publish' }] }
    const first = await handlers['classic.Stop']($, event, next({}))
    const second = await handlers['classic.Stop']($, event, next({}))
    return { text: first?.block || '', second: second?.block || '', logs: state.logs }
  },
  async 'stop-blocked-recheck'() {
    // Nothing runnable, one blocked item with a recorded reason: the stop
    // must name it and demand a fresh check instead of reporting no work.
    const ready = { ...snapshot(), continuation: { actionable: 0, waiting: [], blocked: 1, held: 0,
        blocked_items: [{ id: 'n92', title: 'freeze logs', waiting_on: 'Exact production freeze logs not identified', root_id: 'n1', path: ['goal', 'freeze logs'] }] },
      discovery: { enabled: true, instruction: 'search elsewhere' } }
    const { $ } = host(async () => text(ready))
    const result = await handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    // Same unchanged board on the next stop: no second re-injection (the
    // first recheck changed nothing), so the session stays parked instead of
    // looping the same probe every turn.
    const second = await handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    return { text: result?.block || '', second: second?.block || '' }
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
  async 'router-reconnected'() {
    let attempts = 0
    const {$,state} = host(async()=>{if (++attempts === 1) throw new Error('connection closed PRIVATE-TOKEN');return text(snapshot())})
    let connections = 0
    $.mcp.connect = async()=>{connections++;return {isConnected:true,server:'ciel-runtime-router'}}
    return {first:verdict(await check($,'agent-a')),attempts,connections,logs:state.logs}
  },
  async 'router-auth-closed'() {
    const {$,state} = host(async()=>{throw new Error('401 authentication PRIVATE-TOKEN')})
    let connections = 0
    $.mcp.connect = async()=>{connections++;return {isConnected:true,server:'ciel-runtime-router'}}
    return {first:verdict(await check($,'agent-a')),connections,logs:state.logs}
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
  // The inspector detail read receives a router error reply. Whether the
  // bridge marks it isError or returns plain "Error…" text, the pane must
  // show the router's own message — never a JSON.parse complaint about its
  // first word (screenshot: 'JSON Parse error: Unexpected identifier "Error"').
  ...Object.fromEntries(['plain', 'flagged'].map(kind => ['details-router-error-' + kind, async () => {
    const errText = 'Error: Work plan store is locked by another writer'
    const { $, state } = host(async (tool, args) => {
      if (tool === 'work_status' && args.detail) return kind === 'flagged'
        ? { isError: true, content: [{ type: 'text', text: errText }] } : text(errText)
      return text(snapshot())
    })
    $.ui.open = async spec => { state.opened = spec }
    $.ui.resolve = () => ({ Box: props => props, Button: props => props, Text: props => props })
    await handlers['session.end']($, {}, next(undefined))
    await handlers['command.run']($, { args: 'view' })
    const detail = await readDetailAtom($)
    return { opened: state.opened?.id || '', shown: detail?.error || '' }
  }])),
  // A parallel read that returns non-JSON text names the read and shows what
  // the router said, instead of a JSON.parse error about the first word.
  async 'parallel-error-text'() {
    const { $ } = host(async () => text('Error: parallel ledger unavailable'))
    $.session.repo = async () => null
    $.session.root = async () => '/repo'
    const run = await handlers['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_run',
      item_id: 'n227', priority: 'P1', role: 'worker', expected_version: 11 }, next({}))
    return { error: run.deny || '' }
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

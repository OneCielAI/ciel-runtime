// Real integration: the packaged register.js drives the real Python
// work_parallel_mcp / finalization stores (work_mod_real_backend_server.py)
// in a temporary CIEL_RUNTIME_CONFIG_DIR. restart() re-imports register.js, so
// every in-memory mod map is lost exactly as in a CLI process restart, while
// the backend's durable files remain. Prints one JSON line of observations.
import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { pathToFileURL } from 'node:url'

const scenario = process.argv[2]
const bridge = spawn(process.env.PYTHON || 'python', ['-u', 'work_mod_real_backend_server.py'],
  { cwd: process.cwd(), env: process.env, stdio: ['pipe', 'pipe', 'inherit'] })
let buffer = ''; const waits = []
bridge.stdout.setEncoding('utf8')
bridge.stdout.on('data', chunk => {
  buffer += chunk
  while (buffer.includes('\n')) {
    const i = buffer.indexOf('\n'), line = buffer.slice(0, i); buffer = buffer.slice(i + 1)
    waits.shift()?.(JSON.parse(line))
  }
})
const rpc = (action, data = {}) => new Promise((resolve, reject) => {
  waits.push(value => value.ok ? resolve(value.value) : reject(Object.assign(Error(value.error), { trace: value.trace })))
  bridge.stdin.write(JSON.stringify({ action, ...data }) + '\n')
})
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
// The mod pumps asynchronously after hooks return. Wait until its router
// traffic has been quiet for 400 ms (bounded), never on a fixed short sleep.
// A router call still in flight is not quiet: under load one real backend call
// can take longer than the window (measured 2026-11-13: a bind retry landed
// after the snapshot while other test runs shared the CPU).
let traffic = 0, inflight = 0
const tracked = fn => async (...args) => {
  inflight++
  try { return await fn(...args) } finally { inflight--; traffic++ }
}
async function quiet(limit = 30000) {
  const end = Date.now() + limit
  let seen = -1
  while (Date.now() < end) {
    if (traffic === seen && inflight === 0) return
    seen = traffic
    await sleep(400)
  }
  throw new Error('mod router traffic did not settle within ' + limit + ' ms')
}
const text = value => ({ content: [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value) }] })
const engine = value => { const f = async () => value; f.origin = { plugin: 'engine' }; return f }

const init = await rpc('init')
// Native agents outlive a mod restart; the list is the host's, not the mod's.
// The row $.agent.list() returns for a fork a plugin started with $.agent.spawn,
// as measured on claude.exe 2.1.323 (run-p0-revival-20261112-192623): keys
// description, id, name, spawnedBy (the spawning plugin), status, type 'fork'.
// A fork from the model's own Agent call carries no spawnedBy.
const pluginForkRow = (id, name, status, description = '') =>
  ({ id, type: 'fork', status, spawnedBy: 'ciel-work', name, description })
const native = { agents: [], spawns: [], prompts: [], appended: [], denyNext: 0, failBind: 0,
  answers: [], asks: [], toolCalls: [], hostMarkers: [], restartErrors: [], restartCalls: [],
  bindResults: [], reconcileErrors: [], spawnPrompts: new Map(), statusGate: null, listCalls: 0,
  attributions: [], failAttribution: 0, attributionReplies: [], failStatus: false, sends: [],
  failReconcile: new Set(), failIdentity: false, identityCalls: [], failReview: new Set(), reviewRefusals: 0,
  failureRecords: [], failFailureRecord: false }
const calls = []
let hooks, $, generation = 0
// One claude.exe process environment. A 'reload' re-imports register.js in the
// same process (module maps lost, env kept: owned CC 2.1.322 probe 44e40778);
// a 'process' restart also gets a fresh env. Every start names which it is.
let processEnv = new Map(), processes = 0
// The engine's host-held $.state: kept across a reload, reset for a new process
// (owned probe nonce-lifetime-probe-44e40778).
let hostStore = new Map()
const envFault = { get: false, set: false }
const digest = value => value ? createHash('sha256').update(String(value)).digest('hex') : null
// $.clock.after timers, kept only while native.captureTimers is set (the
// wait-deadlock scenarios); every other scenario keeps the inert clock.
const timers = []
const isDispatchNotice = text => /^Ciel Work dispatch (wake|check)/.test(String(text))
// Time passing with main idle: each round fires every timer armed so far.
// releasedBy: the delay of the first timer after which a watched call appeared.
const passTime = async (rounds, watch = null) => {
  const fired = []
  for (let round = 0; round < rounds; round++) {
    const due = timers.filter(timer => !timer.fired && !timer.cancelled)
    for (const timer of due) {
      timer.fired = true
      fired.push(timer.by + ':' + timer.delay)
      const before = watch ? watch() : 0
      try { await timer.callback() } catch { /* A timer's own failure is the mod's to record. */ }
      await quiet()
      if (watch && passTime.releasedBy === undefined && watch() > before) passTime.releasedBy = { delay: timer.delay, by: timer.by, round }
    }
  }
  return fired
}
async function start(kind, { busy = false, interactive = true } = {}) {
  if (kind !== 'process' && kind !== 'reload') throw new Error('start kind must be process or reload')
  if (kind === 'process' || processes === 0) { processEnv = new Map(); hostStore = new Map(); processes++ }
  // An old process's timers die with it.
  if (kind === 'process') timers.length = 0
  hooks = {}
  const mod = await import(pathToFileURL(process.cwd() + '/register.js').href + '?g=' + (++generation))
  mod.register((name, filter, handler) => {
    if (filter?.command === 'btw_with_mcp' || filter?.requestId === 'ciel-side-intake') return
    // /work-mode is kept: its parallel view prints this module's failure log.
    if (name === 'command.run' && filter?.command === 'work-mode') { hooks['command:work-mode'] = handler; return }
    // The Work & SSE pane's own draw (R2 B2 pane control check).
    if (name === 'ui.render' && filter?.component === 'Pane' && filter?.requestId === 'work-details') { hooks['render:work-details'] = handler; return }
    if (name === 'command.run' || name === 'ui.render') return
    // Only the Work & SSE pane's dashboard messages (its resolve control).
    if (name === 'ui.message' && filter?.element !== 'obs-graphic') return
    hooks[name] = typeof filter === 'function' ? filter : handler
  })
  $ = {
    session: { id: async () => {
        const exits = native.sends.filter(s => String(s.text || '').startsWith('Your native turn ended')).length
        if (native.throwSessionIdAfterExitDelivered > 0 && exits > 0) {
          native.throwSessionIdAfterExitDelivered--
          throw new Error('session identity refused after delivered exit contract (fixture)')
        }
        return 'S1'
      }, model: async () => 'fixture-main', repo: async () => null,
      root: async () => init.repo, send: async args => {
        native.sends.push(args)
        const exit = String(args.text || '').startsWith('Your native turn ended')
        if (exit && native.exitSendFault === 'undelivered') { native.exitSendFault = null; return { isDelivered: false } }
        if (exit && native.exitSendFault === 'throw') { native.exitSendFault = null; throw new Error('exit contract send threw (fixture)') }
        if (native.nestedOwnSend) {
          // CC 2.1.325 dispatches a plugin's $.session.send through SendMessage and
          // may reach another ciel-work module instance's session.send hook. The
          // engine pins this exact sender origin across next(e).
          const event = { to: args.to?.agentId ?? args.to?.sessionId ?? args.to,
            text: args.text, origin: { kind: 'plugin', name: 'ciel-work' } }
          const next = async () => ({ isDelivered: true })
          next.origin = { plugin: 'ciel-work' }
          const result = await hooks['session.send']($, event, next)
          ;(native.nestedOwnSendResults ||= []).push(result)
          return result
        }
        return { isDelivered: true }
      },
      append: async args => {
        // The engine refuses one append (register.js records the wake failed).
        if (native.denyAppend > 0) { native.denyAppend--; return { deny: 'append refused (fixture)' } }
        native.appended.push(args.message.content[0].text); return {} } },
    env: { get: async key => {
        if (key === 'CIEL_RUNTIME_WORK_PARALLEL_HOST_TOKEN') return init.token
        // R2 B2: the launcher that bound the router scope (the server process).
        if (key === 'CIEL_RUNTIME_WORK_LAUNCHER_PID')
          return native.launcherPid !== undefined ? native.launcherPid : String(init.launcher_pid)
        if (envFault.get) throw new Error('env read refused')
        return processEnv.get(key)
      },
      set: async (key, value) => {
        if (envFault.set) return { deny: 'env write refused' }
        if (value === undefined) processEnv.delete(key); else processEnv.set(key, value)
        return {}
      } },
    mcp: { connect: async () => ({ isConnected: true, server: 'ciel-runtime-router' }),
      call: tracked(async (_server, tool, args) => {
        traffic++
        // Every router call, by tool name (KOBE start-load measurement).
        native.toolLog?.push(tool === 'work_parallel_status' && args?.projection ? tool + ':' + args.projection : tool)
        // The engine has not connected the router yet (register.js retryStart).
        if (native.mcpDown) throw new Error('no connected MCP tool')
        // Only the board read times out (register.js readSnapshot: router-timeout takes
        // no reconnect, so session.start sees no snapshot while its host phase works).
        if (tool === 'work_status' && native.failBoard > 0) { native.failBoard--; throw new Error('MCP request timed out') }
        // One review wake prepare is lost on the way (router connection reset).
        if (args.event === 'review_wake' && args.operation === 'prepare' && native.failWakePrepare > 0) {
          native.failWakePrepare--; (native.wakeLog ||= []).push('prepare-lost')
          return { isError: true, ...text('router connection reset') }
        }
        if (args.event === 'review_wake') (native.wakeLog ||= []).push(args.operation + (args.state ? ':' + args.state : ''))
        // The router is down for every Work status read while this is set.
        if (tool === 'work_parallel_status' && native.failStatus) return { isError: true, ...text('router connection reset') }
        // X2 criterion 6: the first status read after a recovery send is refused
        // once, so the host phase throws past the send (in the pump).
        if (tool === 'work_parallel_status' && native.failStatusAfterRecovery > 0
            && native.sends.some(s => String(s.text || '').startsWith('Work recovery:'))) {
          native.failStatusAfterRecovery--
          return { isError: true, ...text('router connection reset') }
        }
        // One armed gate holds the next status read (a pump's first call) until released.
        if (tool === 'work_parallel_status' && native.statusGate) {
          const gate = native.statusGate
          native.statusGate = null
          gate.held = true
          await gate.promise
          traffic++
        }
        if (tool.startsWith('work_parallel_')) calls.push({ tool, event: args.event, operation: args.operation })
        // R2 B2: which host calls carry this process's nonce (digest only).
        if (tool.startsWith('work_parallel_'))
          (native.nonceCalls ||= []).push({ tool, event: args.event ?? null, lease_id: args.lease_id ?? null,
            nonce: digest(args._host?.launch_nonce),
            launcher_pid: args._host?.launcher_pid ?? null, internal: args._host?.internal === true,
            host: args._host !== undefined })
        // What main's review decision carried to the router (R1 review pin).
        if (tool === 'work_parallel_item_review')
          (native.reviewSent ||= []).push({ lease_id: args.lease_id ?? null,
            expected_finalization_version: args.expected_finalization_version ?? null,
            expected_review_pin: args.expected_review_pin ?? null })
        if (/^rework_launch_|^rework_spawn_/.test(String(args.event || '')))
          native.hostMarkers.push({ event: args.event, internal: args._host?.internal === true,
            user_confirmed: args._host?.user_confirmed === true,
            nonce: digest(args._host?.launch_nonce), fork: args._host?.fork === true })
        // Main's review decision for one lease is refused by the router.
        if (tool === 'work_parallel_item_review' && native.failReview.has(args.lease_id)) {
          native.reviewRefusals++
          return { isError: true, ...text('workspace unavailable: injected refusal; approve refused; rework refused') }
        }
        // Every durable failure-log write, and an optional failing router.
        if (args.event === 'failure_record') {
          native.failureRecords.push({ code: args.code ?? null, lease_id: args.lease_id ?? null,
            dedup_key: args.dedup_key ?? null, internal: args._host?.internal === true, failed: native.failFailureRecord })
          if (native.failFailureRecord) return { isError: true, ...text('router connection reset') }
        }
        // Every durable fork identity lookup, and an optional failing router.
        if (args.event === 'fork_identity') {
          native.identityCalls.push({ ids: args.ids ?? null, names: args.names ?? null, internal: args._host?.internal === true,
            failed: native.failIdentity })
          if (native.failIdentity) return { isError: true, ...text('router connection reset') }
        }
        // One lease's reconcile is refused by the router (an injected refusal).
        if (args.event === 'reconcile' && native.failReconcile.has(args.lease_id)) {
          native.reconcileErrors.push({ lease_id: args.lease_id, state: args._host?.state ?? null, error: 'reconcile refused (injected router refusal)' })
          return { isError: true, ...text('reconcile refused (injected router refusal)') }
        }
        // The router connection drops before a bind reaches the backend.
        if (args.event === 'bind' && native.failBind > 0) { native.failBind--; return { isError: true, ...text('router connection reset') } }
        // Every fork attribution query the mod sends, and an optional lost one.
        if (args.event === 'rework_fork_attribution') {
          const lost = native.failAttribution > 0
          native.attributions.push({ names: Array.isArray(args.names) ? [...args.names] : null, lost,
            internal: args._host?.internal === true })
          if (lost) { native.failAttribution--; return { isError: true, ...text('router connection reset') } }
          if (native.attributionReplies.length) return text(native.attributionReplies.shift())
        }
        // Every restart observation is measured in place: the stores are read
        // right before and after the real backend call, with nothing between.
        if (args.event === 'restart_submitted_terminal') {
          const measured = await rpc('measured_tool', { tool, args })
          native.restartCalls.push({ lease_id: args.lease_id, error: measured.error,
            before: measured.before, after: measured.after })
          if (measured.error !== null) { native.restartErrors.push(measured.error); return { isError: true, ...text(measured.error) } }
          return text(measured.value)
        }
        try {
          const value = await rpc('tool', { tool, args })
          if (args.event === 'bind') native.bindResults.push({ lease_id: args.lease_id, host_id: args._host?.host_id ?? null, error: null })
          return text(value)
        } catch (error) {
          if (args.event === 'bind') native.bindResults.push({ lease_id: args.lease_id, host_id: args._host?.host_id ?? null, error: error.message.slice(0, 200) })
          if (args.event === 'reconcile') native.reconcileErrors.push({ lease_id: args.lease_id, state: args._host?.state ?? null, error: error.message.slice(0, 200) })
          return { isError: true, ...text(error.message) }
        }
      }) },
    ui: { invalidate() {}, toast() {}, log() {}, open: async () => {}, close: async () => {},
      ask: async (question, choices) => { native.asks.push({ question, choices }); return native.answers.shift() ?? 'Approve' } },
    clock: { after(delay, callback) {
      if (!native.captureTimers) return { cancel() {} }
      // Which mod function armed it (the first register.js / side-intake.js frame).
      const frame = String(new Error().stack || '').split('\n').slice(2)
        .find(line => /register\.js|side-intake\.js/.test(line)) || ''
      const by = (/at (?:async )?([\w$.]+) /.exec(frame) || [])[1] || 'anonymous'
      const timer = { delay, callback, fired: false, cancelled: false, by }
      timers.push(timer)
      return { cancel() { timer.cancelled = true } }
    } },
    // The fork gate plugin's presence answer (ciel-work-gate); forkGate:false models a missing gate.
    tool: { list: async () => [], call: async args => { native.toolCalls.push(args); return {} },
      check: async q => typeof q?.input?.ciel_work_gate_probe === 'string' && native.forkGate !== false ? { decision: 'ask', plugin: 'ciel-work-gate', reason: 'ciel-work-gate ' + (native.forkGateVersion || '1') + ' ' + q.input.ciel_work_gate_probe } : { decision: 'allow' } },
    agent: { list: async () => { native.listCalls++; return native.agents }, register: async () => ({}),
      spawn: async args => {
        native.spawns.push(args.name)
        // Held in memory only (it carries the fork's capability); never printed.
        native.spawnPrompts.set(args.name, String(args.prompt || ''))
        if (native.denyNext > 0) { native.denyNext--; return { deny: 'native launch refused' } }
        const row = pluginForkRow('agent-' + native.spawns.length, args.name, 'running', String(args.description || ''))
        native.agents.push(row)
        return { agentId: row.id }
      } },
    prompt: { submit: async args => {
      // The engine refuses a prompt submission (wait-deadlock audit).
      if (native.failSubmit > 0 && !isDispatchNotice(args.text)) { native.failSubmit--; (native.submitFailures ||= []).push(String(args.text).slice(0, 60)); throw new Error('prompt submit refused (fixture)') }
      native.prompts.push(args.text); return {} } },
    state: {get:async ref=>{const held=hostStore.get(ref.plugin+'/'+ref.key);return {value:held?structuredClone(held.value):undefined,version:held?.version||0}},set:async (ref,value,options={})=>{const name=ref.plugin+'/'+ref.key,held=hostStore.get(name)||{version:0};if(options.ifVersion!==undefined&&options.ifVersion!==held.version)return {isSet:false,version:held.version};hostStore.set(name,{value:structuredClone(value),version:held.version+1});return {isSet:true,version:held.version+1}}},
    command: { register: async () => {} },
  }
  // KOBE hotfix 2026-11-18: how long the hook itself held, and how many router
  // calls it waited on before it returned (a stand-in for the hooks worker hold).
  const hookStarted = performance.now()
  await hooks['session.start']($, { isInteractive: interactive }, async e => e)
  native.startHookMs = performance.now() - hookStarted
  native.startCallsAtReturn = native.toolLog ? native.toolLog.length : null
  if (busy) await hooks['turn.start']($, {}, async e => e)
  await quiet()
  native.startSettledMs = performance.now() - hookStarted
}
const reviewCall = async (args) => {
  const result = await hooks['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_item_review',
    tool_use_id: 't-' + Date.now(), ...args }, engine({ ran: true }))
  await quiet()
  return result
}
const count = (event, operation) => calls.filter(c => c.event === event && (!operation || c.operation === operation)).length
const out = {}
const turnComplete = async () => { await hooks['turn.complete']($, {}, async e => e); await quiet() }
// The current module's failure log, as /work-mode parallel prints it. It
// observes native rows, so it is read only between measured boundaries.
const failures = async label => {
  const shown = (await hooks['command:work-mode']($, { args: 'parallel', origin: { kind: 'composer' } })).text
  const lines = shown.split('\n')
  const at = lines.indexOf('Recent parallel failures in this session:')
  ;(out.failures ||= []).push({ label, recent: at >= 0,
    entries: at >= 0 ? lines.slice(at + 1).filter(line => line.startsWith('  ')).map(line => line.slice(0, 300)) : [],
    restart: lines.filter(line => line.includes('restart-submitted')) })
}

// A SendMessage as the engine raises it, measured on CC 2.1.323 (owned preflight
// run-p0-row3t-20261112-172101): tool.call first (origin engine for the model,
// the calling plugin for $.session.send), then session.send. Delivery revives a
// killed or idle listed fork in place, with no agent.spawn hook call.
const revivalProbe = async (to, { origin = 'engine', agentId } = {}) => {
  const row = () => native.agents.find(a => a.id === to) || native.agents.find(a => a.name === to) || null
  const before = row()?.status ?? null
  let delivered = null, revived = null
  const deliver = async s => {
    const target = native.agents.find(a => a.id === s.to) || native.agents.find(a => a.name === s.to)
    if (target) { target.status = 'idle'; revived = target.id }
    return { isDelivered: true }
  }
  const next = async e => {
    const send = { to: e.to, text: e.message, origin: origin === 'engine' ? { kind: 'model' } : { kind: 'plugin', name: origin },
      ...(agentId ? { agentId } : {}) }
    const nextSend = async s => deliver(s)
    nextSend.origin = { plugin: origin }
    delivered = hooks['session.send'] ? await hooks['session.send']($, send, nextSend) : await deliver(send)
    return delivered?.isDelivered ? { success: true, message: 'Resuming agent', resumedAgentId: revived }
      : { success: false, message: delivered?.reason || 'not delivered' }
  }
  next.origin = { plugin: origin }
  const result = await hooks['tool.call']($, { tool: 'SendMessage', to, message: 'continue the old task', summary: 'resume',
    ...(agentId ? { agentId } : {}) }, next)
  await quiet()
  return { to, origin, deny: result?.deny ? String(result.deny).slice(0, 300) : null,
    sendReason: delivered && !delivered.isDelivered ? String(delivered.reason).slice(0, 300) : null,
    delivered: delivered?.isDelivered === true, revived, before, after: row()?.status ?? null }
}
// A deferred native resume spawn for one exact stored name (the restart path).
const deferredResume = async (name, agentId) => {
  const spawnNext = async e => { native.spawns.push(e.name); return { agentId: e.resume?.agentId || 'resumed' } }
  spawnNext.origin = { plugin: 'engine' }
  const result = await hooks['agent.spawn']($, { name, resume: { agentId }, fork: true, subagentType: 'fork',
    description: 'Work resume', prompt: 'resume' }, spawnNext)
  return result?.agentId ? { started: result.agentId } : { deny: String(result?.deny || '').slice(0, 300) }
}

try {
  if (scenario === 'approve') {
    const seeded = await rpc('seed', { title: 'Approve' })
    await start('process')
    out.woken = native.prompts.length
    out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'approve', evidence: 'main verified the diff' })
    out.backend = await rpc('inspect')
    out.lease = seeded.lease_id; out.item = seeded.item_id
  }

  if (scenario === 'completion-restart') {
    const seeded = await rpc('seed_restart_pair')
    out.checkpoint = seeded.checkpoint
    // The workers and their original host process are gone. A fresh module has
    // no native rows, but both submitted leases have durable completion receipts.
    native.agents.length = 0
    await start('process')
    out.afterRestart = await rpc('inspect')
    out.promptsAfterRestart = native.prompts.length
    const approved = seeded.leases[0]
    const other = seeded.leases[1]
    // Main cannot forge the host observation: the mod refuses before the router,
    // and the router refuses the shape a forwarded model call carries.
    const forgedBefore = calls.length
    out.forged = await hooks['tool.call']($, { tool:'mcp__ciel-runtime-router__work_parallel_event',
      event:'restart_submitted_terminal', lease_id:other.lease_id, generation:1, receipt_digest:'0'.repeat(64),
      _host:{ internal:true, absent_agent_id:other.agent_id, absent_from_complete_list:true } }, engine({ ran:true }))
    out.forgedRouterCalls = calls.slice(forgedBefore).filter(c => c.event === 'restart_submitted_terminal').length
    out.forgedBackend = await rpc('tool', { tool:'work_parallel_event', args:{ session_id:'claude:S1', expected_version:0,
      event:'restart_submitted_terminal', lease_id:other.lease_id, generation:1, receipt_digest:'0'.repeat(64),
      _host:{ token:init.token, agent_id:null } } }).then(value => ({ value }), error => ({ error:error.message }))
    out.decision = await reviewCall({ lease_id: approved.lease_id, decision: 'approve',
      evidence: 'main verified the passing result' })
    out.afterDecision = await rpc('inspect')
    // A second process restart must not republish either review or wake main.
    await start('process')
    out.afterSecondRestart = await rpc('inspect')
    out.promptsAfterSecondRestart = native.prompts.length
    out.approved = approved
    out.other = other
    out.restartTerminals = count('restart_submitted_terminal')
    out.wakePrepares = count('review_wake', 'prepare')
    out.reviewDecisions = calls.filter(c => c.tool === 'work_parallel_item_review').length
  }

  if (scenario === 'absent-no-receipt-restart') {
    // A running worker with no receipt is absent after restart: never observed
    // terminal; only the existing recovery message is attempted.
    const seeded = await rpc('seed_running')
    out.before = await rpc('inspect')
    native.agents.length = 0
    await start('process')
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    out.after = await rpc('inspect')
    out.lease = seeded.lease_id
    out.restartTerminals = count('restart_submitted_terminal')
    out.prompts = native.prompts.length
  }

  if (scenario === 'proof-mismatch-restart') {
    // A durable review whose ledger and workspace idle proofs disagree, with its
    // run terminal. A process restart must reach the backend repair.
    const seeded = await rpc('seed_proof_mismatch')
    const lease_id = seeded.lease_id
    out.seeded = seeded.proofs
    out.before = await rpc('inspect')
    native.agents.length = 0
    // (a) session.start alone, in a new process.
    await start('process')
    out.restartAfterStart = count('restart_submitted_terminal')
    out.proofsAfterStart = await rpc('proofs', { lease_id })
    out.afterStart = await rpc('inspect')
    out.promptsAfterStart = native.prompts.length
    // (b) The proofs match now. Each module load sends the event once; no
    // boundary changes a store's version or on-disk bytes.
    out.boundaries = []
    const measure = async (label, boundary) => {
      const sent = count('restart_submitted_terminal')
      const before = await rpc('stores', { lease_id })
      await boundary()
      const after = await rpc('stores', { lease_id })
      out.boundaries.push({ label, sent: count('restart_submitted_terminal') - sent, before, after })
    }
    await measure('turn.complete', turnComplete)
    await measure('turn.complete repeated', turnComplete)
    await failures('process 1')
    // (c) A second process restart, then repeated turn ends, then a reload.
    await measure('session.start process 2', () => start('process'))
    await measure('turn.complete process 2', turnComplete)
    await measure('turn.complete process 2 repeated', turnComplete)
    await failures('process 2')
    await measure('session.start reload', () => start('reload'))
    await measure('turn.complete after reload', turnComplete)
    await failures('reload')
    out.afterReplay = await rpc('inspect')
    out.promptsAfterReplay = native.prompts.length
    out.wakePreparesAfterReplay = count('review_wake', 'prepare')
    out.decision = await reviewCall({ lease_id, decision: 'approve', evidence: 'proofs converged' })
    out.final = await rpc('inspect')
    // (d) Finalized and archived: no later boundary sends it again.
    const sentAtFinal = count('restart_submitted_terminal')
    await turnComplete()
    await start('process')
    await turnComplete()
    await start('reload')
    await turnComplete()
    out.restartAfterFinal = count('restart_submitted_terminal') - sentAtFinal
    out.afterFinal = await rpc('inspect')
    await failures('after final')
    out.restartErrors = [...native.restartErrors]
    out.restartCalls = [...native.restartCalls]
    out.restartTotal = count('restart_submitted_terminal')
    out.lease = lease_id
  }

  if (scenario === 'rework-blocked-rebind') {
    // A rework decision whose board stage fails after the workspace was rebound:
    // the predecessor stays submitted, its manifest is a rebound tombstone, and
    // its fork is gone. Restart observations of it must succeed as no-ops.
    const seeded = await rpc('seed', { title: 'Blocked rebind' })
    const lease_id = seeded.lease_id
    native.agents.length = 0
    const segment = async (label, steps) => {
      const sent = count('restart_submitted_terminal')
      for (const step of steps) await step()
      ;(out.segments ||= []).push({ label, sent: count('restart_submitted_terminal') - sent })
    }
    await segment('process 1 session.start', [() => start('process')])
    await rpc('fault_resume_assignment', { message: 'resume assignment temporarily unavailable' })
    out.decision = await reviewCall({ lease_id, decision: 'rework', evidence: 'blocked after rebind' })
    out.blocked = await rpc('inspect')
    out.blockedWorkspace = await rpc('workspace_status', { lease_id })
    out.replacement = out.blocked.reviews[lease_id].replacement_lease_id
    const callsBefore = native.restartCalls.length
    await segment('process 1 turn.complete x2', [turnComplete, turnComplete])
    await failures('process 1')
    await segment('process 2 session.start', [() => start('process')])
    await segment('process 2 turn.complete x3', [turnComplete, turnComplete, turnComplete])
    await failures('process 2')
    await segment('reload session.start', [() => start('reload')])
    await segment('reload turn.complete x2', [turnComplete, turnComplete])
    await failures('reload')
    out.stillBlocked = await rpc('inspect')
    out.blockedCalls = native.restartCalls.slice(callsBefore)
    out.spawnsWhileBlocked = [...native.spawns]
    // The stage unblocks: the host pump rolls the same decision forward.
    await rpc('fault_resume_assignment', { message: null })
    await segment('unblocked turn.complete', [turnComplete])
    out.final = await rpc('inspect')
    await segment('after ready process 3', [() => start('process'), turnComplete, turnComplete])
    await failures('after ready')
    out.afterReady = await rpc('inspect')
    out.spawns = [...native.spawns]
    out.rollForwards = count('review_roll_forward')
    out.restartErrors = [...native.restartErrors]
    out.lease = lease_id
  }

  if (scenario === 'rework-review-lifecycle') {
    // run-20261111-213818: the rework predecessor stayed under MAIN REVIEW after
    // its replacement generation was approved and finalized.
    const seeded = await rpc('seed', { title: 'Rework lifecycle' })
    const lease_id = seeded.lease_id
    await start('process')
    const prompts = []
    const realSpawn = $.agent.spawn
    $.agent.spawn = async args => { prompts.push(String(args.prompt || '')); return realSpawn(args) }
    const view = async label => {
      const text = (await hooks['command:work-mode']($, { args: 'parallel', origin: { kind: 'composer' } })).text
      const head = text.split('\n').find(line => line.startsWith('MAIN REVIEW')) || null
      const web = await rpc('web', { op: 'parallel' })
      const status = await rpc('tool', { tool: 'work_parallel_status', args: { session_id: 'claude:S1', _host: {} } })
      ;(out.views ||= []).push({ label, head,
        text: text.split('\n').filter(line => line.includes('lease ' + lease_id) || line.includes('lease ' + out.replacement)
          || line.includes('Changed before')),
        web: (web.parallel_reviews || []).map(row => [row.lease_id, row.state, row.changed_before_bind]),
        model: (status.review?.pending || []).map(row => [row.lease_id, row.state, row.changed_before_bind]) })
    }
    out.decision = await reviewCall({ lease_id, decision: 'rework', evidence: 'add the missing case' })
    out.replacement = (await rpc('inspect')).reviews[lease_id].replacement_lease_id
    out.replacementPrompt = prompts[0] || ''
    await view('rework bound and running')
    // The resume fork reports with its own capability and goes idle.
    const token = JSON.parse(prompts[0].slice(prompts[0].indexOf('Assignment JSON:\n') + 'Assignment JSON:\n'.length)).worker_token
    out.reported = (await rpc('report_completion', { lease_id: out.replacement, worker_token: token, label: 'g2' })).state
    out.afterReport = await rpc('inspect')
    await view('replacement submitted, native still running')
    for (const row of native.agents) row.status = 'idle'
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    out.afterIdle = await rpc('inspect')
    out.reviewMessages = [...native.appended, ...native.prompts]
    await view('replacement review pending')
    out.approve = await reviewCall({ lease_id: out.replacement, decision: 'approve', evidence: 'rework verified' })
    out.final = await rpc('inspect')
    await view('replacement finalized')
    await start('process')
    await view('after a new process')
    out.lease = lease_id
    $.agent.spawn = realSpawn
  }

  const BIND_LAUNCH_VARIANTS = ['race', 'exhausted', 'restart', 'manual', 'manual-restart', 'none', 'denied',
    'paused', 'disabled', 'restart-noninteractive', 'reported-first']
  if (scenario.startsWith('bind-launch-bound-') && BIND_LAUNCH_VARIANTS.includes(scenario.slice(18))) {
    // Concurrent finalization writes land between _bind_rework_launch's reads
    // and its launch_bound CAS. One write exercises in-request retry; 100 writes
    // exhaust every bounded attempt so a later boundary (or restart) must replay bind.
    const variant = scenario.slice(18)
    const exhausted = variant !== 'race'
    // A non-interactive session.start runs no pump: the first turn.complete is
    // the new process's first boundary that can replay the bind.
    const headless = variant === 'restart-noninteractive'
    const restartRecovery = variant === 'restart' || variant === 'manual-restart' || headless
    const manualFirst = variant === 'manual' || variant === 'manual-restart'
    const forced = variant === 'none' ? 'none' : variant === 'denied' ? 'spawn_denied' : null
    const seeded = await rpc('seed', { title: 'Bind race ' + scenario })
    const lease_id = seeded.lease_id
    await start('process')
    const prompts = []
    const realSpawn = $.agent.spawn
    $.agent.spawn = async args => { prompts.push(String(args.prompt || '')); return realSpawn(args) }
    const binds = () => calls.filter(c => c.event === 'bind').length
    // Every entry of this module's failure ring, as /work-mode parallel prints it.
    const ring = async () => {
      const lines = (await hooks['command:work-mode']($, { args: 'parallel', origin: { kind: 'composer' } })).text.split('\n')
      const at = lines.indexOf('Recent parallel failures in this session:')
      return at < 0 ? [] : lines.slice(at + 1).filter(line => line.startsWith('  ')).map(line => line.slice(0, 300))
    }
    const snapshot = async label => {
      const inspected = await rpc('inspect')
      const replacement = out.replacement
      const failures = (await hooks['command:work-mode']($, { args: 'parallel', origin: { kind: 'composer' } })).text
        .split('\n').filter(line => /rework-bind|rework-launch|restart-submitted|Parallel read failed/.test(line))
        .map(line => line.replace(/^\s+\S+ · /, '').slice(0, 240))
      ;(out.steps ||= []).push({ label,
        launch: replacement ? { state: inspected.reviews[lease_id]?.launch?.state,
          agent: inspected.reviews[lease_id]?.launch?.agent_id,
          manual_from: inspected.reviews[lease_id]?.launch?.manual_from ?? null,
          audit: (inspected.reviews[lease_id]?.launch?.audit || []).map(row => row.kind) } : null,
        lease: replacement ? inspected.leases[replacement] && { state: inspected.leases[replacement].state,
          host_id: inspected.leases[replacement].host_id, slot: inspected.leases[replacement].slot } : null,
        run: replacement ? inspected.runs[replacement] || null : null,
        manifest: replacement ? await rpc('workspace_manifest', { lease_id: replacement }) : null,
        reviews: Object.fromEntries(Object.entries(inspected.reviews).map(([key, row]) => [key, row.state])),
        binds: binds(), failures })
    }
    const modelPending = async () => ((await rpc('tool', { tool:'work_parallel_status',
      args:{ session_id:'claude:S1', _host:{} } })).review?.pending || []).map(row => [row.lease_id, row.state])
    const idempotentResend = async label => {
      // A second exact bind is a no-op for every store and the workspace manifest.
      const before = await rpc('stores', { lease_id:out.replacement })
      const sent = await rpc('measured_tool', { tool:'work_parallel_event', args:{ event:'bind',
        lease_id:out.replacement, expected_version:before.parallel.version, session_id:'claude:S1',
        _host:{ token:init.token, host_id:'agent-1', agent_name:native.spawns[0] } } })
      ;(out.idempotent ||= []).push({ label, error:sent.error, changed:Object.keys(sent.before)
        .filter(key => JSON.stringify(sent.before[key]) !== JSON.stringify(sent.after[key])) })
    }
    const personResolves = async () => {
      // The web console offers "Resolve stuck launch" for the unbound launch
      // (after 30 s there; the controller itself has no wait). A person confirms
      // it before the next pump boundary runs.
      const shown = (await rpc('web', { op:'launches' }))[lease_id]
      out.manual = await rpc('web', { op:'resolve', body:{ lease_id, launch_ref:shown?.launch_ref, confirmed:true,
        expected_finalization_version:(await rpc('inspect')).finalization_version } })
        .then(value => ({ state:value.state }), error => ({ error:error.message }))
      await snapshot('after the person requested resolution')
    }
    await rpc('arm_launch_bound_race', { attempts: exhausted ? 100 : 1 })
    out.decision = await reviewCall({ lease_id, decision: 'rework', evidence: 'race the launch bind' })
    out.replacement = (await rpc('inspect')).reviews[lease_id].replacement_lease_id
    out.raceAfterInitial = await rpc('launch_bound_race')
    out.bindsAfterInitial = binds()
    await snapshot('after rework dispatch and bind')
    const token = JSON.parse(prompts[0].slice(prompts[0].indexOf('Assignment JSON:\n') + 'Assignment JSON:\n'.length)).worker_token
    if (manualFirst) {
      await rpc('arm_launch_bound_race', { attempts: 0 })
      await personResolves()
    }
    if (forced) {
      // A ledger-bound lease whose launch is none or spawn_denied is not
      // resendable: four boundaries send no bind and record no failure.
      await rpc('arm_launch_bound_race', { attempts: 0 })
      out.forced = await rpc('force_launch_state', { lease_id, state: forced })
      await snapshot('after the launch was forced ' + forced)
      const ringBefore = await ring(), bindsBefore = binds()
      for (let i = 0; i < 4; i++) await turnComplete()
      const ringAfter = await ring()
      out.unresendable = { state: forced, bindSends: binds() - bindsBefore,
        newFailures: ringAfter.filter(line => !ringBefore.includes(line)), ringBefore, ringAfter }
      await snapshot('after four boundaries')
    } else if (restartRecovery) {
      // The fork reports while its launch and workspace are still unbound, then
      // the process dies with it. A fresh process must recover without a person.
      out.report = await rpc('report_completion', { lease_id:out.replacement, worker_token:token, label:'race' })
        .then(value => ({ state:value.state }), error => ({ error:error.message }))
      native.agents.length = 0
      await rpc('arm_launch_bound_race', { attempts: manualFirst ? 0 : 1 })
      const restartBefore = native.restartCalls.length
      await start('process', { interactive: !headless })
      out.raceAfterRecovery = await rpc('launch_bound_race')
      await snapshot('after process restart (session.start)')
      // Headless: only the mod may send the bind; a harness bind would repair it here.
      if (!headless) await idempotentResend('after restart recovery')
      else out.wakesAfterStart = { prompts: native.prompts.length, appended: native.appended.length }
      await hooks['turn.complete']($, {}, async e => e); await quiet()
      out.restartCalls = native.restartCalls.slice(restartBefore).map(row => ({ lease_id:row.lease_id, error:row.error }))
      await snapshot('after turn.complete in the new process')
      if (headless) {
        out.raceAfterRecovery = await rpc('launch_bound_race')
        out.wakesAfterFirst = { prompts: native.prompts.length, appended: native.appended.length }
        await turnComplete()
        out.restartCallsAfterSecond = native.restartCalls.slice(restartBefore).map(row => ({ lease_id:row.lease_id, error:row.error }))
        await snapshot('after a second turn.complete in the new process')
      }
    } else {
      if (variant === 'paused' || variant === 'disabled') {
        // Dispatch is stopped by the person. The exhausted bind is already a
        // started fork's bind, not a dispatch: the next boundary still replays it.
        await rpc('arm_launch_bound_race', { attempts: 0 })
        out.policy = await rpc('policy', { changes: variant === 'paused' ? { paused: true } : { enabled: false } })
        await turnComplete()
        await snapshot('after boundary recovery')
      } else if (variant === 'reported-first') {
        // No boundary passes before the fork reports and goes idle: the first
        // boundary must replay the bind and reconcile the idle fork.
        await rpc('arm_launch_bound_race', { attempts: 0 })
      } else if (manualFirst) {
        // The next ordinary boundary settles the request with the exact bind.
        await turnComplete()
        await snapshot('after boundary recovery')
      } else if (exhausted) {
        // The next ordinary boundary replays bind; one more conflict proves its own retry.
        await rpc('arm_launch_bound_race', { attempts: 1 })
        await hooks['turn.complete']($, {}, async e => e); await quiet()
        out.raceAfterRecovery = await rpc('launch_bound_race')
        await snapshot('after boundary recovery')
      }
      // In these variants only the mod itself may send a bind: a harness bind
      // here would repair the very state the variant measures.
      const gated = variant === 'paused' || variant === 'disabled' || variant === 'reported-first'
      if (!gated) await idempotentResend('after bind recovered')
      out.report = await rpc('report_completion', { lease_id:out.replacement, worker_token:token, label:'race' })
        .then(value => ({ state:value.state }), error => ({ error:error.message }))
      for (const row of native.agents) row.status = 'idle'
      await hooks['turn.complete']($, {}, async e => e); await quiet()
      await snapshot('after report and idle turn.complete')
      if (gated) {
        const status = await rpc('tool', { tool:'work_parallel_status', args:{ session_id:'claude:S1', _host:{} } })
        out.policyAtReview = { enabled: status.policy.enabled, paused: status.policy.paused }
        out.spawnsAtReview = [...native.spawns]
      }
    }
    if (!forced) {
      out.pendingBeforeApprove = await modelPending()
      out.approve = await reviewCall({ lease_id:out.replacement, decision:'approve', evidence:'rework verified' })
      out.pendingAfterApprove = await modelPending()
      await snapshot('after replacement approved')
    }
    out.bindResults = [...native.bindResults]
    out.reconcileErrors = [...native.reconcileErrors]
    out.bindCalls = binds()
    out.lease = lease_id
    $.agent.spawn = realSpawn
  }

  if (scenario === 'nonce-reload') {
    // A spawn throws and the fork never appears. A module reload in the same
    // process keeps the env nonce: absence is never resolved automatically.
    const seeded = await rpc('seed', { title: 'Reload nonce' })
    await start('process')
    const realSpawn = $.agent.spawn
    $.agent.spawn = async args => { native.spawns.push(args.name); throw new Error('spawn response lost') }
    out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'reload keeps the nonce' })
    $.agent.spawn = realSpawn
    out.replacement = (await rpc('inspect')).reviews[seeded.lease_id].replacement_lease_id
    await start('reload')
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    out.afterReload = await rpc('inspect')
    out.reloadSpawns = [...native.spawns]
    out.reloadAbsent = count('rework_launch_absent')
    // Only the person's confirmed request resolves it.
    native.answers.push('Request resolution')
    await hooks['ui.message']($, { data:{ action:'resolve-launch' } }, async e => e); await quiet()
    out.final = await rpc('inspect')
    out.spawns = [...native.spawns]
    out.absent = count('rework_launch_absent')
    out.manual = count('rework_launch_manual_resolve')
  }

  if (scenario === 'nonce-env-failure') {
    // Process 1 records an intent whose fork never appears. Process 2 cannot
    // read or set its env nonce: no absence resolution and no new intent.
    const first = await rpc('seed', { title: 'Env failure old' })
    await start('process')
    const realSpawn = $.agent.spawn
    $.agent.spawn = async args => { native.spawns.push(args.name); throw new Error('spawn response lost') }
    await reviewCall({ lease_id: first.lease_id, decision: 'rework', evidence: 'old process intent' })
    $.agent.spawn = realSpawn
    out.replacement = (await rpc('inspect')).reviews[first.lease_id].replacement_lease_id
    envFault.get = true
    const intentsBefore = count('rework_launch_intent')
    await start('process')
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    const second = await rpc('seed', { title: 'Env failure new' })
    out.decision = await reviewCall({ lease_id: second.lease_id, decision: 'rework', evidence: 'no nonce' })
    out.secondReplacement = (await rpc('inspect')).reviews[second.lease_id].replacement_lease_id
    envFault.get = false; envFault.set = true
    await start('process')
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    out.final = await rpc('inspect')
    out.spawns = [...native.spawns]
    out.absent = count('rework_launch_absent')
    out.newIntents = count('rework_launch_intent') - intentsBefore
    out.lease = first.lease_id; out.second = second.lease_id
    envFault.set = false
  }

  if (scenario === 'nonce-legacy') {
    // A legacy intent recorded with no host nonce is unknown to every process:
    // a new process proves it absent automatically, as before this change.
    const seeded = await rpc('seed', { title: 'Legacy nonce' })
    await start('process')
    const realSpawn = $.agent.spawn
    $.agent.spawn = async args => { native.spawns.push(args.name); throw new Error('spawn response lost') }
    await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'legacy intent' })
    $.agent.spawn = realSpawn
    await rpc('clear_intent_nonce', { lease_id: seeded.lease_id })
    out.legacy = await rpc('inspect')
    out.replacement = out.legacy.reviews[seeded.lease_id].replacement_lease_id
    await start('process')
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    // R2 B2: the automatic absence of a pre-nonce intent is refused (owner_unknown).
    out.automatic = await rpc('inspect')
    out.absent = count('rework_launch_absent')
    out.absentRefusals = (await rpc('failures'))?.failures?.filter(row => row.code === 'owner_unknown').length ?? null
    out.spawnsBeforeManual = [...native.spawns]
    // The person's resolution from the pane: the host checks its own list, the
    // router allows manual_resolve for an unknown owner, and one attempt 2 starts.
    native.answers.push('Request resolution')
    await hooks['ui.message']($, { data:{ action:'resolve-launch' } }, async e => e)
    await quiet()
    out.final = await rpc('inspect')
    out.spawns = [...native.spawns]
    out.manual = count('rework_launch_manual_resolve')
    out.requests = count('rework_launch_manual_request')
  }

  if (scenario === 'rework-restart') {
    const seeded = await rpc('seed', { title: 'Rework' })
    await start('process')
    out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'add the jitter test' })
    out.afterDecision = await rpc('inspect')
    out.spawnsAfterDecision = [...native.spawns]
    out.replacement = out.afterDecision.reviews[seeded.lease_id].replacement_lease_id
    // Reload after the first fork launched: the mod forgets its maps; the
    // native fork is still listed, which only the same process shows (probe f63b94ea).
    await start('reload')
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    out.afterRestart = await rpc('inspect')
    out.spawns = [...native.spawns]
    out.dispatches = count('rework_dispatch')
    out.intents = count('rework_launch_intent')
    out.observed = count('rework_spawn_observed')
  }

  if (scenario === 'intent-absent-restart') {
    // spawn throws and the fork never appears. This process holds the intent
    // (its spawn could still complete); a later process proves it absent.
    const seeded = await rpc('seed', { title: 'Lost' })
    await start('process')
    const realSpawn = $.agent.spawn
    $.agent.spawn = async args => { native.spawns.push(args.name); throw new Error('host process exited') }
    out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'redo' })
    $.agent.spawn = realSpawn
    out.afterCrash = await rpc('inspect')
    // Same process, across a session.start reset: the nonce is unchanged.
    await hooks['session.start']($, { isInteractive: true }, async e => e); await quiet()
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    out.sameProcess = await rpc('inspect')
    out.sameProcessSpawns = [...native.spawns]
    out.sameProcessAbsent = count('rework_launch_absent')
    out.sameProcessDispatches = count('rework_dispatch')
    await start('process')
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    out.final = await rpc('inspect')
    out.spawns = [...native.spawns]
    out.absent = count('rework_launch_absent')
    out.dispatches = count('rework_dispatch')
    out.replacement = out.final.reviews[seeded.lease_id].replacement_lease_id
    // The first attempt's fork shows up late: owned and stopped, never bound.
    native.agents.push(pluginForkRow('agent-late', native.spawns[0], 'running'))
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    out.afterLate = await rpc('inspect')
    out.stopped = native.toolCalls.filter(row => row.tool === 'TaskStop').map(row => row.task_id)
    out.lateNotice = await hooks['session.receive']($, { origin:{ kind:'task-notification' },
      text:'<task-notification><task-id>agent-late</task-id></task-notification>' }, async e => e)
  }

  const LATE_FORK_VARIANTS = ['failed-resend', 'after-review', 'after-approve', 'ended-window', 'ended-window-reload',
    'ended-window-reload-query-failed', 'ended-window-reload-revival']
  if (scenario.startsWith('late-fork-') && LATE_FORK_VARIANTS.includes(scenario.slice(10))) {
    // Attempt 1's spawn throws and its fork is never listed; a later process
    // proves it absent and starts attempt 2. Attempt 1's fork then shows up late.
    const variant = scenario.slice(10)
    const seeded = await rpc('seed', { title: 'Late fork ' + variant })
    const lease_id = seeded.lease_id
    await start('process')
    const realSpawn = $.agent.spawn
    $.agent.spawn = async args => { native.spawns.push(args.name); throw new Error('host process exited') }
    out.decision = await reviewCall({ lease_id, decision: 'rework', evidence: 'redo' })
    $.agent.spawn = realSpawn
    out.replacement = (await rpc('inspect')).reviews[lease_id].replacement_lease_id
    const stopped = () => native.toolCalls.filter(row => row.tool === 'TaskStop').map(row => row.task_id)
    const lateRing = async () => (await hooks['command:work-mode']($, { args: 'parallel', origin: { kind: 'composer' } })).text
      .split('\n').filter(line => line.includes('rework-late-agent')).length
    const step = async label => {
      const inspected = await rpc('inspect')
      ;(out.steps ||= []).push({ label, launch: { state: inspected.reviews[lease_id]?.launch?.state,
          attempt: inspected.reviews[lease_id]?.launch?.attempt, agent: inspected.reviews[lease_id]?.launch?.agent_id },
        lease: { state: inspected.leases[out.replacement]?.state, host_id: inspected.leases[out.replacement]?.host_id,
          dispatch_name: inspected.leases[out.replacement]?.dispatch_name },
        reviews: Object.fromEntries(Object.entries(inspected.reviews).map(([key, row]) => [key, row.state])),
        spawns: [...native.spawns], stopped: stopped(), lateRing: await lateRing(),
        // Whether the late fork's native notification is kept out of main now.
        lateNoticeConsumed: 'consumed' in await hooks['session.receive']($, { origin:{ kind:'task-notification' },
          text:'<task-notification><task-id>agent-late</task-id></task-notification>' }, async e => e),
        binds: native.bindResults.map(row => row.error) })
    }
    await step('after the thrown spawn')
    if (variant === 'failed-resend') {
      // Attempt 2's bind commits the ledger half only: every launch_bound conflicts.
      await rpc('arm_launch_bound_race', { attempts: 100 })
      await start('process')
      await step('after the new process dispatched attempt 2')
      // Attempt 1's fork appears while the bind resend still fails.
      native.agents.push(pluginForkRow('agent-late', native.spawns[0], 'running'))
      await rpc('arm_launch_bound_race', { attempts: 100 })
      await turnComplete()
      await step('after a boundary whose bind resend failed')
      await rpc('arm_launch_bound_race', { attempts: 0 })
      await turnComplete()
      await step('after a boundary whose bind resend succeeded')
    } else {
      // Attempt 2 binds normally, reports and goes idle: its own review is
      // published, which hides the predecessor's rework_ready row from the projection.
      await start('process')
      await step('after the new process dispatched attempt 2')
      const prompt = native.spawnPrompts.get(native.spawns[1]) || ''
      const token = JSON.parse(prompt.slice(prompt.indexOf('Assignment JSON:\n') + 'Assignment JSON:\n'.length)).worker_token
      out.reported = (await rpc('report_completion', { lease_id: out.replacement, worker_token: token, label: 'late' })).state
      for (const row of native.agents) row.status = 'idle'
      await turnComplete()
      const projected = async () => ((await rpc('tool', { tool:'work_parallel_status',
        args:{ session_id:'claude:S1', _host:{} } })).review?.pending || []).map(row => [row.lease_id, row.state])
      out.projectedAfterReview = await projected()
      await step('after the replacement published its review')
      const windowed = variant === 'ended-window' || variant === 'ended-window-reload'
        || variant === 'ended-window-reload-query-failed' || variant === 'ended-window-reload-revival'
      if (variant === 'after-approve' || windowed) {
        out.approve = await reviewCall({ lease_id: out.replacement, decision: 'approve', evidence: 'rework verified' })
        out.projectedAfterApprove = await projected()
        await step('after the replacement was approved')
      }
      if (windowed) {
        // Nine later leases end after the replacement. The status read lists the
        // last 8 ended leases only, so the replacement is in none of its rows.
        out.laterStates = []
        await rpc('policy', { changes: { launches_per_minute: 60 } })
        for (let i = 0; i < 9; i++) {
          const later = await rpc('seed', { title: 'Later ' + i })
          const decided = await reviewCall({ lease_id: later.lease_id, decision: 'approve', evidence: 'later ' + i })
          out.laterStates.push(decided?.result ? JSON.parse(decided.result).state : { deny: String(decided?.deny).slice(0, 400) })
        }
        // What the mod's own status read carries now (host token, as the mod sends it).
        const status = await rpc('tool', { tool:'work_parallel_status',
          args:{ session_id:'claude:S1', _host:{ token:init.token } } })
        const groups = Object.entries(status.forks || {}).filter(([, rows]) => Array.isArray(rows))
        out.statusRows = { open: Object.keys(status.leases || {}),
          groups: Object.fromEntries(groups.map(([name, rows]) => [name, rows.map(row => row.lease_id)])) }
        // A hot reload in the same process: module maps are lost, native forks stay listed.
        if (variant.startsWith('ended-window-reload')) await start('reload')
        if (variant === 'ended-window-reload-query-failed') native.failAttribution = 1
        await step('after nine later leases ended')
      }
      // Only now does attempt 1's fork show up, still running in the retained worktree.
      native.agents.push(pluginForkRow('agent-late', native.spawns[0], 'running'))
      await turnComplete()
      await step('after one boundary with the late fork listed')
      await turnComplete()
      await step('after a second boundary')
      if (variant === 'ended-window-reload-revival') {
        // TaskStop leaves a native fork `killed`; it stays listed (measured, 2.1.323).
        for (const id of native.toolCalls.filter(row => row.tool === 'TaskStop').map(row => row.task_id)) {
          const stoppedRow = native.agents.find(a => a.id === id)
          if (stoppedRow) stoppedRow.status = 'killed'
        }
        // A second hot reload: no module map remembers either fork any more.
        await start('reload')
        const replacementFork = native.agents.find(a => a.name === native.spawns[1])
        const settled = new Map(native.agents.map(a => [a.id, a.status]))
        // Each probe starts from the same native statuses.
        const probe = async (to, options) => {
          for (const a of native.agents) if (settled.has(a.id)) a.status = settled.get(a.id)
          return revivalProbe(to, options)
        }
        out.revival = {
          staleById: await probe('agent-late'),
          staleByName: await probe(native.spawns[0]),
          doneReplacementById: await probe(replacementFork.id),
          doneReplacementByName: await probe(native.spawns[1]),
          otherPluginToStale: await probe('agent-late', { origin: 'other-plugin' }),
          workerToStale: await probe('agent-late', { agentId: replacementFork.id }),
        }
        out.revivalStatuses = native.agents.map(a => ({ id: a.id, name: a.name, status: a.status }))
        out.revivalStops = native.toolCalls.filter(row => row.tool === 'TaskStop').map(row => row.task_id)
      }
    }
    out.attributions = native.attributions.map(row => ({ ...row }))
    out.spawns = [...native.spawns]
    out.listCalls = native.listCalls
    out.lease = lease_id
  }

  const REPORT_REFUSED_VARIANTS = ['unbound', 'unbound-paused']
  if (scenario.startsWith('report-refused-') && REPORT_REFUSED_VARIANTS.includes(scenario.slice(15))) {
    // The rework fork starts, but its bind never reaches the backend: the lease
    // stays reserved, so the fork's own report is refused as not active.
    const variant = scenario.slice(15)
    const seeded = await rpc('seed', { title: 'Refused report ' + variant })
    const lease_id = seeded.lease_id
    await start('process')
    const snap = async label => {
      const inspected = await rpc('inspect')
      ;(out.steps ||= []).push({ label, launch: { state: inspected.reviews[lease_id]?.launch?.state,
          agent: inspected.reviews[lease_id]?.launch?.agent_id },
        lease: { state: inspected.leases[out.replacement]?.state, host_id: inspected.leases[out.replacement]?.host_id },
        reviews: Object.fromEntries(Object.entries(inspected.reviews).map(([key, row]) => [key, row.state])),
        binds: native.bindResults.length, bindSends: count('bind'), spawns: [...native.spawns] })
    }
    native.failBind = 1000
    out.decision = await reviewCall({ lease_id, decision: 'rework', evidence: 'the bind is lost' })
    out.replacement = (await rpc('inspect')).reviews[lease_id].replacement_lease_id
    // The router connection is back; no boundary has passed since the dispatch.
    native.failBind = 0
    await snap('after the rework dispatch lost its bind')
    if (variant === 'unbound-paused') out.policy = await rpc('policy', { changes: { paused: true } })
    const prompt = native.spawnPrompts.get(native.spawns[0]) || ''
    const token = JSON.parse(prompt.slice(prompt.indexOf('Assignment JSON:\n') + 'Assignment JSON:\n'.length)).worker_token
    // The fork's work_parallel_report passes the mod's tool.call hook; the
    // engine's own call then reaches the real backend with the fork's capability.
    const forkReports = async label => {
      const engineCall = async () => rpc('report_completion', { lease_id: out.replacement, worker_token: token, label: 'refused' })
        .then(value => ({ result: JSON.stringify({ state: value.state }) }),
          error => ({ isError: true, ...text(error.message) }))
      engineCall.origin = { plugin: 'engine' }
      const answered = await hooks['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_report',
        tool_use_id: 't-' + Date.now(), agentId: 'agent-1', lease_id: out.replacement, event: 'completion' }, engineCall)
      await quiet()
      ;(out.reports ||= []).push({ label, refused: answered?.isError === true || Boolean(answered?.deny),
        answer: answered?.isError === true ? answered.content[0].text.slice(0, 200) : JSON.parse(answered.result).state })
      await snap(label)
    }
    await forkReports('after the first report')
    await forkReports('after the second report')
    for (const row of native.agents) row.status = 'idle'
    await turnComplete()
    await snap('after the idle turn.complete')
    const status = await rpc('tool', { tool:'work_parallel_status', args:{ session_id:'claude:S1', _host:{} } })
    out.policyAtEnd = { enabled: status.policy.enabled, paused: status.policy.paused }
    out.lease = lease_id
  }

  const STOPPED_POLICY_VARIANTS = ['bind-lost-paused', 'bind-lost-disabled', 'absent-paused']
  if (scenario.startsWith('stopped-policy-') && STOPPED_POLICY_VARIANTS.includes(scenario.slice(15))) {
    // A reserved replacement whose launch is unresolved, under a policy the
    // person stopped. Reconciling that launch is not a dispatch; a new attempt is.
    const variant = scenario.slice(15)
    const seeded = await rpc('seed', { title: 'Stopped policy ' + variant })
    const lease_id = seeded.lease_id
    await start('process')
    const snap = async label => {
      const inspected = await rpc('inspect')
      ;(out.steps ||= []).push({ label, launch: { state: inspected.reviews[lease_id]?.launch?.state,
          attempt: inspected.reviews[lease_id]?.launch?.attempt, agent: inspected.reviews[lease_id]?.launch?.agent_id },
        lease: { state: inspected.leases[out.replacement]?.state, host_id: inspected.leases[out.replacement]?.host_id },
        reviews: Object.fromEntries(Object.entries(inspected.reviews).map(([key, row]) => [key, row.state])),
        bindSends: count('bind'), dispatches: count('rework_dispatch'), absent: count('rework_launch_absent'),
        spawns: [...native.spawns] })
    }
    const stop = variant === 'bind-lost-disabled' ? { enabled: false } : { paused: true }
    if (variant === 'absent-paused') {
      // Attempt 1's spawn throws and its fork is never listed.
      const realSpawn = $.agent.spawn
      $.agent.spawn = async args => { native.spawns.push(args.name); throw new Error('host process exited') }
      out.decision = await reviewCall({ lease_id, decision: 'rework', evidence: 'redo' })
      $.agent.spawn = realSpawn
      out.replacement = (await rpc('inspect')).reviews[lease_id].replacement_lease_id
      await snap('after the thrown spawn')
      out.policy = await rpc('policy', { changes: stop })
      // A later process proves the launch absent; the policy still forbids a new attempt.
      await start('process')
      await snap('after a new process under the stopped policy')
      await turnComplete()
      await snap('after one boundary under the stopped policy')
      out.resumed = await rpc('policy', { changes: { paused: false } })
      await turnComplete()
      await snap('after the policy resumed')
    } else {
      // The fork starts, but the dispatch's bind never reaches the backend.
      native.failBind = 1000
      out.decision = await reviewCall({ lease_id, decision: 'rework', evidence: 'the bind is lost' })
      out.replacement = (await rpc('inspect')).reviews[lease_id].replacement_lease_id
      native.failBind = 0
      await snap('after the rework dispatch lost its bind')
      out.policy = await rpc('policy', { changes: stop })
      await turnComplete()
      await snap('after one boundary under the stopped policy')
      const prompt = native.spawnPrompts.get(native.spawns[0]) || ''
      const token = JSON.parse(prompt.slice(prompt.indexOf('Assignment JSON:\n') + 'Assignment JSON:\n'.length)).worker_token
      out.report = await rpc('report_completion', { lease_id: out.replacement, worker_token: token, label: 'stopped' })
        .then(value => ({ state: value.state }), error => ({ error: error.message }))
      for (const row of native.agents) row.status = 'idle'
      await turnComplete()
      await snap('after the report and the idle turn.complete')
    }
    const status = await rpc('tool', { tool:'work_parallel_status', args:{ session_id:'claude:S1', _host:{} } })
    out.policyAtEnd = { enabled: status.policy.enabled, paused: status.policy.paused }
    out.lease = lease_id
  }

  if (scenario === 'list-calls') {
    // $.agent.list() calls per boundary through one ordinary rework: the
    // replacement runs, reports, is reviewed and approved.
    const seeded = await rpc('seed', { title: 'List calls' })
    const lease_id = seeded.lease_id
    let mark = native.listCalls
    const measure = label => { (out.listCalls ||= []).push([label, native.listCalls - mark]); mark = native.listCalls }
    await start('process')
    measure('session.start, one result awaiting review')
    out.decision = await reviewCall({ lease_id, decision: 'rework', evidence: 'add the missing case' })
    out.replacement = (await rpc('inspect')).reviews[lease_id].replacement_lease_id
    measure('rework decision: dispatch and bind')
    await turnComplete()
    measure('turn.complete, replacement running')
    await hooks['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_report', agentId: 'w' }, engine({ ok: true }))
    await quiet()
    measure('worker report (pump only), replacement running')
    const prompt = native.spawnPrompts.get(native.spawns[0]) || ''
    const token = JSON.parse(prompt.slice(prompt.indexOf('Assignment JSON:\n') + 'Assignment JSON:\n'.length)).worker_token
    out.reported = (await rpc('report_completion', { lease_id: out.replacement, worker_token: token, label: 'lists' })).state
    for (const row of native.agents) row.status = 'idle'
    await turnComplete()
    measure('turn.complete, replacement submitted and idle')
    await turnComplete()
    measure('turn.complete, replacement awaiting review')
    out.approve = JSON.parse((await reviewCall({ lease_id: out.replacement, decision: 'approve', evidence: 'rework verified' })).result).state
    measure('approve decision')
    await turnComplete()
    measure('turn.complete, every lease ended')
    await hooks['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_report', agentId: 'w' }, engine({ ok: true }))
    await quiet()
    measure('worker report (pump only), every lease ended')
    out.lease = lease_id
  }

  if (scenario === 'coalesced-resend') {
    // An exhausted rework bind whose resend keeps failing. A worker report's
    // pump is held at its first status read; a turn.complete then tries the
    // resend itself and its pump call is coalesced into the held pump.
    const seeded = await rpc('seed', { title: 'Coalesced resend' })
    const lease_id = seeded.lease_id
    await start('process')
    await rpc('arm_launch_bound_race', { attempts: 100 })
    out.decision = await reviewCall({ lease_id, decision: 'rework', evidence: 'race the launch bind' })
    out.replacement = (await rpc('inspect')).reviews[lease_id].replacement_lease_id
    const binds = () => calls.filter(c => c.event === 'bind').length
    out.bindsAfterDispatch = binds()
    await rpc('arm_launch_bound_race', { attempts: 100 })
    let release
    const gate = { held: false, promise: new Promise(resolve => { release = resolve }) }
    native.statusGate = gate
    await hooks['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_report', agentId: 'w' }, engine({ ok: true }))
    for (let i = 0; i < 500 && !gate.held; i++) await sleep(20)
    out.pumpHeld = gate.held
    await hooks['turn.complete']($, {}, async e => e)
    out.bindsAfterTurnComplete = binds()
    release()
    await quiet()
    out.bindsAfterPump = binds()
    out.bindErrors = [...new Set(native.bindResults.map(row => row.error))]
    const inspected = await rpc('inspect')
    out.launch = inspected.reviews[lease_id]?.launch?.state
    out.lease = lease_id
  }

  if (scenario === 'turn-complete-broken-lease' || scenario === 'turn-complete-refused-lease') {
    // Two workers reported and are listed idle. The lease the mod reaches first
    // either has lost its retained worktree (broken: Item 2 publishes a stranded
    // row) or has its reconcile refused by the router (refused: item 7 isolation).
    const broken = scenario === 'turn-complete-broken-lease'
    const pair = await rpc('seed_reported_pair')
    // The mod walks its bound leases in the status read's order.
    const order = Object.keys((await rpc('tool', { tool:'work_parallel_status',
      args:{ session_id:'claude:S1', _host:{ token:init.token } } })).leases)
    const stranded = pair.leases.find(row => row.lease_id === order[0])
    const healthy = pair.leases.find(row => row.lease_id !== order[0])
    const role = id => id === stranded.agent_id ? 'stranded' : id === healthy.agent_id ? 'healthy' : id
    out.order = order.map(id => id === stranded.lease_id ? 'stranded' : id === healthy.lease_id ? 'healthy' : 'other')
    if (broken) out.broken = await rpc('break_worktree', { lease_id: stranded.lease_id })
    else native.failReconcile.add(stranded.lease_id)
    for (const row of pair.leases) native.agents.push({ id: row.agent_id, name: row.agent_id, status: 'idle' })
    await start('process')
    const projected = async () => (await rpc('tool', { tool:'work_parallel_status',
      args:{ session_id:'claude:S1', _host:{ token:init.token } } })).review?.pending || []
    const boundary = async label => {
      const before = { fills: count('reserve_next'), reconciles: count('reconcile'),
        wakes: native.prompts.length + native.appended.length, errors: native.reconcileErrors.length,
        appended: native.appended.length, prompts: native.prompts.length }
      if (label !== 'after session.start') await turnComplete()
      const inspected = await rpc('inspect')
      const shown = (await hooks['command:work-mode']($, { args: 'parallel', origin: { kind: 'composer' } })).text.split('\n')
      ;(out.steps ||= []).push({ label,
        // Review payloads appended to main, and wake-only prompts, separately.
        appendedReviews: native.appended.slice(before.appended).flatMap(text => pair.leases
          .filter(row => text.includes(row.lease_id)).map(row => row.lease_id === stranded.lease_id ? 'stranded' : 'healthy')).sort(),
        prompts: native.prompts.length - before.prompts,
        leases: Object.fromEntries(pair.leases.map(row => [role(row.agent_id), inspected.leases[row.lease_id]?.state])),
        reviews: Object.fromEntries(pair.leases.map(row => [role(row.agent_id), inspected.reviews[row.lease_id]?.state ?? null])),
        // The auto-fill attempt is sent only by the pump.
        fills: count('reserve_next') - before.fills, reconciles: count('reconcile') - before.reconciles,
        wakes: native.prompts.length + native.appended.length - before.wakes,
        errors: native.reconcileErrors.slice(before.errors).map(row => [row.lease_id === stranded.lease_id ? 'stranded' : 'healthy', row.error]),
        stopped: native.toolCalls.filter(row => row.tool === 'TaskStop').map(row => role(row.task_id)),
        ring: shown.filter(line => line.includes('turn-reconcile')).map(line => line.replace(/^\s+\S+ · /, '').slice(0, 200)),
        pending: (await projected()).map(row => ({ role: row.lease_id === stranded.lease_id ? 'stranded' : 'healthy',
          state: row.state, stranded: row.stranded || null, blocked: row.blocked || null, workspace: row.workspace || null,
          artifact: row.artifact ?? null, summary: row.summary ?? null })) })
    }
    await boundary('after session.start')
    await boundary('after the first turn.complete')
    await boundary('after the second turn.complete')
    if (broken) {
      // What main and the person see for the stranded row.
      out.statusText = (await hooks['command:work-mode']($, { args: 'parallel', origin: { kind: 'composer' } })).text
      out.appended = native.appended.filter(text => text.includes(stranded.lease_id))
      out.web = (await rpc('web', { op: 'parallel' })).parallel_reviews
      const finalBefore = (await rpc('inspect')).finalization_version
      out.approveStranded = await reviewCall({ lease_id: stranded.lease_id, decision: 'approve', evidence: 'looks done' })
      out.finalAfterApprove = (await rpc('inspect')).finalization_version - finalBefore
      out.reworkStranded = await reviewCall({ lease_id: stranded.lease_id, decision: 'rework', evidence: 'redo in a fresh workspace' })
      out.approveHealthy = await reviewCall({ lease_id: healthy.lease_id, decision: 'approve', evidence: 'verified' })
      const inspected = await rpc('inspect')
      // stranded-v1: the stranded result lives in the sidecar; schema 5 holds no row for it.
      out.strandedStorage = { sidecar: inspected.stranded?.rows?.[stranded.lease_id] ?? null,
        schema5: inspected.reviews[stranded.lease_id] ?? null, sidecarAvailable: inspected.stranded !== null }
      out.replacement = (inspected.stranded?.rows?.[stranded.lease_id]?.replacement_lease_id
        ?? inspected.reviews[stranded.lease_id]?.replacement_lease_id) ?? null
      out.replacementLease = out.replacement ? inspected.leases[out.replacement] : null
      out.replacementWorkspace = out.replacement ? await rpc('lease_workspace', { lease_id: out.replacement }) : null
      out.spawns = [...native.spawns]
    }
    out.strandedLease = stranded.lease_id; out.healthyLease = healthy.lease_id
    out.brokenPath = out.broken?.path ?? null
  }

  if (scenario === 'stranded-no-report' || scenario === 'stranded-no-report-at-limit') {
    // A running fork that never reports, in a retained worktree that is gone.
    const seeded = await rpc('seed_running')
    if (scenario.endsWith('-at-limit')) await rpc('policy', { changes: { max_rework: 0 } })
    out.broken = await rpc('break_worktree', { lease_id: seeded.lease_id })
    const lease_id = seeded.lease_id, agent = seeded.agent_id
    native.agents.push({ id: agent, name: agent, status: 'running', type: 'fork' })
    await start('process')
    const status = () => rpc('tool', { tool:'work_parallel_status', args:{ session_id:'claude:S1', _host:{ token:init.token } } })
    const snap = async label => {
      const inspected = await rpc('inspect')
      const rows = ((await status()).review?.pending || []).filter(row => row.lease_id === lease_id)
      const review = inspected.reviews[lease_id]
      const side = inspected.stranded?.rows?.[lease_id]
      ;(out.steps ||= []).push({ label, lease: inspected.leases[lease_id], run: inspected.runs[lease_id] || null,
        review: review ? { state: review.state, decision: review.decision, wake: review.wake?.state ?? null,
          replacement: review.replacement_lease_id ?? null } : null,
        sidecar: side ? { state: side.state, wake: side.wake, replacement: side.replacement_lease_id ?? null } : null,
        rows, sends: native.sends.filter(row => row?.to?.agentId === agent).length,
        stopped: native.toolCalls.filter(row => row.tool === 'TaskStop' && row.task_id === agent).length,
        salvages: count('host_salvage'), unreported: count('unreported'),
        wakes: native.prompts.length + native.appended.length, spawns: [...native.spawns] })
    }
    await snap('after session.start')
    // The fork's native turn ends without a durable exit receipt, twice.
    native.agents.find(row => row.id === agent).status = 'completed'
    await hooks['turn.complete']($, { agentId: agent, reason: 'answer' }, async e => e); await quiet()
    await snap('after the first native turn end')
    await hooks['turn.complete']($, { agentId: agent, reason: 'answer' }, async e => e); await quiet()
    await snap('after the second native turn end')
    await turnComplete()
    await snap('after a main boundary')
    // The capability is revoked: the fork's own late report is refused.
    out.lateReport = await rpc('report_completion', { lease_id, worker_token: seeded.worker_token, label: 'late' })
      .then(value => ({ value }), error => ({ error: String(error.message).slice(0, 300) }))
    out.statusText = (await hooks['command:work-mode']($, { args: 'parallel', origin: { kind: 'composer' } })).text
    out.appended = native.appended.filter(text => text.includes(lease_id))
    out.web = (await rpc('web', { op: 'parallel' })).parallel_reviews
    const finalBefore = (await rpc('inspect')).finalization_version
    out.approve = await reviewCall({ lease_id, decision: 'approve', evidence: 'looks done' })
    out.finalAfterApprove = (await rpc('inspect')).finalization_version - finalBefore
    out.rework = await reviewCall({ lease_id, decision: 'rework', evidence: 'redo in a fresh workspace' })
    await snap('after the rework decision')
    await turnComplete()
    await snap('after a repeated boundary')
    await turnComplete()
    await snap('after another repeated boundary')
    const inspected = await rpc('inspect')
    out.strandedStorage = { sidecar: inspected.stranded?.rows?.[lease_id] ?? null,
      schema5: inspected.reviews[lease_id] ?? null, sidecarAvailable: inspected.stranded !== null }
    out.replacement = (inspected.stranded?.rows?.[lease_id]?.replacement_lease_id
      ?? inspected.reviews[lease_id]?.replacement_lease_id) ?? null
    out.replacementLease = out.replacement ? inspected.leases[out.replacement] : null
    out.replacementWorkspace = out.replacement ? await rpc('lease_workspace', { lease_id: out.replacement }) : null
    const lastPrompt = native.spawnPrompts.get(native.spawns[native.spawns.length - 1]) || ''
    out.spawnPromptHasWarning = lastPrompt.includes('Retained worktree warning')
    out.spawnPromptMentionsBrokenPath = lastPrompt.includes(out.broken.path)
    out.lease = lease_id; out.agent = agent; out.brokenPath = out.broken.path
  }

  if (scenario === 'stranded-superseded') {
    // checkpoint3: a no-report stranded row, then a real 57dbfb3 process (which
    // never reads the sidecar) redispatches the same item. The new build shows
    // the row as superseded: an ended audit row, never a decision.
    const seeded = await rpc('seed_running')
    out.broken = await rpc('break_worktree', { lease_id: seeded.lease_id })
    const lease_id = seeded.lease_id, agent = seeded.agent_id
    native.agents.push({ id: agent, name: agent, status: 'running', type: 'fork', description: 'Work n1' })
    // Busy main: the Stop gate is active, as in stop-gate-refused.
    await start('process', { busy: true })
    native.agents.find(row => row.id === agent).status = 'completed'
    await hooks['turn.complete']($, { agentId: agent, reason: 'answer' }, async e => e); await quiet()
    await hooks['turn.complete']($, { agentId: agent, reason: 'answer' }, async e => e); await quiet()
    await turnComplete()
    const status = async () => rpc('tool', { tool:'work_parallel_status', args:{ session_id:'claude:S1', _host:{ token:init.token } } })
    // Host read: decidable rows in review.pending, ended audit rows in review.ended (lead ruling).
    const mine = async () => ((await status()).review?.pending || []).filter(row => row.lease_id === lease_id)
    const mineEnded = async () => ((await status()).review?.ended || []).filter(row => row.lease_id === lease_id)
    // The model's own status call carries no host authentication.
    const modelView = async () => {
      const review = (await rpc('tool', { tool:'work_parallel_status', args:{ session_id:'claude:S1' } })).review || {}
      return { pendingSuperseded: (review.pending || []).filter(row => row.state === 'superseded'
          || row.stranded?.phase === 'superseded').length,
        hasEnded: Object.prototype.hasOwnProperty.call(review, 'ended'), endedCount: review.ended_count ?? null,
        mentionsLease: JSON.stringify(review).includes(lease_id) }
    }
    const stopShows = async () => {
      const r = await hooks['classic.Stop']($, { session_id: 'S1' }, async () => ({})); await quiet()
      return typeof r?.block === 'string' && r.block.includes(lease_id)
    }
    out.before = { rows: (await mine()).map(row => ({ state: row.state, phase: row.stranded?.phase ?? null })),
      appended: native.appended.filter(text => text.includes(lease_id)).length, stopShows: await stopShows(),
      sidecar: (await rpc('inspect')).stranded?.rows?.[lease_id] ?? null }
    const repo = await rpc('repo_path')
    out.beta = await rpc('beta', { calls: [['work_parallel_event', { event: 'reserve', item_id: seeded.item_id, priority: 'P1',
      host: { fork: true, workspace_repo: repo } }]] })
    const betaLease = out.beta[0]?.ok?.lease_id ?? null
    out.betaLease = betaLease
    const appendedBefore = native.appended.length, promptsBefore = native.prompts.length
    await turnComplete()
    await turnComplete()
    out.after = { pending: (await mine()).map(row => ({ state: row.state, phase: row.stranded?.phase ?? null })),
      rows: (await mineEnded()).map(row => ({ state: row.state, stranded: row.stranded ?? null,
        keys: Object.keys(row).sort(), workspace: row.workspace ?? null, blocked: row.blocked ?? null })),
      model: await modelView(),
      appendedForLease: native.appended.slice(appendedBefore).filter(text => text.includes(lease_id)).length,
      promptsForLease: native.prompts.slice(promptsBefore).filter(text => text.includes(lease_id)).length,
      stopShows: await stopShows(), sidecar: (await rpc('inspect')).stranded?.rows?.[lease_id] ?? null }
    out.statusText = (await hooks['command:work-mode']($, { args: 'parallel', origin: { kind: 'composer' } })).text
    const web = await rpc('web', { op: 'parallel' })
    out.web = { reviews: (web.parallel_reviews || []).filter(row => row.lease_id === lease_id),
      superseded: (web.parallel_reviews_ended || []).filter(row => row.lease_id === lease_id) }
    const finalBefore = (await rpc('inspect')).finalization_version
    out.approve = await reviewCall({ lease_id, decision: 'approve', evidence: 'looks done' })
    out.rework = await reviewCall({ lease_id, decision: 'rework', evidence: 'redo it' })
    out.finalDelta = (await rpc('inspect')).finalization_version - finalBefore
    out.stopAfterRefusal = await stopShows()
    // The superseding execution ends: the beta process releases its unbound
    // reservation (a real event). The ended row must stay visible.
    out.betaRelease = await rpc('beta', { calls: [['work_parallel_event', { event: 'release', lease_id: betaLease,
      host: { proof: 'beta launch never started' } }]] })
    const appendedGone = native.appended.length, promptsGone = native.prompts.length
    await turnComplete()
    const webGone = await rpc('web', { op: 'parallel' })
    out.afterWinnerGone = { rows: (await mine()).map(row => ({ state: row.state, phase: row.stranded?.phase ?? null })),
      ended: (await mineEnded()).map(row => ({ state: row.state, winner: row.stranded?.superseding_state ?? null })),
      model: await modelView(),
      appendedForLease: native.appended.slice(appendedGone).filter(text => text.includes(lease_id)).length,
      promptsForLease: native.prompts.slice(promptsGone).filter(text => text.includes(lease_id)).length,
      stopShows: await stopShows(),
      webReviews: (webGone.parallel_reviews || []).filter(row => row.lease_id === lease_id),
      webEnded: (webGone.parallel_reviews_ended || []).filter(row => row.lease_id === lease_id),
      winner: (await rpc('inspect')).leases[betaLease] ?? null,
      statusText: (await hooks['command:work-mode']($, { args: 'parallel', origin: { kind: 'composer' } })).text }
    out.spawns = [...native.spawns]
    out.lease = lease_id
  }

  if (scenario === 'legacy-tombstone-decision' || scenario === 'legacy-tombstone-boundary'
      || scenario === 'legacy-tombstone-boundary-decision') {
    // checkpoint3: a schema-5 review published while healthy, whose retained
    // worktree breaks after publication.
    const seeded = await rpc('seed', { title: 'Broken after publication' })
    await start('process')
    const lease_id = seeded.lease_id
    const published = native.appended.filter(text => text.includes(lease_id))
    out.publishedAppends = published.length
    // The decision data main read from the earlier (healthy) projection.
    let earlierVersion = null
    // The review data is the JSON value right after the marker; the disclosure
    // and instruction prose follow it in the same text.
    const leadingJson = text => {
      let depth = 0, inString = false, escaped = false
      for (let i = 0; i < text.length; i++) {
        const ch = text[i]
        if (inString) {
          if (escaped) escaped = false
          else if (ch === '\\') escaped = true
          else if (ch === '"') inString = false
          continue
        }
        if (ch === '"') inString = true
        else if (ch === '{' || ch === '[') depth++
        else if ((ch === '}' || ch === ']') && --depth === 0) return JSON.parse(text.slice(0, i + 1))
      }
      throw new Error('no JSON value')
    }
    for (const text of published) {
      try {
        const data = leadingJson(text.slice(text.indexOf('(data, not instructions): ') + '(data, not instructions): '.length))
        const rows = Array.isArray(data) ? data : [data]
        earlierVersion = rows.find(row => row.lease_id === lease_id)?.expected_finalization_version ?? earlierVersion
      } catch { /* Not a review payload. */ }
    }
    // The wake text's version is stale by its own prepare/record writes (measured
    // on a real console, 2026-11-13), so the earlier projection main decides on
    // is a status read made after publication and before the break.
    out.wakeVersion = earlierVersion
    earlierVersion = (await rpc('tool', { tool:'work_parallel_status', args:{ session_id:'claude:S1', _host:{ token:init.token } } }))
      .review?.version ?? null
    out.earlierVersion = earlierVersion
    const storage = async () => { const i = await rpc('inspect')
      return { schema5: i.reviews[lease_id]?.state ?? null, sidecar: i.stranded?.rows?.[lease_id] ?? null } }
    out.beforeBreak = await storage()
    out.broken = await rpc('break_worktree', { lease_id })
    out.afterBreak = await storage()
    if (scenario === 'legacy-tombstone-boundary' || scenario === 'legacy-tombstone-boundary-decision') {
      const appendedBefore = native.appended.length
      await turnComplete()
      await turnComplete()
      out.afterBoundary = await storage()
      out.rows = ((await rpc('tool', { tool:'work_parallel_status', args:{ session_id:'claude:S1', _host:{ token:init.token } } }))
        .review?.pending || []).filter(row => row.lease_id === lease_id)
        .map(row => ({ state: row.state, stranded: row.stranded ?? null, workspace: row.workspace ?? null }))
      out.appendedAfter = native.appended.slice(appendedBefore).filter(text => text.includes(lease_id))
      out.statusText = (await hooks['command:work-mode']($, { args: 'parallel', origin: { kind: 'composer' } })).text
      // Lead ruling X1: a real classic.Stop at the end of a main turn, then its
      // Stop-block continuation. Each holds main and re-presents the undecided row.
      await hooks['turn.start']($, {}, async e => e); await quiet()
      out.stops = []
      for (const continuing of [false, true]) {
        const stop = await hooks['classic.Stop']($, { session_id: 'S1', ...(continuing ? { stop_hook_active: true } : {}) },
          async () => ({})); await quiet()
        out.stops.push({ continuing, block: typeof stop?.block === 'string', presents: typeof stop?.block === 'string'
          && stop.block.includes(lease_id), head: typeof stop?.block === 'string' ? stop.block.slice(0, 120) : null })
      }
      out.afterStop = await storage()
      if (scenario === 'legacy-tombstone-boundary') {
        // A new host process: its start-time observation of the submitted lease.
        await start('process')
        out.afterRestart = await storage()
      } else {
        // Main decides on the projection it was just shown.
        out.decision = await reviewCall({ lease_id, decision: 'approve', evidence: 'verified' })
        out.afterDecision = await storage()
      }
    } else {
      // Main decides on the earlier projection, before any boundary observed the break.
      out.approve = await reviewCall({ lease_id, decision: 'approve', evidence: 'verified',
        expected_finalization_version: earlierVersion })
      out.afterApprove = await storage()
      out.rework = await reviewCall({ lease_id, decision: 'rework', evidence: 'redo in a fresh workspace',
        expected_finalization_version: earlierVersion })
      out.afterRework = await storage()
      const inspected = await rpc('inspect')
      out.replacement = inspected.stranded?.rows?.[lease_id]?.replacement_lease_id ?? null
      out.replacementWorkspace = out.replacement ? await rpc('lease_workspace', { lease_id: out.replacement }) : null
    }
    out.lease = lease_id; out.brokenPath = out.broken.path
  }

  if (scenario === 'fork-gate-missing' || scenario === 'fork-gate-present' || scenario === 'fork-gate-mismatch') {
    // Blocker 3: without a live ciel-work-gate answer no Work fork starts and
    // nothing durable changes. The rework is decided first (its dispatch is the
    // host's), then each boundary must start nothing while the gate is missing.
    native.forkGate = scenario !== 'fork-gate-missing'
    if (scenario === 'fork-gate-mismatch') native.forkGateVersion = '2'
    const seeded = await rpc('seed', { title: 'Gate' })
    await start('process')
    out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'add the gate case' })
    await turnComplete(); await turnComplete()
    out.spawns = [...native.spawns]
    out.dispatches = count('rework_dispatch')
    out.intents = count('rework_launch_intent')
    const inspected = await rpc('inspect')
    out.review = inspected.reviews[seeded.lease_id] ?? null
    const replacement = out.review?.replacement_lease_id
    out.replacementState = replacement ? inspected.leases[replacement]?.state ?? null : null
    out.statusText = (await hooks['command:work-mode']($, { args: 'parallel', origin: { kind: 'composer' } })).text
    out.lease = seeded.lease_id
    // What the fork gate reads (G3, backend 0ed166ea): the host view's lease row
    // for the bound replacement, and the model view's row for the same lease.
    const host = await rpc('tool', { tool:'work_parallel_status', args:{ session_id:'claude:S1', _host:{ token:init.token } } })
    const row = replacement ? host.leases?.[replacement] ?? null : null
    out.hostRow = row ? { state: row.state, host_id: row.host_id ?? null, has_key: 'workspace_path' in row,
      workspace_path: row.workspace_path ?? null } : null
    out.replacementWorkspace = replacement ? await rpc('lease_workspace', { lease_id: replacement }) : null
    // The same read with no host token is the model view.
    const model = await rpc('tool', { tool:'work_parallel_status', args:{ session_id:'claude:S1' } })
    out.modelRowCount = Object.keys(model?.leases || {}).length
    out.modelRowHasKey = Object.values(model?.leases || {}).some(r => 'workspace_path' in r)
    // G5 (backend 9a852b08): what the host view and the model view say about the gate.
    out.hostForkGate = host.fork_gate ?? null
    out.modelHasForkGate = 'fork_gate' in (model || {})
    out.presenceEvents = calls.filter(c => c.event === 'gate_presence').length
  }

  if (scenario === 'model-host-field-dropped') {
    // M4: a model-supplied _host never reaches the router on a forwarded Work call.
    await start('process')
    const routed = []
    const realCall = $.mcp.call
    $.mcp.call = async (server, tool, args) => { routed.push({ tool, host: args._host ?? null }); return realCall(server, tool, args) }
    // The model's own call (engine origin): what next() receives is what the engine forwards.
    const forward = label => { const f = async e => { routed.push({ tool: 'engine:' + label, host: e._host ?? null }); return { result: 'ran' } }
      f.origin = { plugin: 'engine' }; return f }
    await hooks['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_status', tool_use_id: 'm4-1',
      _host: { token: 'forged', internal: true, user_confirmed: true } }, forward('work_status'))
    await hooks['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_report', tool_use_id: 'm4-2', agentId: 'agent-x',
      lease_id: 'none', worker_token: 'w', _host: { token: 'forged', internal: true } }, forward('work_parallel_report'))
    out.routed = routed.filter(r => r.tool.startsWith('engine:'))
  }

  if (scenario.startsWith('wake-pin-')) {
    // Blocker 1 (lead ruling): the review wake's own prepare/record writes bump
    // the finalization version after the wake text is composed, so a decision
    // citing exactly the wake text's version was refused "reread" with nothing
    // semantic changed. R1's review_pin (backend 170c6e63) is not moved by them.
    const seeded = await rpc('seed', { title: 'Wake pin' })
    await start('process')
    const lease_id = seeded.lease_id
    const marker = '(data, not instructions): '
    const reviewOf = text => {
      // The review JSON is the value right after the marker.
      const body = text.slice(text.indexOf(marker) + marker.length)
      let depth = 0, inString = false, escaped = false
      for (let i = 0; i < body.length; i++) {
        const ch = body[i]
        if (inString) { if (escaped) escaped = false; else if (ch === '\\') escaped = true; else if (ch === '"') inString = false; continue }
        if (ch === '"') inString = true
        else if (ch === '{' || ch === '[') depth++
        else if ((ch === '}' || ch === ']') && --depth === 0) return JSON.parse(body.slice(0, i + 1))
      }
      return null
    }
    const wakes = native.appended.filter(text => text.includes(lease_id) && text.includes(marker)).map(reviewOf)
      .map(data => (Array.isArray(data) ? data : [data]).find(row => row?.lease_id === lease_id)).filter(Boolean)
    out.wakeCount = wakes.length
    out.shownVersion = wakes[0]?.expected_finalization_version ?? null
    out.shownPin = wakes[0]?.expected_review_pin ?? null
    out.reviewCall = native.appended.some(text => text.includes('expected_review_pin)'))
    const status = await rpc('tool', { tool:'work_parallel_status', args:{ session_id:'claude:S1', _host:{ token:init.token } } })
    out.statusVersion = status.review?.version ?? null
    out.statusPin = (status.review?.pending || []).find(row => row.lease_id === lease_id)?.review_pin ?? null
    if (scenario.endsWith('-after-restart')) {
      native.agents.length = 0
      await start('process')
    }
    const decide = async withPin => reviewCall({ lease_id, decision: 'approve', evidence: 'verified',
      expected_finalization_version: out.shownVersion, ...(withPin ? { expected_review_pin: out.shownPin } : {}) })
    if (scenario === 'wake-pin-stale-pin') {
      // A pin the host never projected for this row is refused, never trusted.
      out.decision = await reviewCall({ lease_id, decision: 'approve', evidence: 'verified',
        expected_finalization_version: out.shownVersion, expected_review_pin: 'rp1:' + '0'.repeat(64) })
    } else if (scenario === 'wake-pin-bad-format') {
      // A malformed pin is dropped by the host, which attaches the projected one.
      out.decision = await reviewCall({ lease_id, decision: 'approve', evidence: 'verified',
        expected_finalization_version: out.shownVersion, expected_review_pin: 'not-a-pin' })
    } else {
      out.decision = await decide(scenario === 'wake-pin-model-pin' || scenario === 'wake-pin-model-pin-after-restart')
    }
    out.sent = native.reviewSent || []
    out.review = (await rpc('inspect')).reviews[lease_id] ?? null
    out.lease = lease_id
  }

  if (scenario === 'failure-log' || scenario === 'failure-log-write-fails') {
    // One done worker's fork is messaged three times; one review's decision is
    // refused twice at one finalization version. Each event is logged once.
    native.failFailureRecord = scenario === 'failure-log-write-fails'
    const seeded = await rpc('seed', { title: 'Logged worker', agent_name: 'logged-worker' })
    const refused = await rpc('seed', { title: 'Logged refusal' })
    await start('process', { busy: true })
    out.decision = JSON.parse((await reviewCall({ lease_id: seeded.lease_id, decision: 'approve', evidence: 'ok' })).result).state
    const workerId = (await rpc('inspect')).leases[seeded.lease_id].host_id
    native.agents.push({ id: workerId, name: 'logged-worker', status: 'killed', type: 'fork', description: 'Work n1' })
    const settled = new Map(native.agents.map(a => [a.id, a.status]))
    out.sends = []
    for (let i = 0; i < 3; i++) {
      for (const a of native.agents) if (settled.has(a.id)) a.status = settled.get(a.id)
      out.sends.push(await revivalProbe(workerId))
    }
    native.failReview = new Set([refused.lease_id])
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    await hooks['turn.start']($, {}, async e => e); await quiet()
    out.refusals = []
    for (const decision of ['approve', 'rework', 'approve']) {
      const r = await reviewCall({ lease_id: refused.lease_id, decision, evidence: 'redo it' })
      out.refusals.push(r?.deny ? String(r.deny).slice(0, 300) : null)
    }
    const stop = await hooks['classic.Stop']($, { session_id: 'S1' }, async () => ({})); await quiet()
    out.stopBlockedOnRefused = typeof stop?.block === 'string' && stop.block.includes(refused.lease_id)
    out.appendedText = native.appended.join('\n')
    out.workerLease = seeded.lease_id; out.refusedLease = refused.lease_id; out.workerId = workerId
  }

  if (scenario === 'deny-once') {
    const seeded = await rpc('seed', { title: 'Denied' })
    await start('process')
    native.denyNext = 1
    out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'retry me' })
    out.afterDeny = await rpc('inspect')
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    out.final = await rpc('inspect')
    out.spawns = [...native.spawns]
    out.denied = count('rework_launch_denied')
    out.replacement = out.final.reviews[seeded.lease_id].replacement_lease_id
  }

  if (scenario === 'rework-retry-warning') {
    const seeded = await rpc('seed', { title: 'Retry warning' })
    await start('process')
    const realSpawn = $.agent.spawn
    $.agent.spawn = async args => { native.spawns.push(args.name); throw new Error('attempt one lost') }
    out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'retry warning' })
    $.agent.spawn = realSpawn
    native.agents.length = 0
    await start('process')
    out.replacement = (await rpc('inspect')).reviews[seeded.lease_id].replacement_lease_id
    out.prompt = native.spawnPrompts.get(native.spawns[1]) || ''
    out.spawns = [...native.spawns]
  }

  if (scenario === 'observed-absent-restart') {
    const seeded = await rpc('seed', { title: 'Observed absent' })
    await start('process')
    // Every bind of this process is lost: the fork is observed, never bound.
    native.failBind = 1000
    out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'retry after lost fork' })
    out.afterObserved = await rpc('inspect')
    // The process exits and its fork is gone from the native list.
    native.failBind = 0
    native.agents.length = 0
    await start('process')
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    out.final = await rpc('inspect')
    out.replacement = out.final.reviews[seeded.lease_id].replacement_lease_id
    out.spawns = [...native.spawns]
    out.absent = count('rework_launch_absent')
  }

  if (scenario === 'manual-late-present') {
    const seeded = await rpc('seed', { title: 'Late present' })
    await start('process')
    const realSpawn = $.agent.spawn
    $.agent.spawn = async args => { native.spawns.push(args.name); throw new Error('spawn response lost') }
    out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'resolve lost spawn' })
    $.agent.spawn = realSpawn
    const stuck = await rpc('inspect')
    out.replacement = stuck.reviews[seeded.lease_id].replacement_lease_id
    out.webBefore = await rpc('web', { op:'launches' })
    const ref = out.webBefore[seeded.lease_id]?.launch_ref
    const body = { lease_id:seeded.lease_id, launch_ref:ref, confirmed:true, expected_finalization_version:stuck.finalization_version }
    out.request = await rpc('web', { op:'resolve', body })
    // A retried click for the same ref is idempotent.
    out.retry = await rpc('web', { op:'resolve', body:{ ...body, expected_finalization_version:out.request.finalization_version } })
    // The late result lands after the browser records its request, before the pump checks.
    native.agents.push(pluginForkRow('agent-late', native.spawns[0], 'running'))
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    out.final = await rpc('inspect')
    out.webAfter = await rpc('web', { op:'launches' })
    out.spawns = [...native.spawns]
    out.present = count('rework_launch_present')
    // The present restore minted a new ref and the fork is bound: the old ref is refused.
    out.stale = await rpc('web', { op:'resolve', body:{ ...body, expected_finalization_version:out.final.finalization_version } })
      .then(value => ({ value }), error => ({ error:error.message }))
  }

  if (scenario === 'manual-present-restart') {
    // A web request is recorded, the fork turns out to be listed, and the CLI
    // restarts before any boundary checked it: session.start alone must resolve it.
    const seeded = await rpc('seed', { title: 'Present at restart' })
    await start('process')
    const realSpawn = $.agent.spawn
    $.agent.spawn = async args => { native.spawns.push(args.name); throw new Error('spawn response lost') }
    out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'present at restart' })
    $.agent.spawn = realSpawn
    const stuck = await rpc('inspect')
    out.replacement = stuck.reviews[seeded.lease_id].replacement_lease_id
    const ref = (await rpc('web', { op:'launches' }))[seeded.lease_id]?.launch_ref
    out.request = await rpc('web', { op:'resolve', body:{ lease_id:seeded.lease_id, launch_ref:ref, confirmed:true,
      expected_finalization_version:stuck.finalization_version } })
    native.agents.push(pluginForkRow('agent-late', native.spawns[0], 'running'))
    const before = calls.length
    // The fork is still listed, so this is the same process: a module reload.
    await start('reload')
    out.final = await rpc('inspect')
    out.spawns = [...native.spawns]
    out.present = count('rework_launch_present')
    out.sessionStartEvents = calls.slice(before).map(c => c.event || c.tool)
  }

  if (scenario === 'intent-listed-restart') {
    // spawn threw, but the native fork did start. After a restart the exact
    // name, read host-side, binds it; no second fork and no second dispatch.
    const seeded = await rpc('seed', { title: 'Listed after throw' })
    await start('process')
    const realSpawn = $.agent.spawn
    $.agent.spawn = async args => {
      native.spawns.push(args.name)
      native.agents.push(pluginForkRow('agent-started', args.name, 'running', String(args.description || '')))
      throw new Error('spawn response lost')
    }
    // The first process exits before any later boundary observes the fork.
    native.failBind = 1000
    out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'listed late' })
    $.agent.spawn = realSpawn
    native.failBind = 0
    out.afterCrash = await rpc('inspect')
    out.replacement = out.afterCrash.reviews[seeded.lease_id].replacement_lease_id
    // What a model-originated status call (no host token) sees for that lease.
    out.modelStatus = await rpc('tool', { tool:'work_parallel_status', args:{ session_id:'claude:S1', _host:{} } })
      .then(value => ({ value }), error => ({ error:error.message }))
    // The started fork is still listed: a module reload of the same process.
    await start('reload')
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    out.final = await rpc('inspect')
    out.spawns = [...native.spawns]
    out.dispatches = count('rework_dispatch')
  }

  if (scenario === 'manual-pane-absent') {
    const seeded = await rpc('seed', { title: 'Pane resolve' })
    await start('process')
    const realSpawn = $.agent.spawn
    $.agent.spawn = async args => { native.spawns.push(args.name); throw new Error('spawn response lost') }
    out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'resolve through pane' })
    $.agent.spawn = realSpawn
    out.replacement = (await rpc('inspect')).reviews[seeded.lease_id].replacement_lease_id
    // The next boundary finds the same-process launch unlisted: stuck, not resolved.
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    out.stuck = await rpc('inspect')
    // A declined confirmation records nothing.
    native.answers.push('Keep waiting')
    await hooks['ui.message']($, { data:{ action:'resolve-launch' } }, async e => e)
    await quiet()
    out.requestsAfterDecline = count('rework_launch_manual_request')
    native.answers.push('Request resolution')
    out.beforeRequest = await rpc('web', { op:'launches' })
    await hooks['ui.message']($, { data:{ action:'resolve-launch' } }, async e => e)
    await quiet()
    out.final = await rpc('inspect')
    out.spawns = [...native.spawns]
    out.manual = count('rework_launch_manual_resolve')
    out.requests = count('rework_launch_manual_request')
    // Re-dispatched after the request: the first attempt's ref is now stale.
    const oldRef = out.beforeRequest[seeded.lease_id]?.launch_ref
    out.stale = await rpc('web', { op:'resolve', body:{ lease_id:seeded.lease_id, launch_ref:oldRef, confirmed:true,
      expected_finalization_version:out.final.finalization_version } }).then(value => ({ value }), error => ({ error:error.message }))
  }

  if (scenario.startsWith('fork-attribution-')) {
    const variant = scenario.slice('fork-attribution-'.length)
    await start('process')
    if (variant === 'batch') {
      for (let i = 0; i < 65; i++)
        native.agents.push({ id:'unattributed-' + i, name:'work-rework-unknown-' + i + '-a1', status:'running' })
    } else if (variant === 'shape') {
      native.agents.push(
        { id:'shape-0', name:'work-rework-hyphenated-lease-id-a12', status:'running' },
        { id:'shape-1', name:'work-rework-side-owned-looking-a1', status:'running', type:'ciel-work:worker' },
        { id:'shape-2', name:'work-rework-no-attempt', status:'running' },
        { id:'shape-3', name:'work-rework-x-a0', status:'running' },
        { id:'shape-4', name:'work-rework-x-a1000', status:'running' },
        { id:'shape-5', name:'work-rework-x-a1-junk', status:'running' },
        { id:'shape-side', name:'side-intake-control', status:'running', type:'ciel-work:side-intake' })
    } else {
      const names = variant === 'reply-shapes'
        ? ['work-rework-dup-a1', 'work-rework-dup-a1', 'work-rework-other-a1']
        : ['work-rework-unknown-0-a1']
      for (let i = 0; i < names.length; i++)
        native.agents.push({ id:'unattributed-' + i, name:names[i], status:'running' })
      if (variant === 'reply-shapes') {
        native.attributionReplies.push({ forks:[
          { name:names[1], status:'stale', reason:'earlier_attempt' },
          { name:'work-rework-wrong-a1', status:'stale', reason:'earlier_attempt' }] })
      }
    }
    await turnComplete()
    if (variant === 'batch') await turnComplete()
    out.attributions = native.attributions.map(row => ({ ...row }))
    out.backendAttributions = (await rpc('bridge_calls')).filter(row => row.tool === 'work_parallel_event'
      && row.args?.event === 'rework_fork_attribution').map(row => ({ names:row.args.names, internal:row.args._host?.internal === true }))
    out.stopped = native.toolCalls.filter(row => row.tool === 'TaskStop').map(row => row.task_id)
    out.notice = await hooks['session.receive']($, { origin:{ kind:'task-notification' },
      text:'<task-notification><task-id>unattributed-0</task-id></task-notification>' }, async e => e)
  }

  if (scenario === 'revival-nested-own-send') {
    // Exact X3 shape: an active running Work lease is recovered through the
    // mod's own $.session.send, whose SendMessage dispatch reaches this module's
    // session.send hook. A foreign plugin's otherwise identical send is fenced.
    const seeded = await rpc('seed_running')
    native.nestedOwnSend = true
    await start('process')
    native.nestedOwnSend = false
    const own = native.nestedOwnSendResults?.[0] ?? null
    const foreign = await revivalProbe(seeded.agent_id, { origin: 'other-plugin' })
    out.lease = seeded.lease_id
    out.workerId = seeded.agent_id
    out.own = { delivered: own?.isDelivered === true,
      reason: own?.reason ? String(own.reason).slice(0, 300) : null }
    out.foreign = foreign
    out.recoverySends = native.sends.filter(s => String(s.text || '').startsWith('Work recovery:')).length
  }

  if (scenario === 'revival-terminal') {
    // A worker whose approved lease is done, plus a side fork and an unrelated
    // teammate. The module is reloaded, so no in-memory map remembers the worker.
    const seeded = await rpc('seed', { title: 'Terminal worker', agent_name: 'terminal-worker-name' })
    await start('process')
    out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'approve', evidence: 'verified' })
    out.leaseState = (await rpc('inspect')).leases[seeded.lease_id]
    const workerId = out.leaseState.host_id
    // An engine-origin fork (the model's Agent call): no spawnedBy.
    native.agents.push({ id: workerId, name: 'terminal-worker-name', status: 'killed', type: 'fork', description: 'Work n1' },
      { id: 'side-fork-1', status: 'completed', type: 'ciel-work:side-intake', description: 'Side SSE request' },
      { id: 'teammate-1', name: 'helper@team', status: 'idle', type: 'teammate' })
    await start('reload')
    const settled = new Map(native.agents.map(a => [a.id, a.status]))
    const probe = async (to, options) => {
      for (const a of native.agents) if (settled.has(a.id)) a.status = settled.get(a.id)
      return revivalProbe(to, options)
    }
    // A teammate whose name has the shape of a Work dispatch name.
    native.agents.push({ id: 'teammate-2', name: 'work-backend-fix1', status: 'idle', type: 'teammate' })
    settled.set('teammate-2', 'idle')
    out.revival = {
      doneById: await probe(workerId),
      doneByName: await probe('terminal-worker-name'),
      sideById: await probe('side-fork-1'),
      otherPluginToDone: await probe(workerId, { origin: 'other-plugin' }),
      teammate: await probe('helper@team'),
      teammateWorkShapedName: await probe('work-backend-fix1'),
      unlistedPeer: await probe('peer-session-xyz'),
    }
    out.workerId = workerId
  }

  if (scenario === 'stop-gate-refused') {
    // Two results await review. Every decision for the first is refused by the
    // router (an injected refusal); the second is a normal decidable row.
    const refused = await rpc('seed', { title: 'Refused review' })
    const normal = await rpc('seed', { title: 'Normal review' })
    native.failReview = new Set([refused.lease_id])
    await start('process', { busy: true })
    const stop = async () => {
      const r = await hooks['classic.Stop']($, { session_id: 'S1' }, async () => ({}))
      await quiet()
      const block = typeof r?.block === 'string' ? r.block : ''
      // The finalization version each gated row carried (the gate's review data).
      let refusedVersion = null
      try {
        const data = JSON.parse(block.slice(block.indexOf('Review data: ') + 'Review data: '.length))
        refusedVersion = data.find(row => row.lease_id === refused.lease_id)?.expected_finalization_version ?? null
      } catch { /* No review data in this block. */ }
      return { blocked: !!block, refusedShown: block.includes(refused.lease_id), normalShown: block.includes(normal.lease_id),
        refusedVersion }
    }
    const decide = async (lease_id, decision) => {
      const r = await reviewCall({ lease_id, decision, evidence: decision === 'rework' ? 'redo it' : 'verified' })
      return r?.deny ? 'refused' : JSON.parse(r.result).state
    }
    const turn = async label => {
      const rows = []
      for (let i = 0; i < 5; i++) {
        const shown = await stop()
        rows.push(shown)
        if (!shown.blocked) break
        if (shown.refusedShown) rows[rows.length - 1].decision = await decide(refused.lease_id, i % 2 ? 'rework' : 'approve')
        else if (shown.normalShown && label === 'turn 1') rows[rows.length - 1].decision = await decide(normal.lease_id, 'approve')
      }
      ;(out.turns ||= []).push({ label, rows, blocks: rows.filter(r => r.blocked).length,
        notices: native.appended.length })
    }
    await turn('turn 1')
    // A new main turn gates the refused row again, once.
    await hooks['turn.start']($, {}, async e => e); await quiet()
    await turn('turn 2')
    out.leases = { refused: refused.lease_id, normal: normal.lease_id }
    out.reviewRefusals = native.reviewRefusals
  }

  if (scenario === 'revival-durable-only' || scenario === 'revival-durable-only-lookup-failed') {
    // A done worker whose fork carries no reserved name and no ciel-work mark,
    // after nine later leases pushed it out of the status read's ended window
    // and a reload cleared every module map: only the durable store knows it.
    const old = await rpc('seed', { title: 'Durable only', agent_name: 'legacy-helper' })
    await start('process')
    out.oldDecision = JSON.parse((await reviewCall({ lease_id: old.lease_id, decision: 'approve', evidence: 'ok' })).result).state
    await rpc('policy', { changes: { launches_per_minute: 60 } })
    for (let i = 0; i < 9; i++) {
      const later = await rpc('seed', { title: 'Later ' + i })
      await reviewCall({ lease_id: later.lease_id, decision: 'approve', evidence: 'later ' + i })
    }
    const inspected = await rpc('inspect')
    const workerId = inspected.leases[old.lease_id].host_id
    out.oldLease = inspected.leases[old.lease_id]
    const status = await rpc('tool', { tool:'work_parallel_status', args:{ session_id:'claude:S1', _host:{ token:init.token } } })
    out.statusMentionsOld = JSON.stringify(status).includes(workerId) || JSON.stringify(status).includes('legacy-helper')
    native.agents.push({ id: workerId, name: 'legacy-helper', status: 'killed', type: 'fork', description: 'Work n1' })
    await start('reload')
    if (scenario.endsWith('-lookup-failed')) native.failIdentity = true
    const settled = new Map(native.agents.map(a => [a.id, a.status]))
    const probe = async (to, options) => {
      for (const a of native.agents) if (settled.has(a.id)) a.status = settled.get(a.id)
      return revivalProbe(to, options)
    }
    out.revival = { byId: await probe(workerId), byName: await probe('legacy-helper'),
      otherPlugin: await probe(workerId, { origin: 'other-plugin' }), unlistedPeer: await probe('peer-session-xyz') }
    out.identityCalls = native.identityCalls
    out.workerId = workerId
  }

  if (scenario === 'revival-read-failure') {
    // The router is down. Only what this process itself knows may refuse a send.
    await start('process')
    native.agents.push(
      // Engine-origin (no spawnedBy) with a reserved name, and a ciel-work spawn.
      { id: 'work-fork-1', name: 'work-mh2k3x9a-4f8gq1', status: 'killed', type: 'fork', description: 'Work n1' },
      pluginForkRow('cw-fork-1', 'helper-fork', 'completed', 'Work n2'),
      { id: 'side-fork-2', status: 'completed', type: 'ciel-work:side-intake' },
      { id: 'teammate-3', name: 'work-backend-fix1', status: 'idle', type: 'teammate' })
    const settled = new Map(native.agents.map(a => [a.id, a.status]))
    const probe = async (to, options) => {
      for (const a of native.agents) if (settled.has(a.id)) a.status = settled.get(a.id)
      return revivalProbe(to, options)
    }
    native.failStatus = true
    out.revival = {
      unlistedPeer: await probe('peer-session-xyz'),
      unlistedPeerRef: await probe('worker [3fa9c1]'),
      teammateWorkShapedName: await probe('work-backend-fix1'),
      reservedNameForkById: await probe('work-fork-1'),
      reservedNameForkByName: await probe('work-mh2k3x9a-4f8gq1'),
      cielWorkSpawnedFork: await probe('cw-fork-1'),
      sideFork: await probe('side-fork-2'),
      unlistedReservedName: await probe('work-rework-L-earlier-a1'),
      otherPluginToReservedFork: await probe('work-fork-1', { origin: 'other-plugin' }),
    }
    // An earlier process's fork is unlisted here; its only way back is a deferred resume spawn.
    out.deferredWithoutAuthorization = await deferredResume('work-mh2k3x9a-old', 'old-process-fork')
    native.failStatus = false
    out.failures = (await hooks['command:work-mode']($, { args: 'parallel', origin: { kind: 'composer' } })).text
      .split('\n').filter(line => line.includes('revival-refused') || line.includes('agent-spawn')).map(line => line.slice(0, 300))
  }

  if (scenario === 'revival-resume-auth' || scenario === 'revival-resume-auth-unlisted') {
    // An active lease on the resume path. Listed: the engine revives it in place
    // and raises no agent.spawn. Unlisted (an earlier process's fork): the resume
    // comes later as a deferred agent.spawn.
    const seeded = await rpc('seed_running')
    await start('process')
    const listed = scenario === 'revival-resume-auth'
    if (listed) native.agents.push({ id: seeded.agent_id, name: seeded.agent_id, status: 'idle', type: 'fork' })
    out.send = await revivalProbe(seeded.agent_id)
    if (listed) {
      // No deferred spawn follows an in-place revival; a later one must not ride on it.
      out.laterSpawn = await deferredResume(seeded.agent_id, seeded.agent_id)
    } else {
      out.deferredSpawn = await deferredResume(seeded.agent_id, seeded.agent_id)
      out.secondSend = await revivalProbe(seeded.agent_id)
      const realNow = Date.now
      Date.now = () => realNow() + 10 * 60 * 1000
      try { out.expiredSpawn = await deferredResume(seeded.agent_id, seeded.agent_id) } finally { Date.now = realNow }
    }
    out.lease = seeded.lease_id
  }

  if (scenario === 'owner-process-paths') {
    // R2 B2 paths 2/4: one running worker per owner class, each unlisted here.
    // Only own and gone are adopted and recovered; other_live and unknown are shown.
    const own = await rpc('seed_running')
    const other = await rpc('seed_running_owned', { kind: 'other_live' })
    const unknown = await rpc('seed_running_owned', { kind: 'unknown' })
    const gone = await rpc('seed_running_owned', { kind: 'gone' })
    out.seeded = { own: own.lease_id, other_live: other.lease_id, unknown: unknown.lease_id, gone: gone.lease_id }
    const agentOf = { own: own.agent_id, other_live: other.agent_id, unknown: unknown.agent_id, gone: gone.agent_id }
    await start('process')
    out.attach = (native.nonceCalls || []).filter(row => row.event === 'host_attach')
    // The host view as this process reads it (its token and its own nonce).
    const status = await rpc('tool', { tool: 'work_parallel_status',
      args: { session_id: 'claude:S1', _host: { token: init.token, launch_nonce: await $.env.get('CIEL_WORK_HOST_PROCESS_NONCE') } } })
    out.ownerProcess = Object.fromEntries(Object.entries(out.seeded).map(([kind, id]) => [kind, status.leases[id]?.owner_process ?? null]))
    // This process's own attachment and the other_live stand-in are both live.
    out.attachments = status.session_attachments
    out.recoverySends = Object.fromEntries(Object.entries(agentOf).map(([kind, id]) =>
      [kind, native.sends.filter(s => s.to?.agentId === id).length]))
    const reconcileBefore = (native.nonceCalls || []).length
    await turnComplete()
    const reconciled = (native.nonceCalls || []).slice(reconcileBefore).filter(row => row.event === 'reconcile')
    const kindOf = Object.fromEntries(Object.entries(out.seeded).map(([kind, id]) => [id, kind]))
    out.turnReconciled = [...new Set(reconciled.map(row => kindOf[row.lease_id] || 'other'))].sort()
    out.statusText = (await hooks['command:work-mode']($, { args: 'parallel', origin: { kind: 'composer' } })).text
      .split('\n').filter(line => line.startsWith('HOSTS:') || / · owner (other_live|unknown|gone)/.test(line))
    // Every host call kind the backend reads the nonce on, and whether it carried it.
    const nonced = event => (native.nonceCalls || []).filter(row => (row.event ?? row.tool) === event)
    out.nonceBy = Object.fromEntries(['work_parallel_status', 'host_attach', 'native_observation', 'reserve_next', 'reserve']
      .map(event => [event, { calls: nonced(event).length, withNonce: nonced(event).filter(row => row.nonce).length }]))
    out.nonceValues = [...new Set((native.nonceCalls || []).filter(row => row.nonce).map(row => row.nonce))].length
  }

  if (scenario === 'launch-owner-pane-unknown' || scenario === 'launch-owner-pane-other-live') {
    // R2 B2 pane gap: a later process's automatic absence is refused for an
    // attempt owned by an unknown or another live host. Unknown must offer the
    // pane's "Resolve stuck launch…" control (the router allows a person's
    // manual resolution); other_live only shows the waiting text.
    const kind = scenario.endsWith('unknown') ? 'unknown' : 'other_live'
    const seeded = await rpc('seed', { title: 'Owner pane' })
    await start('process')
    const realSpawn = $.agent.spawn
    $.agent.spawn = async args => { native.spawns.push(args.name); throw new Error('spawn response lost') }
    await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'owner pane' })
    $.agent.spawn = realSpawn
    await rpc('set_intent_owner', { lease_id: seeded.lease_id, kind })
    await start('process')
    await turnComplete()
    out.absent = (native.nonceCalls || []).filter(row => row.event === 'rework_launch_absent').length
    out.statusLines = (await hooks['command:work-mode']($, { args: 'parallel', origin: { kind: 'composer' } })).text
      .split('\n').filter(line => /Rework launch (stuck|waiting)/.test(line))
    // Draw the pane exactly as the terminal does and look for the control.
    await hooks['command:work-mode']($, { args: 'view', origin: { kind: 'composer' } })
    $.ui.resolve = () => ({ Box: p => p, Text: p => p, Button: p => p, Client: p => p })
    const flat = []
    const walk = node => { if (!node || typeof node !== 'object') return; flat.push(node); for (const child of [].concat(node.children || [])) walk(child) }
    walk(await hooks['render:work-details']($, { component: 'Pane', requestId: 'work-details', surface: 'terminal',
      props: { bodyColumns: 120, scroll: { bodyRows: 30 } }, viewport: { rows: 32 } }, async e => e))
    out.paneLabels = flat.filter(node => typeof node.label === 'string').map(node => node.label)
    out.control = flat.some(node => node.key === 'obs-resolve-launch' && node.label === 'Resolve stuck launch…')
    // The person presses it: only their confirmed request is sent by the press.
    const before = calls.length
    native.answers.push('Request resolution')
    await hooks['ui.message']($, { data: { action: 'resolve-launch' } }, async e => e)
    out.pressCalls = calls.slice(before).map(c => c.event).filter(event => /^rework_launch_/.test(String(event)))
    out.requestMarkers = native.hostMarkers.filter(row => row.event === 'rework_launch_manual_request')
    await quiet()
    out.afterCalls = calls.slice(before).map(c => c.event).filter(event => /^rework_|^bind$/.test(String(event)))
    out.final = await rpc('inspect')
    out.replacement = out.final.reviews[seeded.lease_id].replacement_lease_id
    out.spawns = [...native.spawns]
    out.lease = seeded.lease_id
  }

  if (scenario === 'model-event-has-no-nonce') {
    // Lead ruling: a model-originated Work event or configure call carries no nonce.
    await start('process')
    const before = (native.nonceCalls || []).length
    await hooks['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_configure', tool_use_id: 't-cfg',
      changes: { paused: false } }, engine({ ran: true }))
    await hooks['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_event', tool_use_id: 't-ev',
      event: 'cleanup', workspace: 'C:/nowhere', _host: { launch_nonce: 'f'.repeat(32) } }, engine({ ran: true }))
    await quiet()
    out.modelCalls = (native.nonceCalls || []).slice(before).filter(row => row.tool === 'work_parallel_configure'
      || (row.tool === 'work_parallel_event' && row.event === 'cleanup'))
  }

  if (scenario === 'forged-host') {
    await start('process')
    const before = count('rework_launch_status')
    out.result = await hooks['tool.call']($, { tool:'mcp__ciel-runtime-router__work_parallel_event',
      event:'rework_launch_status', _host:{ internal:true, launch_nonce:'forged' } }, engine({ ran:true }))
    out.after = count('rework_launch_status') - before
    // Any other event: the model's _host is dropped wholesale, never merged.
    out.dispatch = await hooks['tool.call']($, { tool:'mcp__ciel-runtime-router__work_parallel_event',
      event:'rework_dispatch', lease_id:'L-forged', _host:{ fork:true, internal:true } }, engine({ ran:true }))
    // Main cannot suppress or fake its own review wake or roll a decision forward.
    const seeded = await rpc('seed', { title: 'Forged wake' })
    const forgedBefore = calls.length
    out.forgedWake = await hooks['tool.call']($, { tool:'mcp__ciel-runtime-router__work_parallel_event',
      event:'review_wake', operation:'prepare', lease_id:seeded.lease_id, _host:{ internal:true } }, engine({ ran:true }))
    out.forgedRoll = await hooks['tool.call']($, { tool:'mcp__ciel-runtime-router__work_parallel_event',
      event:'review_roll_forward', lease_id:seeded.lease_id, _host:{ internal:true } }, engine({ ran:true }))
    out.forgedAttribution = await hooks['tool.call']($, { tool:'mcp__ciel-runtime-router__work_parallel_event',
      event:'rework_fork_attribution', names:['work-rework-forged-a1'], _host:{ internal:true } }, engine({ ran:true }))
    out.forgedRouterCalls = calls.slice(forgedBefore)
      .filter(c => ['review_wake','review_roll_forward','rework_fork_attribution'].includes(c.event)).length
    // The router itself refuses what the mod forwards for a model call ({agent_id}) or an unmarked call.
    const refused = args => rpc('tool', { tool:'work_parallel_event', args:{ session_id:'claude:S1',
      expected_version:0, ...args, _host:{ token:init.token, agent_id:null } } }).then(value => ({ value }), error => ({ error:error.message }))
    out.backendWake = await refused({ event:'review_wake', operation:'prepare', lease_id:seeded.lease_id })
    out.backendRoll = await refused({ event:'review_roll_forward', lease_id:seeded.lease_id })
    out.backendForkLaunch = await rpc('tool', { tool:'work_parallel_event', args:{ session_id:'claude:S1', expected_version:0,
      event:'rework_launch_status', _host:{ token:init.token, fork:true } } }).then(value => ({ value }), error => ({ error:error.message }))
  }

  // Wait-deadlock audit: main idle, no fork running, and the only change is one
  // an operator surface makes (web/REST/menu/ctl share these effects) or the
  // router connecting late. Time then passes: every armed $.clock timer fires
  // for 8 rounds. No main turn, Stop, report or prompt happens after the change.
  if (scenario.startsWith('idle-')) {
    // '-late': the change happens only after 3 rounds of idle ticks, so the
    // first tick (whose host signature is new) cannot be what releases it.
    const named = scenario.slice('idle-'.length)
    const late = named.endsWith('-late') && named !== 'owner-offline-late'
    const variant = late ? named.slice(0, -'-late'.length) : named
    native.captureTimers = true
    const seeded = await rpc('seed', { title: 'Idle ' + variant })
    out.lease = seeded.lease_id
    const snap = async () => {
      const inspected = await rpc('inspect')
      const row = inspected.reviews[seeded.lease_id]
      return { state: row?.state ?? null, blocked: row?.blocked?.code ?? null, wake: row?.wake?.state ?? null,
        replacement: row?.replacement_lease_id ?? null, spawns: native.spawns.length,
        // Review and recovery delivery. The gate round's own dispatch notice is
        // counted apart: it is a separate, once-per-diagnosis message.
        prompts: native.prompts.filter(text => !isDispatchNotice(text)).length,
        appended: native.appended.filter(text => !isDispatchNotice(text)).length,
        dispatchPrompts: native.prompts.filter(isDispatchNotice).length,
        promptKinds: native.prompts.map(text => String(text).slice(0, 160)),
        dispatchAppended: native.appended.filter(isDispatchNotice).length, prepares: count('review_wake', 'prepare'), rollForwards: count('review_roll_forward'),
        recoverySends: native.sends.filter(s => String(s.text || '').startsWith('Work recovery:')).length,
        exitSends: native.sends.filter(s => String(s.text || '').startsWith('Your native turn ended')).length,
        salvages: count('host_salvage') }
    }
    // Every file the backend owns, before and after time passes (no-write proof).
    const writes = (before, after) => Object.keys({ ...before, ...after }).filter(key => before[key] !== after[key]).sort()
    if (variant === 'owner-offline-late') {
      // As owner-offline, but the router becomes bound only after 3 rounds of
      // idle ticks: the host projection is unchanged by then.
      out.unbound = await rpc('owner', { bound: false })
      await start('process')
      out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'owner offline' })
      await turnComplete()
      out.early = await passTime(3)
      out.before = await snap()
      out.change = await rpc('owner', { bound: true })
    } else if (variant === 'owner-offline' || variant === 'nowrite-offline') {
      // The OO case with main idle: the rework waits owner_offline; the router
      // becomes bound later with no main turn, Stop, report or prompt.
      out.unbound = await rpc('owner', { bound: false })
      // No-write: backend time stops, so only the mod's own calls can write.
      if (variant === 'nowrite-offline') out.clock = await rpc('freeze_clock')
      await start('process')
      out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'owner offline' })
      await turnComplete()
      out.before = await snap()
      if (variant === 'owner-offline') out.change = await rpc('owner', { bound: true })
      else { out.change = { none: true }; out.filesBefore = await rpc('fingerprint') }
    } else if (variant === 'nowrite-paused') {
      await rpc('pause', { paused: true })
      out.clock = await rpc('freeze_clock')
      await start('process')
      out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'paused' })
      await turnComplete()
      out.before = await snap()
      out.change = { none: true }
      out.filesBefore = await rpc('fingerprint')
    } else if (variant === 'wake-failed' || variant === 'wake-pending' || variant === 'wake-once') {
      // Main idle from the start. failed: the engine refuses the review append once;
      // pending: the wake prepare is lost once; once: delivered normally.
      if (variant === 'wake-failed') native.denyAppend = 1
      if (variant === 'wake-pending') native.failWakePrepare = 1
      await start('process')
      out.before = await snap()
      out.change = { none: true }
    } else if (variant === 'retry-once') {
      // session.start reads no board (failBoard) but its host phase works; the
      // running worker is unlisted, so session.start sends one recovery.
      const running = await rpc('seed_running')
      out.lease = running.lease_id
      native.failBoard = 1
      await start('process')
      out.before = await snap()
      out.change = { none: true }
    } else if (variant === 'policy-resume') {
      await rpc('pause', { paused: true })
      await start('process')
      out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'needs a slot' })
      await turnComplete()
      if (late) out.early = await passTime(3)
      out.before = await snap()
      out.change = await rpc('web', { op: 'configure', body: { changes: { paused: false } } }).then(v => ({ paused: v?.policy?.paused ?? null }))
    } else if (variant === 'web-rework') {
      await start('process')
      // Main was woken and ended its turn without deciding; the person decides on the web.
      await turnComplete()
      if (late) out.early = await passTime(3)
      out.before = await snap()
      out.change = await rpc('web', { op: 'review', body: { lease_id: seeded.lease_id, decision: 'rework', evidence: 'redo it (web)' } })
        .then(v => ({ state: v.state }), error => ({ error: error.message }))
    } else if (variant === 'web-launch-resolve') {
      // This process's own spawn response is lost: a stuck same-process launch.
      // Main ends its turn; the person resolves it on the web while main is idle.
      await start('process')
      const realSpawn = $.agent.spawn
      $.agent.spawn = async args => { native.spawns.push(args.name); throw new Error('spawn response lost') }
      out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'web resolve' })
      $.agent.spawn = realSpawn
      await turnComplete()
      const stuck = await rpc('inspect')
      out.launchBefore = stuck.reviews[seeded.lease_id].launch
      if (late) out.early = await passTime(3)
      out.before = await snap()
      // The person's view right before the click (the version the web shows now).
      const shownVersion = (await rpc('inspect')).finalization_version
      const ref = (await rpc('web', { op:'launches' }))[seeded.lease_id]?.launch_ref
      out.change = await rpc('web', { op:'resolve', body:{ lease_id:seeded.lease_id, launch_ref:ref, confirmed:true,
        expected_finalization_version:shownVersion } }).then(v => ({ state: v.state }), error => ({ error: error.message }))
    } else if (variant === 'wake-failed-always') {
      // The engine refuses every review append: the bound of the failed-wake retry.
      native.denyAppend = 1000
      await start('process')
      out.before = await snap()
      out.change = { none: true }
    } else if (variant === 'wake-submit-fails-twice' || variant === 'wake-failed-twice') {
      // Main idle from the start. submit: the wake prompt's $.prompt.submit throws
      // twice, then would succeed. failed: the engine refuses the review append
      // twice (recorded wake failed), then would accept it.
      if (variant === 'wake-submit-fails-twice') native.failSubmit = 2
      else native.denyAppend = 2
      await start('process')
      out.before = await snap()
      out.change = { none: true }
    } else if (variant === 'launch-rate-review') {
      // One launch per minute; the seed's own reservation used it. A rework
      // decided now waits for launch_rate, then 61 s pass with nothing written.
      out.policy = await rpc('policy', { changes: { launches_per_minute: 1 } })
      await start('process')
      out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'launch rate' })
      await turnComplete()
      out.before = await snap()
      out.change = await rpc('advance_clock', { seconds: 61 })
    } else if (variant === 'launch-rate-leaf' || variant === 'capacity-leaf') {
      // A plain runnable leaf (not a review) that auto-fill cannot dispatch yet.
      // launch-rate: the seed's reservation used the one launch per minute; then
      // 61 s pass with nothing written. capacity: a running worker holds the only
      // P1 slot; then the operator raises the P1 limit on the web.
      if (variant === 'launch-rate-leaf') out.policy = await rpc('policy', { changes: { launches_per_minute: 1 } })
      else { await rpc('seed_running'); out.policy = await rpc('limits', { p1: 1 }) }
      out.leaf = await rpc('seed_leaf', { title: 'Idle leaf' })
      await start('process')
      await turnComplete()
      const leafLease = async () => Object.values((await rpc('inspect')).leases).filter(l => l.item_id === out.leaf.item_id).length
      out.leafLeasesBefore = await leafLease()
      out.fillsBefore = count('reserve_next')
      if (late) out.early = await passTime(3)
      out.before = await snap()
      out.change = variant === 'launch-rate-leaf' ? await rpc('advance_clock', { seconds: 61 })
        : await rpc('web', { op: 'configure', body: { changes: { priority_limits: { P1: 2 } } } }).then(v => ({ P1: v?.policy?.priority_limits?.P1 ?? null }))
      out.leafLease = leafLease
    } else if (variant === 'other-live-exits') {
      // The launch attempt belongs to another live Work host; this process waits.
      // That host's launcher then exits while main here is idle.
      await start('process')
      const realSpawn = $.agent.spawn
      $.agent.spawn = async args => { native.spawns.push(args.name); throw new Error('spawn response lost') }
      out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'other host' })
      $.agent.spawn = realSpawn
      await rpc('set_intent_owner', { lease_id: seeded.lease_id, kind: 'other_live' })
      // A later process of this conversation (its own nonce) sees the attempt as another host's.
      await start('process')
      await turnComplete()
      out.launchBefore = (await rpc('inspect')).reviews[seeded.lease_id].launch
      if (late) out.early = await passTime(3)
      out.before = await snap()
      out.waiting = (out.failures = undefined, (await hooks['command:work-mode']($, { args: 'parallel', origin: { kind: 'composer' } })).text
        .split('\n').filter(line => line.includes('Another live Work host owns this launch attempt')).length)
      out.change = await rpc('end_owner_children')
    } else if (variant === 'start-unconnected-running-window' || variant === 'start-unconnected-board-only') {
      // X2 (ctl's A2 probe shape, review/a2-row12-probe-harness.diff): the router
      // connects only after every retryStart timer of the original 6-step window
      // has fired (7 rounds). board-only: the board read recovers but the host
      // phase keeps failing for those rounds (host status refused), then recovers.
      const running = await rpc('seed_running')
      out.lease = running.lease_id
      if (variant === 'start-unconnected-board-only') native.failStatus = true
      else native.mcpDown = true
      await start('process')
      out.early = await passTime(7)
      out.armedAfterWindow = timers.filter(t => !t.fired && !t.cancelled).map(t => t.by + ':' + t.delay)
      out.before = await snap()
      native.mcpDown = false
      native.failStatus = false
      out.change = { connected: true }
    } else if (['start-recovery-then-exit-throws','start-recovery-exit-control',
        'start-recovery-exit-undelivered-then-throws','start-recovery-exit-undelivered-control',
        'start-recovery-exit-send-throws-then-throws','start-recovery-exit-send-throws-control'].includes(variant)) {
      // X2 criterion 6 exact lead/ctl shape. L1 is unlisted (recovery). L2 and
      // L3 are terminal. In the fault case, L2's delivered exit-contract send
      // is followed by a one-shot sessionId throw when L3 starts. The retry
      // must skip both L1 and L2. Control has the same leases without the throw.
      const l1 = await rpc('seed_running', { agent_id: 'check6-l1' })
      const l2 = await rpc('seed_running', { agent_id: 'check6-l2' })
      const l3 = await rpc('seed_running', { agent_id: 'check6-l3' })
      out.leases = { l1: l1.lease_id, l2: l2.lease_id, l3: l3.lease_id }
      native.agents.push({ id:l2.agent_id, name:l2.agent_id, status:'killed', type:'fork' },
        { id:l3.agent_id, name:l3.agent_id, status:'killed', type:'fork' })
      if (variant.includes('undelivered')) native.exitSendFault = 'undelivered'
      if (variant.includes('send-throws')) native.exitSendFault = 'throw'
      if (variant === 'start-recovery-then-exit-throws' || variant.includes('then-throws')) native.throwSessionIdAfterExitDelivered = 1
      await start('process')
      out.before = await snap()
      out.change = { none: true }
    } else if (variant === 'start-recovery-then-pump-throws') {
      // X2 criterion 6 (lead, 23:4xZ): session.start's host phase sends recovery
      // for an unlisted running lease, then throws later (its pump's status read
      // is refused once). The capped retry runs the host phase again.
      const running = await rpc('seed_running')
      out.lease = running.lease_id
      native.failStatusAfterRecovery = 1
      await start('process')
      out.before = await snap()
      out.change = { none: true }
    } else if (variant === 'start-unconnected-running') {
      // A running worker whose fork did not survive the process restart, and a
      // router that connects only after session.start: nobody else recovers it.
      const running = await rpc('seed_running')
      out.lease = running.lease_id
      native.mcpDown = true
      await start('process')
      out.before = await snap()
      native.mcpDown = false
      out.change = { connected: true }
    } else if (variant === 'start-unconnected') {
      native.mcpDown = true
      await start('process')
      out.before = await snap()
      // The engine finishes connecting the router; nothing else happens.
      native.mcpDown = false
      out.change = { connected: true }
    } else throw new Error('unknown idle variant ' + variant)
    const callsBefore = calls.length
    // The release: the first new roll-forward, dispatch, spawn, recovery or wake append.
    const progress = () => count('review_roll_forward') + count('rework_dispatch') + native.spawns.length
      + native.appended.length + native.prompts.length
      + native.sends.filter(s => String(s.text || '').startsWith('Work recovery:')).length
    passTime.releasedBy = undefined
    out.fired = await passTime(8, progress)
    out.releasedBy = passTime.releasedBy ?? null
    out.after = await snap()
    if (out.leafLease) { out.leafLeasesAfter = await out.leafLease(); delete out.leafLease; out.fillsAfter = count('reserve_next') }
    if (native.submitFailures) out.submitFailures = native.submitFailures.length
    if (native.failStatusAfterRecovery !== undefined) out.throwsLeft = native.failStatusAfterRecovery
    if (native.throwSessionIdAfterExitDelivered !== undefined) out.exitThrowsLeft = native.throwSessionIdAfterExitDelivered
    if (out.leases?.l2) out.sendByAgent = Object.fromEntries(['check6-l1','check6-l2','check6-l3']
      .map(id => [id, native.sends.filter(s => s.to?.agentId === id).length]))
    if (out.launchBefore) out.launchAfter = (await rpc('inspect')).reviews[seeded.lease_id].launch
    if (out.filesBefore) {
      out.filesAfter = await rpc('fingerprint')
      // The gate round records itself; the claim is about what the pump writes.
      out.filesChanged = writes(out.filesBefore, out.filesAfter).filter(name => !/\/gate-check\.json(\.lock)?$/.test(name))
    }
    out.afterCalls = calls.slice(callsBefore).map(c => c.event || c.tool)
      .filter(name => ['review_roll_forward', 'rework_dispatch', 'review_wake', 'host_attach', 'work_parallel_status'].includes(name))
      .reduce((acc, name) => ({ ...acc, [name]: (acc[name] || 0) + 1 }), {})
  }

  if (scenario === 'close-resolved-done' || scenario === 'close-resolved-removed' || scenario === 'close-resolved-open') {
    // Main completes or removes the Work item itself while the fork's result
    // waits in review (G:\\ciel-Walkie 2026-11-15). The next pump boundary ends
    // that review unreviewed; an item still open is never closed.
    const seeded = await rpc('seed', { title: 'Close' })
    await start('process')
    out.before = await rpc('inspect')
    const how = scenario === 'close-resolved-done' ? 'done' : scenario === 'close-resolved-removed' ? 'remove' : null
    if (how) out.board = await rpc('board_change', { item_id: seeded.item_id, how })
    await turnComplete()
    out.after = await rpc('inspect')
    out.closeCalls = count('review_close_resolved')
    out.reviewDecisions = calls.filter(c => c.tool === 'work_parallel_item_review').length
    out.pane = (await hooks['command:work-mode']($, { args: 'parallel', origin: { kind: 'composer' } })).text
    out.lease = seeded.lease_id; out.item = seeded.item_id
  }

  if (scenario === 'close-resolved-stranded') {
    // A stranded no-report review (n1971 on G:\\ciel-Walkie) for an item main
    // then completed itself ends at the next pump too.
    const seeded = await rpc('seed_running')
    await rpc('break_worktree', { lease_id: seeded.lease_id })
    const lease_id = seeded.lease_id, agent = seeded.agent_id
    native.agents.push({ id: agent, name: agent, status: 'running', type: 'fork' })
    await start('process')
    native.agents.find(row => row.id === agent).status = 'completed'
    await hooks['turn.complete']($, { agentId: agent, reason: 'answer' }, async e => e); await quiet()
    await hooks['turn.complete']($, { agentId: agent, reason: 'answer' }, async e => e); await quiet()
    await turnComplete()
    const status = () => rpc('tool', { tool:'work_parallel_status', args:{ session_id:'claude:S1', _host:{ token:init.token } } })
    out.before = { stranded: (await rpc('inspect')).stranded?.rows?.[lease_id] ?? null,
      pending: ((await status()).review?.pending || []).filter(row => row.lease_id === lease_id).length }
    out.board = await rpc('board_change', { item_id: seeded.item_id, how: 'done' })
    await turnComplete()
    out.after = { stranded: (await rpc('inspect')).stranded?.rows?.[lease_id] ?? null,
      pending: ((await status()).review?.pending || []).filter(row => row.lease_id === lease_id).length }
    out.spawns = [...native.spawns]
    out.pane = (await hooks['command:work-mode']($, { args: 'parallel', origin: { kind: 'composer' } })).text
    out.lease = lease_id; out.item = seeded.item_id
  }

  if (scenario === 'blocked-label') {
    // An approve whose board stage fails is shown with its reason, not "Finalizing".
    const seeded = await rpc('seed', { title: 'Blocked' })
    await start('process')
    await rpc('fault_board_accept', { message: 'still has unfinished children; finish them first' })
    out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'approve', evidence: 'main verified the diff' })
    out.after = await rpc('inspect')
    out.pane = (await hooks['command:work-mode']($, { args: 'parallel', origin: { kind: 'composer' } })).text
    out.lease = seeded.lease_id; out.item = seeded.item_id
  }

  if (scenario === 'capacity') {
    const seeded = await rpc('seed', { title: 'Capacity' })
    await start('process')
    await rpc('pause', { paused: true })
    out.decision = await reviewCall({ lease_id: seeded.lease_id, decision: 'rework', evidence: 'needs a slot' })
    out.waiting = await rpc('inspect')
    await rpc('pause', { paused: false })
    // An event boundary (child turn end), not a timer, rolls it forward.
    await hooks['turn.complete']($, { agentId: 'child' }, async e => e); await quiet()
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    out.final = await rpc('inspect')
    out.rollForwards = count('review_roll_forward')
    out.spawns = [...native.spawns]
    out.lease = seeded.lease_id
  }

  if (scenario === 'wake') {
    const seeded = await rpc('seed', { title: 'Wake' })
    await start('process')
    out.first = { prompts: native.prompts.length, appended: native.appended.length, backend: await rpc('inspect') }
    // Process restart while still undecided: the durable wake is not re-sent.
    await start('process')
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    out.second = { prompts: native.prompts.length, appended: native.appended.length, backend: await rpc('inspect') }
    out.prepares = count('review_wake', 'prepare'); out.records = count('review_wake', 'record')
    out.stop = await hooks['classic.Stop']($, { session_id: 'S1' }, async () => ({}))
    out.lease = seeded.lease_id
  }

  if (scenario === 'p0-immediate-paused') {
    // An existing canonical P1 root becomes effective P0 through the host-only
    // priority command. The pump launches it while policy is disabled+paused,
    // before any ordinary reserve_next attempt, under the backend dispatch name.
    await start('process')
    const seeded = await rpc('seed_leaf', { title:'P0 immediate' })
    await rpc('policy', { changes:{ enabled:false, paused:true } })
    const before = calls.length
    const next = engine({ ok:true })
    out.priority = await hooks['tool.call']($, { tool:'mcp__ciel-runtime-router__work_node_update',
      tool_use_id:'p0-priority', id:seeded.root_id, priority:'P0', expected_version:(await rpc('p0_view')).board.version }, next)
    await quiet()
    out.p0 = await rpc('p0_view')
    out.backend = await rpc('inspect')
    out.events = calls.slice(before).map(c => c.event || c.tool)
    out.spawns = [...native.spawns]
    out.prompt = native.spawnPrompts.get(native.spawns[0]) || ''
  }

  if (scenario === 'start-load') {
    // KOBE 2026-11-18: session.start held the hooks worker 172 s with about 360
    // router calls (mostly work_host_page). A large board and ledger, with the
    // router paging large host results as it does for real.
    await rpc('paging', { on: true })
    await rpc('policy', { changes: { launches_per_minute: 20 } })
    for (let i = 0; i < 6; i++) await rpc('seed', { title: 'Load ' + i + ' ' + 'x'.repeat(150) })
    out.board = await rpc('seed_board', { goals: 40, items: 6 })
    const tally = log => log.reduce((all, tool) => ({ ...all, [tool]: (all[tool] || 0) + 1 }), {})
    native.toolLog = []
    await start('process')
    out.start = { hookMs: native.startHookMs, callsAtReturn: native.startCallsAtReturn,
      settledMs: native.startSettledMs, total: native.toolLog.length, byTool: tally(native.toolLog) }
    native.toolLog = []
    const viewStarted = performance.now()
    const view = await hooks['command:work-mode']($, { args: 'view', origin: { kind: 'composer' } })
    out.view = { ms: performance.now() - viewStarted, callsAtReturn: native.toolLog.length,
      text: String(view?.text || '').slice(0, 80) }
    await quiet()
    out.view.total = native.toolLog.length
    out.view.byTool = tally(native.toolLog)
    out.leases = Object.keys((await rpc('inspect')).leases || {}).length
  }

  if (scenario === 'wake-busy') {
    // Main is mid-turn when the worker's completion lands.
    await start('process', { busy: true })
    const seeded = await rpc('seed', { title: 'Busy' })
    // A worker report is the event that wakes a busy main.
    await hooks['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_report', agentId: 'w' }, engine({ ok: true }))
    await quiet()
    out.prompts = native.prompts.length; out.appended = native.appended.length
    out.backend = await rpc('inspect'); out.lease = seeded.lease_id
    await start('process')
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    out.afterRestart = { prompts: native.prompts.length, appended: native.appended.length }
  }
} catch (error) {
  out.error = String(error.message || error); out.trace = error.trace || error.stack
}
try { out.failureLog = await rpc('failures') } catch (error) { out.failureLog = { error: String(error.message || error) } }
out.failureRecords = native.failureRecords
bridge.stdin.end()
out.calls = calls.map(c => c.event ? c.event + (c.operation ? ':' + c.operation : '') : c.tool)
out.hostMarkers = native.hostMarkers
out.asks = native.asks.map(row => ({ question:String(row.question).slice(0, 400), choices:row.choices }))
process.stdout.write(JSON.stringify({ scenario, ...out }) + '\n')
process.exit(0)

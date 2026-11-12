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
let traffic = 0
async function quiet(limit = 30000) {
  const end = Date.now() + limit
  let seen = -1
  while (Date.now() < end) {
    if (traffic === seen) return
    seen = traffic
    await sleep(400)
  }
  throw new Error('mod router traffic did not settle within ' + limit + ' ms')
}
const text = value => ({ content: [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value) }] })
const engine = value => { const f = async () => value; f.origin = { plugin: 'engine' }; return f }

const init = await rpc('init')
// Native agents outlive a mod restart; the list is the host's, not the mod's.
const native = { agents: [], spawns: [], prompts: [], appended: [], denyNext: 0, failBind: 0,
  answers: [], asks: [], toolCalls: [], hostMarkers: [], restartErrors: [], restartCalls: [],
  bindResults: [], reconcileErrors: [] }
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
async function start(kind, { busy = false } = {}) {
  if (kind !== 'process' && kind !== 'reload') throw new Error('start kind must be process or reload')
  if (kind === 'process' || processes === 0) { processEnv = new Map(); hostStore = new Map(); processes++ }
  hooks = {}
  const mod = await import(pathToFileURL(process.cwd() + '/register.js').href + '?g=' + (++generation))
  mod.register((name, filter, handler) => {
    if (filter?.command === 'btw_with_mcp' || filter?.requestId === 'ciel-side-intake') return
    // /work-mode is kept: its parallel view prints this module's failure log.
    if (name === 'command.run' && filter?.command === 'work-mode') { hooks['command:work-mode'] = handler; return }
    if (name === 'command.run' || name === 'ui.render') return
    // Only the Work & SSE pane's dashboard messages (its resolve control).
    if (name === 'ui.message' && filter?.element !== 'obs-graphic') return
    hooks[name] = typeof filter === 'function' ? filter : handler
  })
  $ = {
    session: { id: async () => 'S1', model: async () => 'fixture-main', repo: async () => null,
      root: async () => init.repo, send: async () => ({ isDelivered: true }),
      append: async args => { native.appended.push(args.message.content[0].text); return {} } },
    env: { get: async key => {
        if (key === 'CIEL_RUNTIME_WORK_PARALLEL_HOST_TOKEN') return init.token
        if (envFault.get) throw new Error('env read refused')
        return processEnv.get(key)
      },
      set: async (key, value) => {
        if (envFault.set) return { deny: 'env write refused' }
        if (value === undefined) processEnv.delete(key); else processEnv.set(key, value)
        return {}
      } },
    mcp: { connect: async () => ({ isConnected: true, server: 'ciel-runtime-router' }),
      call: async (_server, tool, args) => {
        traffic++
        if (tool.startsWith('work_parallel_')) calls.push({ tool, event: args.event, operation: args.operation })
        if (/^rework_launch_|^rework_spawn_/.test(String(args.event || '')))
          native.hostMarkers.push({ event: args.event, internal: args._host?.internal === true,
            nonce: digest(args._host?.launch_nonce), fork: args._host?.fork === true })
        // The router connection drops before a bind reaches the backend.
        if (args.event === 'bind' && native.failBind > 0) { native.failBind--; return { isError: true, ...text('router connection reset') } }
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
      } },
    ui: { invalidate() {}, toast() {}, log() {}, open: async () => {}, close: async () => {},
      ask: async (question, choices) => { native.asks.push({ question, choices }); return native.answers.shift() ?? 'Approve' } },
    clock: { after() { return { cancel() {} } } },
    tool: { list: async () => [], call: async args => { native.toolCalls.push(args); return {} } },
    agent: { list: async () => native.agents, register: async () => ({}),
      spawn: async args => {
        native.spawns.push(args.name)
        if (native.denyNext > 0) { native.denyNext--; return { deny: 'native launch refused' } }
        const row = { id: 'agent-' + native.spawns.length, name: args.name, status: 'running' }
        native.agents.push(row)
        return { agentId: row.id }
      } },
    prompt: { submit: async args => { native.prompts.push(args.text); return {} } },
    state: {get:async ref=>{const held=hostStore.get(ref.plugin+'/'+ref.key);return {value:held?structuredClone(held.value):undefined,version:held?.version||0}},set:async (ref,value,options={})=>{const name=ref.plugin+'/'+ref.key,held=hostStore.get(name)||{version:0};if(options.ifVersion!==undefined&&options.ifVersion!==held.version)return {isSet:false,version:held.version};hostStore.set(name,{value:structuredClone(value),version:held.version+1});return {isSet:true,version:held.version+1}}},
    command: { register: async () => {} },
  }
  await hooks['session.start']($, { isInteractive: true }, async e => e)
  if (busy) await hooks['turn.start']($, {}, async e => e)
  await quiet()
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
        text: text.split('\n').filter(line => line.includes('lease ' + lease_id) || line.includes('lease ' + out.replacement)),
        web: (web.parallel_reviews || []).map(row => [row.lease_id, row.state]),
        model: (status.review?.pending || []).map(row => [row.lease_id, row.state]) })
    }
    out.decision = await reviewCall({ lease_id, decision: 'rework', evidence: 'add the missing case' })
    out.replacement = (await rpc('inspect')).reviews[lease_id].replacement_lease_id
    await view('rework bound and running')
    // The resume fork reports with its own capability and goes idle.
    const token = JSON.parse(prompts[0].slice(prompts[0].indexOf('Assignment JSON:\n') + 'Assignment JSON:\n'.length)).worker_token
    out.reported = (await rpc('report_completion', { lease_id: out.replacement, worker_token: token, label: 'g2' })).state
    await view('replacement submitted, native still running')
    for (const row of native.agents) row.status = 'idle'
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    out.afterIdle = await rpc('inspect')
    await view('replacement review pending')
    out.approve = await reviewCall({ lease_id: out.replacement, decision: 'approve', evidence: 'rework verified' })
    out.final = await rpc('inspect')
    await view('replacement finalized')
    await start('process')
    await view('after a new process')
    out.lease = lease_id
    $.agent.spawn = realSpawn
  }

  const BIND_LAUNCH_VARIANTS = ['race', 'exhausted', 'restart', 'manual', 'manual-restart', 'none', 'denied']
  if (scenario.startsWith('bind-launch-bound-') && BIND_LAUNCH_VARIANTS.includes(scenario.slice(18))) {
    // Concurrent finalization writes land between _bind_rework_launch's reads
    // and its launch_bound CAS. One write exercises in-request retry; 100 writes
    // exhaust every bounded attempt so a later boundary (or restart) must replay bind.
    const variant = scenario.slice(18)
    const exhausted = variant !== 'race'
    const restartRecovery = variant === 'restart' || variant === 'manual-restart'
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
      await start('process')
      out.raceAfterRecovery = await rpc('launch_bound_race')
      await snapshot('after process restart (session.start)')
      await idempotentResend('after restart recovery')
      await hooks['turn.complete']($, {}, async e => e); await quiet()
      out.restartCalls = native.restartCalls.slice(restartBefore).map(row => ({ lease_id:row.lease_id, error:row.error }))
      await snapshot('after turn.complete in the new process')
    } else {
      if (manualFirst) {
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
      await idempotentResend('after bind recovered')
      out.report = await rpc('report_completion', { lease_id:out.replacement, worker_token:token, label:'race' })
        .then(value => ({ state:value.state }), error => ({ error:error.message }))
      for (const row of native.agents) row.status = 'idle'
      await hooks['turn.complete']($, {}, async e => e); await quiet()
      await snapshot('after report and idle turn.complete')
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
    out.final = await rpc('inspect')
    out.spawns = [...native.spawns]
    out.absent = count('rework_launch_absent')
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
    native.agents.push({ id:'agent-late', name:native.spawns[0], status:'running' })
    await hooks['turn.complete']($, {}, async e => e); await quiet()
    out.afterLate = await rpc('inspect')
    out.stopped = native.toolCalls.filter(row => row.tool === 'TaskStop').map(row => row.task_id)
    out.lateNotice = await hooks['session.receive']($, { origin:{ kind:'task-notification' },
      text:'<task-notification><task-id>agent-late</task-id></task-notification>' }, async e => e)
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
    native.agents.push({ id:'agent-late', name:native.spawns[0], status:'running' })
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
    native.agents.push({ id:'agent-late', name:native.spawns[0], status:'running' })
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
      native.agents.push({ id:'agent-started', name:args.name, status:'running' })
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
    out.forgedRouterCalls = calls.slice(forgedBefore).filter(c => ['review_wake','review_roll_forward'].includes(c.event)).length
    // The router itself refuses what the mod forwards for a model call ({agent_id}) or an unmarked call.
    const refused = args => rpc('tool', { tool:'work_parallel_event', args:{ session_id:'claude:S1',
      expected_version:0, ...args, _host:{ token:init.token, agent_id:null } } }).then(value => ({ value }), error => ({ error:error.message }))
    out.backendWake = await refused({ event:'review_wake', operation:'prepare', lease_id:seeded.lease_id })
    out.backendRoll = await refused({ event:'review_roll_forward', lease_id:seeded.lease_id })
    out.backendForkLaunch = await rpc('tool', { tool:'work_parallel_event', args:{ session_id:'claude:S1', expected_version:0,
      event:'rework_launch_status', _host:{ token:init.token, fork:true } } }).then(value => ({ value }), error => ({ error:error.message }))
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
bridge.stdin.end()
out.calls = calls.map(c => c.event ? c.event + (c.operation ? ':' + c.operation : '') : c.tool)
out.hostMarkers = native.hostMarkers
out.asks = native.asks.map(row => ({ question:String(row.question).slice(0, 400), choices:row.choices }))
process.stdout.write(JSON.stringify({ scenario, ...out }) + '\n')
process.exit(0)

// Drives the packaged ciel-work register.js with a scripted host: no Claude Code,
// no router. Each scenario prints one JSON line describing the gate decisions.
import { register } from './register.js'
import { pollSide, sidePromptSubmit, sidePromptPending } from './side-intake.js'
import { createHash } from 'node:crypto'
import { copyFileSync, mkdirSync } from 'node:fs'
// One side pump pass through the same module instance register.js uses.
const sidePump = $ => pollSide($, 'claude:S1')
// side_wake.attempt_ref: what a redacted row names an attempt by.
const attemptRef = id => createHash('sha256').update(String(id)).digest('hex').slice(0, 16)
import { atom, read, update } from 'claude-code'

const handlers = {}
const sideHandlers = {}
register((name, filter, handler) => {
  if (filter?.command === 'btw_with_mcp' || filter?.requestId === 'ciel-side-intake') sideHandlers[name] = handler
  else handlers[name] = typeof filter === 'function' ? filter : handler
  // KOBE hotfix 2026-11-18: session.start returns before its board read, side
  // start, hydrate and pump, and turn.complete before its pump. Scenarios
  // observe after that work, as they did when the hooks themselves awaited it.
  if (name === 'session.start' || name === 'turn.complete') {
    const hook = handlers[name]
    handlers[name] = async ($, e, nextFn) => {
      const result = await hook($, e, nextFn)
      await startSettled($)
      return result
    }
  }
})
// Settled: no router call in flight and none started for three 5 ms ticks
// (bounded at 2 s, so a scenario that holds a router call goes on).
async function startSettled($) {
  const state = $?.__harnessState
  let seen = -1, still = 0
  for (let i = 0; i < 400 && still < 3; i++) {
    await new Promise(resolve => setTimeout(resolve, 5))
    if (state && (state.inflight > 0 || state.calls !== seen)) { seen = state.calls; still = 0 } else still++
  }
}
// The engine's claude-code helpers are stubbed by the pytest fixture: atoms
// live in a shared map keyed by plugin+key. Probe the detail pane state there.
const detailAtom = atom({ plugin: 'ciel-work', key: 'detail' }, null)
const readDetailAtom = $ => read($, detailAtom)
const writeDetailAtom = async ($, v) => update($, detailAtom, () => v)

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
// The mods' clock, moved by a scenario with later(): side-intake.js lets a main
// step prove an appended row was carried only once the row is older than its
// hook-delay grace (1 s). Scenarios that never call later() keep real time.
const realNow = Date.now.bind(Date)
let clockSkew = 0
Date.now = () => realNow() + clockSkew
const later = (ms = 2000) => { clockSkew += ms }
const snapshot = (phase = 'executing', version = 5) => ({ enabled: true, phase, approval: 'auto', version,
  done: 0, total: 1, active_root: { id: 'n1', title: 'goal', status: 'in_progress' }, auto_continue: true,
  continuation: { actionable: 0, waiting: [] }, queue: [] })
const text = value => ({ content: [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value) }] })

// The env interleave a concurrent first establishment can hit. The first
// `callers` nonce reads wait for each other (up to 1 s), so every caller sees
// the env before any write. Each write is then applied only after the previous
// writer's read-back: get x N, set, read, set, read, ... An establishment that
// is not shared returns a different value to every caller under this order.
function nonceRace(callers) {
  return { callers, arrived: 0, released: false, waiting: [], concurrentGets: 0,
    awaitingReadback: false, pendingSets: [], log: [] }
}
function host(respond) {
  // A fresh host is a fresh claude.exe process: its env Map starts empty.
  const state = { session: 'S1', calls: 0, inflight: 0, logs: [], env: new Map(), envFault: null, nonceRace: null, routerNonces: [] }
  // The engine's host-held $.state, one per fresh host (process).
  const hostStore = new Map()
  const NONCE = 'CIEL_WORK_HOST_PROCESS_NONCE'
  const raceGet = async race => {
    if (!race.released) {
      race.arrived++
      race.concurrentGets = Math.max(race.concurrentGets, race.arrived)
      const seen = state.env.get(NONCE)
      race.log.push('get')
      if (race.arrived >= race.callers) { race.released = true; race.waiting.splice(0).forEach(go => go()) }
      else await new Promise(go => { race.waiting.push(go); setTimeout(() => { race.released = true; go() }, 1000) })
      return seen
    }
    const value = state.env.get(NONCE)
    race.log.push('get')
    if (race.awaitingReadback) { race.awaitingReadback = false; race.pendingSets.shift()?.() }
    return value
  }
  const $ = {
    session: { id: async () => state.session, model: async () => 'fixture-main', messages: async () => [],
      send: async args => { (state.sent ||= []).push(args); return { isDelivered: true } },
      append: async args => { (state.appended ||= []).push(args); return {uuid:'append-fixture'} } },
    prompt: { submit: async args => { (state.submitted ||= []).push(args); return {} } },
    state: {get:async ref=>{const held=hostStore.get(ref.plugin+'/'+ref.key);return {value:held?structuredClone(held.value):undefined,version:held?.version||0}},set:async (ref,value,options={})=>{const name=ref.plugin+'/'+ref.key,held=hostStore.get(name)||{version:0};if(options.ifVersion!==undefined&&options.ifVersion!==held.version)return {isSet:false,version:held.version};hostStore.set(name,{value:structuredClone(value),version:held.version+1});return {isSet:true,version:held.version+1}}},
    mcp: { connect: async () => ({}), call: async (server, tool, args) => {
      state.calls++
      // What the router receives, never what the fake env holds.
      if (args?._host && 'launch_nonce' in args._host) state.routerNonces.push({ event: args.event, nonce: args._host.launch_nonce })
      state.inflight++
      try { return await respond(tool, args, state) } finally { state.inflight-- }
    } },
    ui: { invalidate() {}, toast() {}, log(t) { state.logs.push(t) }, open: async () => {}, ask: async () => 'Approve' },
    clock: { after() { return { cancel() {} } } },
    // Stateful only for the rework launch nonce (register.js reworkLaunchNonce),
    // which is set once and read back; every other name is unchanged.
    // envFault: 'set-throws' makes $.env.set throw; 'never' makes a write vanish;
    // 'deny' returns a deny result. nonceRace forces the interleave above.
    env: { get: async key => {
        if (key !== NONCE) return 'host-token'
        return state.nonceRace ? raceGet(state.nonceRace) : state.env.get(key)
      },
      set: async (key, value) => {
        if (key !== NONCE) return {}
        const race = state.nonceRace
        if (race) {
          if (race.awaitingReadback) await new Promise(go => race.pendingSets.push(go))
          race.log.push('set')
        }
        if (state.envFault === 'set-throws') throw new Error('env write refused')
        if (state.envFault === 'deny') return { deny: 'env write refused' }
        if (race) race.awaitingReadback = true
        if (state.envFault !== 'never') state.env.set(key, value)
        return {}
      } },
    // The fork gate plugin's presence answer (ciel-work-gate); forkGate:false models a missing gate.
    tool: { list: async () => [], check: async q => typeof q?.input?.ciel_work_gate_probe === 'string' && state.forkGate !== false ? { decision: 'ask', plugin: 'ciel-work-gate', reason: 'ciel-work-gate 1 ' + q.input.ciel_work_gate_probe } : { decision: 'allow' } },
    agent: { list: async () => state.agents || [], register: async () => ({}),
      spawn: async args => { if (args.subagentType !== 'ciel-work:side-intake') return {deny:'fixture'}; state.sideSpawn = args; return {agentId:'side1'} } },
    command: { register: async () => {} },
  }
  Object.defineProperty($, '__harnessState', { value: state })
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
// Main turn events in the order owned probes stopblock-838268bd/d9595ffa saw:
// turn.start T, turn.step T#0, Stop (block), turn.step T#1, Stop, turn.complete T.
const engineStream = async function* () { yield { kind:'engine' } }
const mainStep = async ($, turnId, index) => {
  const rows = await $.session.messages()
  for await (const _ of handlers['turn.step']($, { turnId, index, messageCount:rows.length }, engineStream)) { void _ }
}
const mainTurn = async ($, turnId) => {
  await handlers['turn.start']($, { turnId, text:'continue' }, next({}))
  await mainStep($, turnId, 0)
}

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
  async 'side-status-delivers-failed-original-without-free-actor-slot'() {
    const id = 'c'.repeat(64)
    const original = 'failed original from status ' + 'q'.repeat(9000)
    const forwardText = '[Ciel side 처리 실패 · 원문 전달]\n\n' + original
    const timers = [], calls = []
    let status = 0
    const { $, state } = host(async (tool,args) => {
      calls.push(tool + (args?.state ? ':' + args.state : ''))
      if (tool === 'work_status') return text({ ...snapshot(), side_intake_enabled:true })
      if (tool === 'work_side_claim') return text({ claim:null, settings:{}, main_forwards:[] })
      if (tool === 'work_side_status') return text({ enabled:true, side_inbox_receipts:[],
        main_forwards: status++ === 0 ? [{ receipt_id:id, text:forwardText }] : [] })
      if (tool === 'work_side_wake' && args.state === 'notice_prepare')
        return text({ receipt_id:id, protocol:2, state:'claimed', attempt_id:'e'.repeat(32) })
      if (tool === 'work_side_wake') return text({ receipt_id:id, protocol:2, state:args.state.slice(7) })
      return text({})
    })
    $.clock.after=(delay,callback)=>{timers.push({delay,callback});return{cancel(){}}}
    await handlers['session.start']($,{isInteractive:false},next(undefined))
    const tick=timers.find(t=>t.delay===2000); await tick.callback(); await sleep(10)
    const appended=(state.appended||[]).map(row=>row.message.content[0].text)
    return {appended:appended.length,carriesOriginal:appended.length===1&&appended[0].includes(original),calls}
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
        return text(id ? { claim: { receipt_id: id, generation: ('4' + id.slice(1, 2)).repeat(16), text: 'event ' + id.slice(0, 2), route: {},
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
  // none_referenced: the event named no source message, so no reply exists.
  async 'side-review-not-applicable'() {
    const rows = [{ receipt_id:'n'.repeat(64), source:'none_referenced', kind:'request', item_id:'n7', summary:'queued build check',
        reply:'COMPOSED-REPLY-TEXT', delivery:'not_applicable', source_context:'none_referenced' },
      { receipt_id:'s'.repeat(64), source:'message:c:s', kind:'info', summary:'sent one', reply:'sent reply', delivery:'submitted' }]
    const { $ } = host(async () => text({ ...snapshot(), auto_continue:false, side_reviews:rows }))
    const promptNext=async e=>e; promptNext.origin={plugin:'engine'}
    const prompt = await handlers['prompt.submit']($, { origin:{kind:'composer'}, text:'continue', context:[] }, promptNext)
    const stop = await handlers['classic.Stop']($, { session_id:'S1' }, next({}))
    return { context:(prompt.context||[]).join('\n'), block:stop?.block || '' }
  },
  async 'side-advice-stop'() {
    // New-router redacted row (delivery_uncertain present) and its wake ledger.
    let wake = {state:'pending',requested_at:1,revision:0,delivery_uncertain:false}
    const {$}=host(async(tool,args)=>{
      if (tool === 'work_side_wake' && args.state === 'prepare') {
        wake = {...wake,state:'submitting',phase:'intent',revision:1}
        return text({protocol:2,state:'submitting',phase:'intent',attempt_id:'c'.repeat(32),owner_nonce:args.owner_nonce,channel:args.channel,revision:1})
      }
      if (tool === 'work_side_wake' && args.state === 'start') {
        wake = {...wake,phase:'started',revision:2,attempt_ref:attemptRef('c'.repeat(32)),delivery_uncertain:true}
        return text({protocol:2,state:'submitting',phase:'started',attempt_id:'c'.repeat(32),owner_nonce:args.owner_nonce,channel:args.channel,revision:2})
      }
      if (tool === 'work_parallel_status') return text({version:1,policy:{enabled:false},leases:{}})
      return text({...snapshot(),auto_continue:false,side_handoffs:[{event_id:'event',item_id:'n2',kind:'resume',summary:'P1 deadline',question:'Need decision',
        waits:[{id:'n3',waiting_on:'Daniel approval 118269'}],gates:[{id:'n3',waiting_on:'Daniel approval 118269',blocked:false}],wake:{...wake}}]})
    })
    await mainTurn($,'T')
    const first = await handlers['classic.Stop']($,{session_id:'S1'},next({}))
    const second = await handlers['classic.Stop']($,{session_id:'S1'},next({}))
    return {...first,second}
  },
  // Stop's start call fails. 'uncommitted': the row stays this owner's stop
  // intent. 'committed': start committed but its answer was lost. Either way
  // Stop returns no block, shows no uncertainty, and the pump delivers once.
  async 'side-wake-stop-start-lost'() {
    const variant = process.argv[3] || 'uncommitted'
    const event = 's'.repeat(64)
    let wake = { state:'pending', revision:0, delivery_uncertain:false }
    let startFaults = 1
    const calls = []
    const pub = () => ({ state:wake.state, phase:wake.phase, channel:wake.channel, revision:wake.revision,
      attempt_ref:wake.attempt_id ? attemptRef(wake.attempt_id) : undefined,
      delivery_uncertain:wake.state === 'submitting' && wake.phase === 'started' })
    const row = hostView => ({ event_id:event, item_id:'n2', kind:'resume', summary:'Start lost', question:'', waits:[],
      wake: hostView ? { ...pub(), attempt_id:wake.attempt_id } : pub() })
    const { $, state } = host(async (tool, args) => {
      calls.push({ tool, args })
      if (tool === 'work_status') return text({ ...snapshot(), auto_continue:false, side_handoffs:[row(false)] })
      if (tool === 'work_parallel_status') return text({ version:1, policy:{ enabled:false }, leases:{} })
      if (tool === 'work_side_claim') return text({ claim:null, settings:{}, handoff_replies:[],
        main_wakes: wake.state === 'pending' || (wake.state === 'submitting') ? [row(true)] : [] })
      if (tool === 'work_side_wake' && args.state === 'prepare') {
        if (wake.state === 'pending') wake = { state:'submitting', phase:'intent', revision:1, attempt_id:'d'.repeat(32), owner:args.owner_nonce, channel:args.channel }
        if (wake.state === 'submitting' && wake.phase === 'intent' && wake.owner === args.owner_nonce)
          return text({ protocol:2, state:'submitting', phase:'intent', attempt_id:wake.attempt_id, owner_nonce:wake.owner, channel:wake.channel, revision:wake.revision })
        return text({ protocol:2, state:'already_' + wake.state, phase:wake.phase, revision:wake.revision })
      }
      if (tool === 'work_side_wake' && args.state === 'start') {
        if (wake.phase !== 'intent' || args.channel !== wake.channel || args.attempt_id !== wake.attempt_id)
          return { isError:true, ...text('stale side wake intent') }
        if (startFaults-- > 0) {
          if (variant === 'committed') wake = { ...wake, phase:'started', revision:2 }
          throw new Error('transport lost')
        }
        wake = { ...wake, phase:'started', revision:2 }
        return text({ protocol:2, state:'submitting', phase:'started', attempt_id:wake.attempt_id, owner_nonce:wake.owner, channel:wake.channel, revision:2 })
      }
      if (tool === 'work_side_wake' && ['queued', 'appended', 'uncertain', 'failed'].includes(args.state)) {
        if (wake.phase !== 'started' || args.attempt_id !== wake.attempt_id || args.owner_nonce !== wake.owner)
          return { isError:true, ...text('stale side wake attempt') }
        wake = { ...wake, state:args.state, revision:wake.revision + 1 }
        return text({ protocol:2, state:args.state, revision:wake.revision })
      }
      return text({})
    })
    await mainTurn($, 'T')
    const stop = await handlers['classic.Stop']($, { session_id:'S1' }, next({}))
    const afterStop = { state:wake.state, phase:wake.phase }
    // A second Stop before the pump: the row is this module's own, never "uncertain".
    const again = await handlers['classic.Stop']($, { session_id:'S1' }, next({}))
    // Nor does the side pane offer a retry for it.
    $.ui.resolve = () => ({ Box:props => props, Text:props => props, Button:props => props })
    await handlers['command.run']($, { args:'status' })
    const pane = await sideHandlers['ui.render']($, { component:'Pane', requestId:'ciel-side-intake' }, next(null))
    const paneUncertain = pane.children.some(r => r.label === 'Retry main wake…'
      || (typeof r.children === 'string' && r.children.startsWith('Main delivery uncertain')))
    const beforePump = { state:wake.state, phase:wake.phase, submitted:(state.submitted || []).length }
    await sidePump($)
    await sleep(10)
    await sidePump($)
    await sleep(10)
    const later = await handlers['classic.Stop']($, { session_id:'S1' }, next({}))
    return { variant, stopBlock:Boolean(stop?.block), againBlock:again?.block || '', afterStop, paneUncertain, beforePump,
      submitted:(state.submitted || []).length, appended:(state.appended || []).length,
      wake:{ state:wake.state, phase:wake.phase, channel:wake.channel }, laterBlock:later?.block || '',
      calls:calls.filter(c => c.tool === 'work_side_wake').map(c => c.args.state + ':' + (c.args.channel || '')),
      staleShown:state.logs.some(l => /stale|transport/i.test(String(l))) }
  },
  // The pane offers a retry only for an uncertain wake this process does not
  // own; the retry needs an explicit confirm and sends redacted fields only.
  async 'side-wake-pane-retry'() {
    const calls = []
    const answers = ['Cancel', 'Retry main wake (may duplicate)']
    const handoff = { event_id:'r'.repeat(64), item_id:'n2', kind:'resume', summary:'Uncertain wake', question:'', waits:[],
      wake:{ state:'submitting', phase:'started', revision:4, attempt_ref:'ref-pane', delivery_uncertain:true } }
    const { $ } = host(async (tool, args) => { calls.push({ tool, args })
      if (tool === 'work_status') return text({ ...snapshot(), side_handoffs:[handoff] })
      if (tool === 'work_side_status') return text({ enabled:true, side_inbox_receipts:[] })
      if (tool === 'work_side_claim') return text({ claim:null, settings:{}, handoff_replies:[], main_wakes:[] })
      if (tool === 'work_side_wake') return text({ protocol:2, state:'pending', revision:5 })
      return text({}) })
    $.ui.ask = async () => answers.shift()
    $.ui.resolve = () => ({ Box:props => props, Text:props => props, Button:props => props })
    await handlers['command.run']($, { args:'status' })
    const pane = await sideHandlers['ui.render']($, { component:'Pane', requestId:'ciel-side-intake' }, next(null))
    const rows = pane.children
    const label = rows.find(r => typeof r.children === 'string' && r.children.startsWith('Main delivery uncertain'))?.children || ''
    const button = rows.find(r => r.label === 'Retry main wake…')
    await button.onPress()
    const afterCancel = calls.filter(c => c.tool === 'work_side_wake').length
    await button.onPress()
    return { label, afterCancel, retries:calls.filter(c => c.tool === 'work_side_wake').map(c => c.args) }
  },
  // A released-wait notice whose outcome is unknown appears in the Side pane
  // from the host-only receipt projection's bounded boolean; only a confirmed
  // press releases it, by receipt id alone.
  async 'side-notice-pane-retry'() {
    const calls = []
    const answers = ['Cancel', 'Retry release notice (may duplicate)']
    const row = (id, uncertain) => ({ receipt_id:id.repeat(64), stage:'processed', item_id:'n2', recap:'approval 7 granted',
      original:'approval 7 granted', notice_delivery_uncertain:uncertain,
      notice_attempt_ref:id.repeat(16), notice_revision:2,
      result:{ action:'update', summary:'released', reply:'ok', delivery:'route_unavailable', handoff:'none', version:3 } })
    const { $ } = host(async (tool, args) => { calls.push({ tool, args })
      if (tool === 'work_status') return text({ ...snapshot(), side_handoffs:[] })
      if (tool === 'work_side_status') return text({ enabled:true, side_inbox_receipts:[row('s', false), row('u', true)] })
      if (tool === 'work_side_claim') return text({ claim:null, settings:{}, handoff_replies:[], main_wakes:[], main_notices:[] })
      if (tool === 'work_side_wake') return text({ protocol:2, state:'released', notice_delivery_uncertain:false })
      return text({}) })
    $.ui.ask = async () => answers.shift()
    $.ui.resolve = () => ({ Box:props => props, Text:props => props, Button:props => props })
    await sideHandlers['command.run']($, { args:'receipts' })
    const pane = await sideHandlers['ui.render']($, { component:'Pane', requestId:'ciel-side-intake' }, next(null))
    const rows = pane.children
    const labels = rows.filter(r => typeof r.children === 'string' && r.children.startsWith('Release notice uncertain'))
      .map(r => r.children)
    const buttons = rows.filter(r => r.label === 'Retry release notice…')
    await buttons[0]?.onPress()
    const afterCancel = calls.filter(c => c.tool === 'work_side_wake').length
    await buttons[0]?.onPress()
    return { labels, buttons:buttons.length, afterCancel, retries:calls.filter(c => c.tool === 'work_side_wake').map(c => c.args) }
  },
  // Shared fake of the protocol-2 wake ledger for the Stop/pump scenarios.
  async 'side-wake-stop-election'() {
    const event = 'e'.repeat(64), attempt = 'a'.repeat(32)
    let wake = { state:'pending', revision:0, delivery_uncertain:false }
    const calls = []
    const board = () => ({ ...snapshot(), auto_continue:false, side_handoffs:[{ event_id:event, item_id:'n2', kind:'resume',
      summary:'Stop election', question:'', waits:[], wake:{ ...wake } }] })
    const { $ } = host(async (tool, args) => {
      calls.push({ tool, args })
      if (tool === 'work_status') return text(board())
      if (tool === 'work_side_wake' && args.state === 'prepare') {
        wake = { state:'submitting', phase:'intent', revision:1, attempt_ref:attemptRef(attempt), delivery_uncertain:false }
        return text({ protocol:2, state:'submitting', phase:'intent', attempt_id:attempt, owner_nonce:args.owner_nonce, channel:'stop', revision:1 })
      }
      if (tool === 'work_side_wake' && args.state === 'start') {
        wake = { state:'submitting', phase:'started', revision:2, attempt_ref:attemptRef(attempt), delivery_uncertain:true }
        return text({ protocol:2, state:'submitting', phase:'started', attempt_id:attempt, owner_nonce:args.owner_nonce, channel:'stop', revision:2 })
      }
      if (tool === 'work_side_wake' && ['queued','appended'].includes(args.state)) {
        wake = { state:args.state, phase:'started', revision:3, attempt_ref:attemptRef(attempt), delivery_uncertain:false }
        return text({ protocol:2, state:args.state, revision:3 })
      }
      if (tool === 'work_parallel_status') return text({ version:1, policy:{ enabled:false }, leases:{} })
      if (tool === 'work_side_claim') return text({ claim:null, settings:{}, handoff_replies:[], main_wakes:[] })
      return text({})
    })
    const records = () => calls.filter(c => c.tool === 'work_side_wake' && c.args.state === 'queued').length
    // proof: 'next-step' (T#1), 'other-turn' (U#5 first), 'same-index' (T#0
    // again first), 'interrupt' (turn.complete T aborted before T#1).
    const proof = process.argv[3] || 'next-step'
    await mainTurn($, 'T')
    const first = await handlers['classic.Stop']($, { session_id:'S1' }, next({}))
    const beforeStep = { state:wake.state, phase:wake.phase, records:records() }
    let early = null
    if (proof === 'other-turn') { await mainStep($, 'U', 5); early = records() }
    if (proof === 'same-index') { await mainStep($, 'T', 0); early = records() }
    if (proof === 'interrupt') {
      await handlers['turn.complete']($, { turnId:'T', reason:'aborted' }, next({}))
      early = records()
    }
    await mainStep($, 'T', 1)
    const afterStep = { state:wake.state, records:records() }
    // The continuation's own Stop (stop_hook_active) and a later ordinary Stop.
    const second = await handlers['classic.Stop']($, { session_id:'S1', stop_hook_active:true }, next({}))
    await mainTurn($, 'T2')
    const third = await handlers['classic.Stop']($, { session_id:'S1' }, next({}))
    return { proof, first, beforeStep, early, afterStep, second, third:third?.block || '',
      calls:calls.filter(c => c.tool === 'work_side_wake').map(c => c.args.state) }
  },
  async 'side-wake-uncertain-and-ack-retry-once'() {
    const event = 'u'.repeat(64)
    let mode = 'uncertain', revision = 7
    const board = () => ({ ...snapshot(), auto_continue:false, side_handoffs:[{ event_id:event, item_id:'n2', kind:'resume',
      summary:'Uncertain', question:'', waits:[], wake: mode === 'uncertain'
        ? { state:'submitting', phase:'started', revision, attempt_ref:'ref-u', delivery_uncertain:true }
        : { state:'queued', phase:'started', revision, attempt_ref:'ref-u', delivery_uncertain:false, needs_ack_retry:true } }] })
    const { $ } = host(async tool => tool === 'work_status' ? text(board())
      : tool === 'work_parallel_status' ? text({ version:1, policy:{ enabled:false }, leases:{} })
        : text({ claim:null, settings:{}, handoff_replies:[], main_wakes:[] }))
    // A Stop-block continuation (stop_hook_active) never adds these notices.
    const continuing = { session_id:'S1', stop_hook_active:true }
    const uncertainContinuing = await handlers['classic.Stop']($, continuing, next({}))
    const uncertain1 = await handlers['classic.Stop']($, { session_id:'S1' }, next({}))
    const uncertain2 = await handlers['classic.Stop']($, { session_id:'S1' }, next({}))
    mode = 'ack'; revision++
    const ackContinuing = await handlers['classic.Stop']($, continuing, next({}))
    const ack1 = await handlers['classic.Stop']($, { session_id:'S1' }, next({}))
    const ack2 = await handlers['classic.Stop']($, { session_id:'S1' }, next({}))
    const ack3 = await handlers['classic.Stop']($, continuing, next({}))
    return { uncertainContinuing, uncertain1, uncertain2, ackContinuing, ack1, ack2, ack3 }
  },
  async 'side-wake-stop-legacy-router'() {
    // An old router row has no redacted delivery_uncertain field: Stop presents nothing.
    const { $, state } = host(async tool => tool === 'work_status' ? text({ ...snapshot(), auto_continue:false,
      side_handoffs:[{ event_id:'l'.repeat(64), item_id:'n2', kind:'resume', summary:'Legacy', question:'', waits:[], wake:{ state:'pending' } }] })
      : tool === 'work_parallel_status' ? text({ version:1, policy:{ enabled:false }, leases:{} })
        : text({ claim:null, settings:{}, handoff_replies:[], main_wakes:[] }))
    const calls = []
    const original = $.mcp.call
    $.mcp.call = async (server, tool, args) => { calls.push(tool + ':' + (args?.state || '')); return original(server, tool, args) }
    await mainTurn($, 'T')
    const stop = await handlers['classic.Stop']($, { session_id:'S1' }, next({}))
    return { block:Boolean(stop?.block), wakeCalls:calls.filter(c => c.startsWith('work_side_wake')), submitted:(state.submitted || []).length }
  },
  async 'side-wake-stop-pump-same-owner'() {
    const order = process.argv[3] || 'stop-first'
    const event = 'p'.repeat(64), attempt = 'b'.repeat(32)
    let wake = { state:'pending', revision:0, delivery_uncertain:false }, channel = null
    const calls = []
    const row = () => ({ event_id:event, item_id:'n2', kind:'resume', summary:'Race', question:'', waits:[], wake:{ ...wake } })
    let releasePrepare = null
    const prepareGate = order === 'interleaved' ? new Promise(resolve => { releasePrepare = resolve }) : null
    const { $, state } = host(async (tool, args) => {
      calls.push({ tool, args })
      if (tool === 'work_status') return text({ ...snapshot(), auto_continue:false, side_handoffs:[row()] })
      if (tool === 'work_parallel_status') return text({ version:1, policy:{ enabled:false }, leases:{} })
      if (tool === 'work_side_claim') return text({ claim:null, settings:{}, handoff_replies:[], main_wakes:[row()] })
      if (tool === 'work_side_wake' && args.state === 'prepare') {
        if (wake.state === 'pending') {
          channel = args.channel
          wake = { state:'submitting', phase:'intent', revision:1, attempt_ref:attemptRef(attempt), delivery_uncertain:false }
          if (prepareGate && channel === 'stop') await prepareGate
        }
        // Same owner adopts its own intent: the stored channel comes back.
        return text({ protocol:2, state:'submitting', phase:wake.phase, attempt_id:attempt, owner_nonce:args.owner_nonce, channel, revision:wake.revision })
      }
      if (tool === 'work_side_wake' && args.state === 'start') {
        if (wake.phase !== 'intent' || args.channel !== channel) return { isError:true, ...text('stale side wake intent') }
        wake = { state:'submitting', phase:'started', revision:2, attempt_ref:attemptRef(attempt), delivery_uncertain:true }
        return text({ protocol:2, state:'submitting', phase:'started', attempt_id:attempt, owner_nonce:args.owner_nonce, channel, revision:2 })
      }
      if (tool === 'work_side_wake' && ['queued', 'appended'].includes(args.state)) {
        wake = { state:args.state, phase:'started', revision:3, attempt_ref:attemptRef(attempt), delivery_uncertain:false }
        return text({ protocol:2, state:args.state, revision:3 })
      }
      return text({})
    })
    const step = () => mainStep($, 'T', 1)
    // Stop runs at the end of a busy main turn (working), as on the engine.
    await mainTurn($, 'T')
    let stop
    if (order === 'pump-first') {
      await sidePump($)
      // The append and its record land before the turn reaches Stop.
      await sleep(40)
      stop = await handlers['classic.Stop']($, { session_id:'S1' }, next({}))
    } else if (order === 'interleaved') {
      const stopping = handlers['classic.Stop']($, { session_id:'S1' }, next({}))
      await sleep(5)
      const pumping = sidePump($)
      await sleep(5)
      releasePrepare()
      stop = await stopping
      await pumping
    } else {
      stop = await handlers['classic.Stop']($, { session_id:'S1' }, next({}))
      await sidePump($)
    }
    if (stop?.block) await step()
    await sleep(10)
    return { order, block:Boolean(stop?.block), elected:calls.some(c => c.tool === 'work_side_wake' && c.args.channel === 'stop'),
      submitted:(state.submitted || []).length, appended:(state.appended || []).length,
      wake, calls:calls.filter(c => c.tool === 'work_side_wake').map(c => c.args.state + ':' + (c.args.channel || '')),
      staleShown:state.logs.some(l => /stale/i.test(String(l))) }
  },
  // The SSE actor is a native fork. Its exact pending row passes the Work
  // agent.spawn gate before any Work ledger read; a look-alike does not.
  async 'side-fork-authorized-before-work-gate'() {
    const id = 'f'.repeat(64)
    let claimed = false
    const timers = []; const routed = []; let parallelReads = 0
    const { $ } = host(async (tool) => {
      if (tool === 'work_status') return text({ ...snapshot(), side_intake_enabled: true })
      if (tool === 'work_side_status') return text({ enabled: true, side_inbox_receipts: [] })
      if (tool === 'work_parallel_status') { parallelReads++; return text({ version: 1, policy: { enabled: true, paused: false }, leases: {} }) }
      if (tool === 'work_side_claim') {
        if (claimed) return text({ claim: null, settings: { max_active: 1 } })
        claimed = true
        return text({ claim: { receipt_id: id, generation: '10000000000000000000000000000007', text: 'event', route: {}, lease_until: Date.now() / 1000 + 600,
          workload: { version: 5, truncated: false, nodes: [], queue_order: [] } }, settings: { max_active: 1 } })
      }
      return text({})
    })
    $.clock.after = (delay, callback) => { timers.push({ delay, callback }); return { cancel() {} } }
    // Route the side actor's own spawn through register.js's agent.spawn hook,
    // as Claude Code does for a plugin-origin native spawn.
    $.agent.spawn = async args => {
      const own = async input => { routed.push({ name: input.name, description: input.description }); return { agentId: 'side-fork-1' } }
      own.origin = { plugin: 'ciel-work' }
      const event = { ...args, fork: true, parentAgentId: undefined }
      const before = parallelReads
      const result = await handlers['agent.spawn']($, event, own)
      const sideReads = parallelReads - before
      const lookAlike = await handlers['agent.spawn']($, { ...event, description: event.description + ' changed' }, own)
      // Exact pending description, failed proof: the side proof deny fires.
      const exactForkFalse = await handlers['agent.spawn']($, { ...event, fork: false }, own)
      const exactGeneral = await handlers['agent.spawn']($, { ...event, subagentType: 'general-purpose' }, own)
      // Side forks are unnamed; a named spawn with the exact description is denied.
      const named = await handlers['agent.spawn']($, { ...event, name: 'side-named-probe' }, own)
      // A non-side description falls through to the normal Work policy.
      const nonSide = await handlers['agent.spawn']($, { ...event, description: 'Ordinary fork' }, own)
      return { ...result, sideReads, lookAlike, exactForkFalse, exactGeneral, named, nonSide }
    }
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    const spawn = $.agent.spawn
    let observed = null
    $.agent.spawn = async args => { observed = await spawn(args); return observed }
    await timers.find(t => t.delay === 2000).callback()
    return { routed, observed }
  },
  // User requirement 2026-11-16: every external event gets ACK/readback first,
  // then processing and a separate processing-result reply. The ACK must not
  // wait for source prefetch, main compaction or a side fork.
  async 'side-immediate-ack-before-prefetch-and-fork'() {
    const id = 'a'.repeat(64)
    const order = []
    let claimed = false
    const { $ } = host(async (tool, args) => {
      order.push(tool + (args?.state ? ':' + args.state : ''))
      if (tool === 'work_status') return text({ ...snapshot(), side_intake_enabled:true })
      if (tool === 'work_side_status') return text({ enabled:true, side_inbox_receipts:[] })
      if (tool === 'work_side_claim') {
        if (claimed) return text({ claim:null, settings:{ max_active:1 } })
        claimed = true
        return text({ claim:{ receipt_id:id, generation:'10000000000000000000000000000009', text:'{"type":"com.ciel.walkie.support.message"}',
          route:{ source_kind:'external_event' }, lease_until:Date.now() / 1000 + 600,
          source_read:{ server:'walkie', classification:'thread', conversation_id:'34886995-4a66-4527-a618-814e9cb0a5bf', root_id:'1', target_message_id:'1' },
          workload:{ version:5, truncated:false, nodes:[], queue_order:[] } }, settings:{ max_active:1 } })
      }
      if (tool === 'work_side_ack' && args.state === 'prepare')
        return text({ state:'submitting', receipt_id:id, attempt_id:'ack-attempt', server:'walkie', tool:'walkie_send_message',
          arguments:{ conversation_id:'c', client_id:'ack-client', reply_to_message_id:'1', text:'ACK — 요청을 받았습니다.\n\n복기: priority' } })
      if (tool === 'work_side_ack') return text({ receipt:{ receipt_id:id, stage:'received', external_ack:args.state } })
      if (tool === 'walkie_send_message') return text({ ok:true })
      if (tool === 'walkie_get_thread' || tool === 'walkie_read_thread' || String(tool).startsWith('walkie_'))
        throw new Error('source prefetch reached after ACK')
      return text({})
    })
    $.agent.spawn = async () => { order.push('spawn'); return { agentId:'side-ack-fork' } }
    await sidePump($)
    const ack = order.indexOf('work_side_ack:prepare')
    const sent = order.indexOf('walkie_send_message')
    const recorded = order.indexOf('work_side_ack:submitted')
    const prefetch = order.findIndex(row => row.startsWith('walkie_') && row !== 'walkie_send_message')
    return { order, ackBeforePrefetch:ack >= 0 && sent > ack && recorded > sent && (prefetch < 0 || recorded < prefetch),
      spawned:order.includes('spawn') }
  },
  // User requirement 2026-11-16: an event the side could not process is not
  // dropped; its original is appended to main once, after a claim.
  async 'side-failed-event-original-reaches-main'() {
    const id = 'b'.repeat(64)
    const original = 'Celly 원문 메시지 ' + 'z'.repeat(9000)
    const forwardText = '[Ciel side 처리 실패 · 원문 전달 · event bbbbbbbbbbbb · turn_error]\n...\n\n' + original
    const text_ = text
    const calls = []
    let offers = 1
    const { $, state } = host(async (tool, args) => {
      calls.push(tool + (args?.state ? ':' + args.state : ''))
      if (tool === 'work_side_claim') {
        const main_forwards = offers-- > 0 ? [{ receipt_id:id, text:forwardText }] : []
        return text_({ claim:null, settings:{}, handoff_replies:[], main_wakes:[], main_notices:[], main_forwards })
      }
      if (tool === 'work_side_wake' && args.state === 'notice_prepare')
        return text_({ receipt_id:id, protocol:2, state:'claimed', attempt_id:'f'.repeat(32) })
      if (tool === 'work_side_wake') return text_({ receipt_id:id, protocol:2, state:args.state.slice(7) })
      return text_({})
    })
    await sidePump($)
    await sidePump($)
    const appended = (state.appended || []).map(row => row.message.content[0].text)
    return { calls, appended:appended.length, carriesOriginal:appended.length === 1 && appended[0].includes(original),
      recorded:calls.includes('work_side_wake:notice_appended') }
  },
  // After the side model failed, the router marks the claim use_parent_model;
  // the host spawns the retry with the " main-model" description the gate reads.
  async 'side-parent-model-retry-is-marked-for-the-gate'() {
    const spawned = []
    for (const parent of [false, true]) {
      const id = (parent ? 'd' : 'e').repeat(64)
      let claimed = false
      const { $ } = host(async (tool, args) => {
        if (tool === 'work_side_claim') {
          if (claimed) return text({ claim:null, settings:{ max_active:1 } })
          claimed = true
          return text({ claim:{ receipt_id:id, generation:'1000000000000000000000000000000'+(parent ? 'a' : 'b'), text:'event', route:{},
            lease_until:Date.now() / 1000 + 600, use_parent_model:parent,
            workload:{ version:5, truncated:false, nodes:[], queue_order:[] } }, settings:{ max_active:1 } })
        }
        if (tool === 'work_side_ack') return text({ state:'not_applicable' })
        return text({})
      })
      $.agent.spawn = async args => { spawned.push(args.description); return { agentId:'side-' + spawned.length } }
      await sidePump($)
    }
    return { spawned }
  },
  // A spawn claiming the pending side identity but failing the fork proof is
  // denied before any Work policy read: parallel on, off or unavailable.
  async 'side-failed-proof-denied'() {
    const policy=process.argv[3]  // on | off | unavailable
    const id='e'.repeat(64); let claimed=false; const timers=[]; const attempts={}; let parallelReads=0
    const {$}=host(async(tool)=>{
      if(tool==='work_status') return text({ ...snapshot(), side_intake_enabled:true })
      if(tool==='work_side_status') return text({ enabled:true, side_inbox_receipts:[] })
      if(tool==='work_parallel_status'){
        parallelReads++
        if(policy==='unavailable') return {isError:true,...text('parallel ledger unavailable')}
        return text({version:1,policy:{enabled:policy==='on',paused:false},leases:{}})
      }
      if(tool==='work_side_claim'){
        if(claimed) return text({claim:null,settings:{max_active:1}})
        claimed=true
        return text({claim:{receipt_id:id,generation:'10000000000000000000000000000008',text:'event',route:{},lease_until:Date.now()/1000+600,
          workload:{version:5,truncated:false,nodes:[],queue_order:[]}},settings:{max_active:1}})
      }
      return text({})
    })
    $.clock.after=(delay,callback)=>{timers.push({delay,callback});return{cancel(){}}}
    const reached=[]
    $.agent.spawn=async args=>{
      const own=async input=>{reached.push(input.description);return {agentId:'must-not-run'}};own.origin={plugin:'ciel-work'}
      const engine=async input=>{reached.push(input.description);return {agentId:'must-not-run'}};engine.origin={plugin:'engine'}
      const before=parallelReads
      attempts.forkFalse=await handlers['agent.spawn']($,{...args,fork:false,parentAgentId:undefined},own)
      attempts.generalPurpose=await handlers['agent.spawn']($,{...args,subagentType:'general-purpose',fork:true,parentAgentId:undefined},own)
      // Same description, other name, model origin: still the side identity.
      attempts.engineLookAlike=await handlers['agent.spawn']($,{...args,name:'other-name',fork:false,parentAgentId:undefined},engine)
      // Reserved description prefix but not the pending row, otherwise a valid fork.
      attempts.prefixLookAlike=await handlers['agent.spawn']($,{...args,description:args.description+' changed',fork:true,parentAgentId:undefined},own)
      attempts.sideReads=parallelReads-before
      return {deny:'scenario'}
    }
    await handlers['session.start']($,{isInteractive:false},next(undefined))
    await timers.find(t=>t.delay===2000).callback()
    // Reserved side namespace with no pending row (after the event ended).
    const engine=async input=>{reached.push(input.description);return {agentId:'must-not-run'}};engine.origin={plugin:'engine'}
    attempts.reservedName=await handlers['agent.spawn']($,{name:'side-intake-0123456789abcdef',description:'x',subagentType:'fork',fork:true},engine)
    const unrelatedNext=async e=>({passed:e.name});unrelatedNext.origin={plugin:'engine'}
    const unrelated=await handlers['agent.spawn']($,{name:'ordinary-fork',description:'ordinary',subagentType:'fork',fork:true},unrelatedNext)
    return {attempts,reached,unrelated}
  },
  async 'side-time-limit-handoff-bounded'() {
    const handoff={event_id:'limit-1',item_id:'n9',kind:'time_limit',summary:'Deployment window closes',question:'Can this finish?',
      time_limit:{text:'Complete before maintenance closes',due_at:'2026-11-11T03:00:00Z',evidence:'change ticket CHG-42'},
      waits:[{id:'n9',waiting_on:'maintenance window'}],raw_thread:'SECRET-THREAD',sse_event:'SECRET-SSE',
      wake:{state:'pending',revision:0,delivery_uncertain:false}}
    // New-router row: Stop presents it only through its own started election.
    const {$}=host(async(tool,args)=>{
      if (tool==='work_side_wake') {
        handoff.wake={state:'submitting',phase:args.state==='start'?'started':'intent',revision:args.state==='start'?2:1,
          delivery_uncertain:args.state==='start'}
        return text({protocol:2,state:'submitting',phase:handoff.wake.phase,attempt_id:'e'.repeat(32),
          owner_nonce:args.owner_nonce,channel:args.channel,revision:handoff.wake.revision})
      }
      if (tool==='work_parallel_status') return text({version:1,policy:{enabled:false},leases:{}})
      return text({...snapshot(),auto_continue:false,side_handoffs:[handoff]})
    })
    const promptNext=async e=>e
    promptNext.origin={plugin:'engine'}
    const prompt=await handlers['prompt.submit']($,{origin:{kind:'composer'},text:'continue',context:[]},promptNext)
    await mainTurn($,'T')
    const stop=await handlers['classic.Stop']($,{session_id:'S1'},next({}))
    return {context:(prompt.context||[]).join('\n'),block:stop?.block||''}
  },
  async 'side-notification-isolation'() {
    const { $, state } = host(async tool => tool === 'work_status'
      ? text({ ...snapshot(), side_intake_enabled: true })
      : tool === 'work_side_status' ? text({ enabled: true, side_inbox_receipts: [] }) : text({}))
    state.agents = [{ id:'owned-side', spawnedBy:'ciel-work', type:'fork', status:'completed',
      name:'side-intake-abcdef1234567890', description:'Side SSE abcdef123456' }]
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
        return text({claim:{receipt_id:'mail1',generation:'10000000000000000000000000000009',text:'original request',route:{},workload:{version:5,truncated:false,nodes:[],queue_order:[]}}})
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
  // Native fork evidence: the host reports only this session's bound Work
  // forks seen in $.agent.list(). Main is not in the list; a side actor and an
  // unrelated agent are never recorded; nothing reaches main's context.
  async 'parallel-native-observation'() {
    const leases = {
      L1: { id: 'L1', item_id: 'n1', priority: 'P1', role: 'worker', state: 'running', slot: true, host_id: 'fork-a', agent_name: 'work-a' },
      L2: { id: 'L2', item_id: 'n2', priority: 'P2', role: 'worker', state: 'running', slot: true, host_id: 'fork-b', agent_name: 'work-b' },
      L3: { id: 'L3', item_id: 'n3', priority: 'P2', role: 'worker', state: 'reserved', slot: true, host_id: null },
      L4: { id: 'L4', item_id: 'n4', priority: 'P3', role: 'worker', state: 'done', slot: false, host_id: 'fork-old' },
    }
    const parallel = { version: 20, active_device: 3,
      policy: { enabled: true, paused: false, priority_limits: { P1: 5, P2: 3, P3: 1, P4: 1, P5: 1 }, launches_per_minute: 4, max_rework: 2 }, leases,
      forks: { schema: 'native-fork-evidence/1', available: true, evidence_version: 7, confirmed_count: 0,
        confirmed: [], waiting: [], reservation: [], uncertain: [], validating: [], ended: [] } }
    const observations = []
    const appended = []
    const { $, state } = host(async (tool, args) => {
      if (tool === 'work_parallel_status') return text(parallel)
      if (tool === 'work_parallel_event' && args.event === 'native_observation') {
        observations.push(args)
        return text({ evidence_version: observations.length, changed: args.rows.map(r => r.lease_id) })
      }
      return text(snapshot())
    })
    $.session.append = async row => { appended.push(row) }
    state.agents = [
      { id: 'fork-a', name: 'work-a', type: 'fork', status: 'running', spawnedBy: 'ciel-work', description: 'Work n1 SECRET-PROMPT' },
      { id: 'fork-b', name: 'work-b', type: 'fork', status: 'pending', spawnedBy: 'ciel-work', description: 'Work n2' },
      { id: 'side-1', name: 'side-intake-abc', type: 'ciel-work:side-intake', status: 'running', spawnedBy: 'ciel-work' },
      { id: 'other', name: 'helper', type: 'Explore', status: 'running' },
      { id: 'fork-old', name: 'work-old', type: 'fork', status: 'completed', spawnedBy: 'ciel-work' },
    ]
    const shown = async () => (await handlers['command.run']($, { args: 'parallel' })).text || ''
    // A projection carrying evidence groups renders FORKS from them only.
    parallel.forks = { schema: 'native-fork-evidence/1', available: true, evidence_version: 8, confirmed_count: 2,
      confirmed: [{ item_id: 'n1', item_title: 'Fix A', priority: 'P1', role: 'worker', state: 'running', slot: true, agent_name: 'work-a', native: { status: 'running' } },
                  { item_id: 'n2', item_title: 'Fix B', priority: 'P2', role: 'worker', state: 'running', slot: true, agent_name: 'work-b', native: { status: 'pending' } }],
      waiting: [], reservation: [{ item_id: 'n3', priority: 'P2', role: 'worker', state: 'reserved', slot: true, native: null }],
      uncertain: [], validating: [], ended: [] }
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    const view = await shown()
    return { observations, appended, logs: state.logs, view }
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
  // Main reviews a submitted worker result. The host never reserves or spawns a
  // reviewer fork; it wakes main once with an allow-listed bounded receipt.
  async 'parallel-submitted-wakes-main-without-reviewer'() {
    const lease = { id:'L1', item_id:'n2', priority:'P1', role:'worker', state:'submitted', host_id:'agent-old', slot:false,
      plan_version:7, artifact:'a'.repeat(64) }
    // Backend row (work_parallel_finalization.projection) plus fields a fork
    // could have produced; only the allow-listed ones may reach main.
    const review = { lease_id:'L1', item_id:'n2', root_id:'n1', priority:'P1', state:'pending', decision:null,
      decision_id:null, evidence:null, replacement_lease_id:null, replacement_generation:null,
      stages:{accepted:false,workspace:false,board:false,lease:false,archive:false},
      item_title:'Fix retry', item_path:['Goal','Fix retry'], artifact:'a'.repeat(64), completion_receipt:'d'.repeat(64),
      fingerprint:'f'.repeat(64), summary:'retry fixed', coverage:[{ item_id:'n2', verified:true }], verification:'unit tests 12 passed',
      workspace:{ repo:'C:/w', path:'C:/w/wt-n2', branch:'work/n2', head:'abc123', dirty:false, changed_files:['src/retry.py'],
        diff_stat:'1 file changed', snapshot_digest:'e'.repeat(64), receipt_digest:'c'.repeat(64) },
      answer:'SECRET-FORK-ANSWER', transcript:'SECRET-TRANSCRIPT', agent_id:'SECRET-AGENT', native:{ status:'SECRET-NATIVE' } }
    const events=[]; const submitted=[]; const appended=[]; const timers=[]
    const { $, state } = host(async (tool,args) => {
      if (tool === 'work_status') return text(snapshot())
      if (tool === 'work_parallel_status') return text({version:9,active_device:0,
        policy:{enabled:true,paused:false,priority_limits:{P1:2,P2:1,P3:1,P4:1,P5:1}},leases:{L1:lease},
        review:{version:4,pending:[review],truncated:false}})
      if (tool === 'work_parallel_event') {
        events.push(args.event + (args.role ? ':' + args.role : ''))
        if (args.event === 'reserve_next') return {isError:true,...text('no eligible candidate')}
        return text({})
      }
      return text({})
    })
    $.session.repo=async()=>null; $.session.root=async()=>'/repo'
    $.clock.after=(delay)=>{timers.push(delay);return {cancel(){}}}
    $.agent.spawn=async args=>{(state.spawned ||= []).push(args);return {agentId:'must-not-start'}}
    $.prompt={submit:async args=>{submitted.push(args.text);return {}}}
    $.session.append=async args=>{appended.push(args);return {}}
    await handlers['session.start']($,{isInteractive:false},next(undefined))
    await handlers['turn.complete']($,{},next(undefined))
    await handlers['turn.complete']($,{},next(undefined))
    await sleep(10)
    // This row has no durable wake (older projection), so only lifecycle dedup
    // guarded it, and session.start clears that (noticedParallelReviews). The
    // review's own terminal prompt record now holds across it.
    const beforeRestart = submitted.length
    if (process.argv[3] === 'session-restart') {
      await handlers['session.start']($,{isInteractive:false},next(undefined))
      await handlers['turn.complete']($,{},next(undefined))
      await sleep(30)
    }
    return {spawned:state.spawned || [], events, submitted, appended, timers, beforeRestart}
  },
  async 'parallel-review-busy-main-appends'() {
    const review = { lease_id:'L2', item_id:'n3', state:'pending', decision:null, summary:'done', verification:'ok' }
    let reviews=[]
    const submitted=[]; const appended=[]
    const { $ } = host(async (tool,args) => {
      if (tool === 'work_status') return text(snapshot())
      if (tool === 'work_parallel_status') return text({version:9,active_device:0,
        policy:{enabled:true,paused:false,priority_limits:{P1:1,P2:0,P3:0,P4:0,P5:0}},leases:{},review:{version:2,pending:reviews}})
      if (tool === 'work_parallel_event' && args.event === 'reserve_next') return {isError:true,...text('no eligible candidate')}
      return text({})
    })
    $.session.repo=async()=>null; $.session.root=async()=>'/repo'
    $.prompt={submit:async args=>{submitted.push(args.text);return {}}}
    $.session.append=async args=>{appended.push(args.message.content[0].text);return {}}
    await handlers['session.start']($,{isInteractive:false},next(undefined))
    await handlers['turn.start']($,{},next(undefined))
    // The worker's durable report is the event that wakes main.
    reviews=[review]
    await handlers['tool.call']($,{tool:'mcp__ciel-runtime-router__work_parallel_report',agentId:'worker'},next({ok:true}))
    await sleep(20)
    return {submitted, appended}
  },
  async 'parallel-review-durable-wake'() {
    const mode=process.argv[3]
    let wakeState=mode==='failed'?'failed':mode.startsWith('restart-')?mode.slice(8):'pending'
    const review={lease_id:'LW',item_id:'nw',state:'pending',decision:null,summary:'wake me',verification:'ok',
      wake:{state:wakeState,attempts:wakeState==='pending'?0:1}}
    const calls=[]; const prompts=[]; const appended=[]
    const {$}=host(async(tool,args)=>{
      if(tool==='work_status') return text({...snapshot(),auto_continue:false})
      if(tool==='work_parallel_status') return text({version:9,active_device:0,
        policy:{enabled:true,paused:false,priority_limits:{P1:1,P2:0,P3:0,P4:0,P5:0}},leases:{},review:{version:4,pending:[review]}})
      if(tool==='work_parallel_event'){
        calls.push({...args})
        if(args.event==='review_wake'&&args.operation==='prepare'){
          if(!['pending','failed'].includes(wakeState)) return {isError:true,...text('review wake already submitted')}
          wakeState='submitting';review.wake={state:wakeState,attempts:(review.wake.attempts||0)+1}
          return text({lease_id:'LW',state:'submitting',attempt_id:'attempt-1',attempts:review.wake.attempts,finalization_version:5})
        }
        if(args.event==='review_wake'&&args.operation==='record'){
          wakeState=args.state;review.wake={state:wakeState,attempts:review.wake.attempts};return text({state:wakeState})
        }
        if(args.event==='reserve_next') return {isError:true,...text('no eligible candidate')}
        return text({})
      }
      return text({})
    })
    $.session.repo=async()=>null;$.session.root=async()=>'/repo'
    $.prompt={submit:async args=>{prompts.push(args.text);return {}}}
    $.session.append=async args=>{appended.push(args.message.content[0].text);return {}}
    await handlers['session.start']($,{isInteractive:mode==='idle'||mode==='failed'||mode.startsWith('restart-')},next(undefined))
    if(mode==='busy'){
      await handlers['turn.start']($,{},next(undefined))
      await handlers['tool.call']($,{tool:'mcp__ciel-runtime-router__work_parallel_report',agentId:'worker'},next({ok:true}))
    }
    await sleep(20)
    if(mode.startsWith('restart-')){
      await handlers['session.start']($,{isInteractive:true},next(undefined));await sleep(20)
    }
    return {calls,prompts,appended,wakeState}
  },
  // The pump rolls a stored decision forward only for awaiting_capacity and a
  // decided saga with a blocked stage; never for an undecided or ready row.
  async 'parallel-roll-forward-selection'() {
    const rows=[
      {lease_id:'LA',item_id:'na',item_title:'Waiting',priority:'P1',state:'awaiting_capacity',decision:'rework',blocked:{code:'paused',message:'dispatch paused'}},
      {lease_id:'LB',item_id:'nb',item_title:'Stuck',priority:'P1',state:'deciding',decision:'approve',blocked:{code:'stage_failed',message:'board busy'}},
      {lease_id:'LC',item_id:'nc',item_title:'Undecided',priority:'P1',state:'pending',decision:null,wake:{state:'queued',attempts:1}},
      {lease_id:'LD',item_id:'nd',item_title:'Ready',priority:'P1',state:'rework_ready',decision:'rework',blocked:null,launch:{state:'bound',attempt:1}},
      {lease_id:'LE',item_id:'ne',item_title:'Running',priority:'P1',state:'deciding',decision:'approve',blocked:null}]
    const rolled=[]
    const {$}=host(async(tool,args)=>{
      if(tool==='work_status') return text(snapshot())
      if(tool==='work_parallel_status') return text({version:9,active_device:0,policy:{enabled:true,paused:false,priority_limits:{P1:1}},
        leases:{},review:{version:4,pending:rows}})
      if(tool==='work_parallel_event'){
        if(args.event==='review_roll_forward'){rolled.push(args.lease_id);return text({decision:'x',lease_id:args.lease_id,state:'awaiting_capacity'})}
        if(args.event==='reserve_next') return {isError:true,...text('no eligible candidate')}
        return text({})
      }
      return text({})
    })
    $.session.repo=async()=>null;$.session.root=async()=>'/repo'
    $.prompt={submit:async()=>({})};$.session.append=async()=>({})
    await handlers['session.start']($,{isInteractive:true},next(undefined))
    const shown=await handlers['command.run']($,{args:'parallel'})
    return {rolled,shown:shown.text||''}
  },
  async 'parallel-review-stop-gate'() {
    let reviews=[{ lease_id:'L3', item_id:'n4', state:'pending', decision:null, item_title:'Ship it', summary:'built',
      verification:'tests ok', transcript:'SECRET-TRANSCRIPT' }]
    let version=5
    const { $, state } = host(async (tool,args) => {
      if (tool === 'work_status') return text({...snapshot(),auto_continue:false})
      if (tool === 'work_parallel_status') return text({version:9,active_device:0,
        policy:{enabled:true,paused:false,priority_limits:{P1:1,P2:0,P3:0,P4:0,P5:0}},leases:{},review:{version,pending:reviews}})
      if (tool === 'work_parallel_event' && args.event === 'reserve_next') return {isError:true,...text('no eligible candidate')}
      if (tool === 'work_parallel_item_review') { state.decision=args
        return text({decision:'approve',lease_id:'L3',item_id:'n4',state:'finalized',version:10,finalization_version:6}) }
      return text({})
    })
    $.session.repo=async()=>null; $.session.root=async()=>'/repo'
    $.prompt={submit:async()=>({})}
    $.session.append=async()=>({})
    const stop = () => handlers['classic.Stop']($,{session_id:'S1'},next({}))
    const first = await stop()
    const second = await stop()
    const third = await stop()
    const decided = await handlers['tool.call']($,{tool:'mcp__ciel-runtime-router__work_parallel_item_review',
      tool_use_id:'t1', lease_id:'L3', decision:'approve', evidence:'tests ok and diff matches scope'},next({ran:true}))
    reviews=[{ lease_id:'L3', item_id:'n4', state:'deciding', decision:'approve', summary:'built' }]; version=6
    const after = await stop()
    return {first, second, third, after, decided, decision:state.decision, logs:state.logs}
  },
  async 'parallel-owned-fork-notifications-suppressed'() {
    const parallel = { version: 11, active_device: 0,
      policy: { enabled: true, paused: false, priority_limits: { P1: 5, P2: 3, P3: 1, P4: 1, P5: 1 } }, leases: {} }
    const { $ } = host(async (tool, args) => {
      if (tool === 'work_parallel_status') return text(parallel)
      if (tool === 'work_parallel_event') {
        if (args.event === 'reserve') return text({ lease_id: 'L9', version: 12, plan_version: 5 })
        return text({ version: 13, leases: {} })
      }
      return text(snapshot())
    })
    $.session.repo = async () => null
    $.session.root = async () => '/repo'
    $.agent.spawn = async () => ({ agentId: 'host-9' })
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    await handlers['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_run',
      item_id: 'n227', priority: 'P1', role: 'worker', expected_version: 11 }, next({}))
    const note = id => '<task-notification><task-id>' + id + '</task-id><status>completed</status><result>SECRET-FORK-ANSWER</result></task-notification>'
    const busy = await handlers['session.receive']($, { origin:{kind:'task-notification'}, text:note('host-9') }, next({text:'leaked'}))
    const idle = await handlers['prompt.submit']($, { origin:{kind:'task-notification'}, text:note('host-9'), context:[] }, next({text:'leaked'}))
    const foreign = await handlers['session.receive']($, { origin:{kind:'task-notification'}, text:note('foreign') }, next({text:'passed'}))
    const typed = await handlers['prompt.submit']($, { origin:{kind:'composer'}, text:note('host-9'), context:[] }, next({text:'typed'}))
    // A later lifecycle still recognises the fork it no longer holds a lease for.
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    const late = await handlers['session.receive']($, { origin:{kind:'task-notification'}, text:note('host-9') }, next({text:'leaked'}))
    return { busy, idle, foreign, typed, late }
  },
  async 'parallel-reviewer-run-denied'() {
    const events=[]
    const { $ } = host(async (tool, args) => {
      if (tool === 'work_parallel_status') return text({ version: 11, active_device: 0,
        policy: { enabled: true, paused: false, priority_limits: { P1: 5 } }, leases: {} })
      if (tool === 'work_parallel_event') { events.push(args.event); return text({}) }
      return text(snapshot())
    })
    const run = await handlers['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_run',
      item_id: 'n227', priority: 'P1', role: 'reviewer' }, next({}))
    return { run: verdict(run), events }
  },
  async 'parallel-rework-dispatch-started'() {
    const replacement={id:'R2',item_id:'n2',priority:'P1',role:'resume',state:'reserved',slot:true,host_id:null,plan_version:7,
      workspace:{path:'C:/w/rework',branch:'work/n2',head:'abc'}}
    const review={lease_id:'L1',item_id:'n2',priority:'P1',state:'rework_ready',decision:'rework',evidence:'add jitter test',
      replacement_lease_id:'R2',replacement_generation:2,workspace:replacement.workspace}
    const calls=[]; const spawned=[]; let bound=false
    const {$}=host(async(tool,args)=>{
      if(tool==='work_status') return text(snapshot())
      if(tool==='work_parallel_status') return text({version:9,active_device:1,
        policy:{enabled:true,paused:false,priority_limits:{P1:2,P2:0,P3:0,P4:0,P5:0}},
        leases:{R2:{...replacement,...(bound?{state:'running',host_id:'agent-rework'}:{})}},review:{version:4,pending:[review]}})
      if(tool==='work_parallel_event'){
        calls.push(args)
        if(args.event==='rework_launch_status') return text({finalization_version:4,launches:[{review_lease_id:'L1',lease_id:'R2',item_id:'n2',generation:2,dispatch_name:null,state:'none',attempt:0,manual_requested:false,manual_from:null,intent_nonce_present:false,same_process:null,observed_agent_id:null}]})
        if(args.event==='rework_dispatch') return text({lease_id:'R2',worker_token:'b'.repeat(64),plan_version:7,
          item_id:'n2',priority:'P1',role:'resume',generation:2,workspace:replacement.workspace})
        if(args.event==='rework_launch_intent') return text({state:'launch_intent'})
        if(args.event==='rework_spawn_observed') return text({state:'spawn_observed'})
        if(args.event==='bind'){bound=true;return text({})}
        if(args.event==='reserve_next') return {isError:true,...text('no eligible candidate')}
        return text({})
      }
      return text({})
    })
    $.session.repo=async()=>null; $.session.root=async()=>'/repo'
    $.agent.spawn=async args=>{spawned.push(args);return {agentId:'agent-rework'}}
    await handlers['session.start']($,{isInteractive:true},next(undefined))
    return {calls,spawned}
  },
  async 'parallel-rework-spawn-denied-retains-reservation'() {
    const replacement={id:'R2',item_id:'n2',priority:'P1',role:'resume',state:'reserved',slot:true,host_id:null,plan_version:7,
      workspace:{path:'C:/w/rework',branch:'work/n2',head:'abc'}}
    const review={lease_id:'L1',item_id:'n2',priority:'P1',state:'rework_ready',decision:'rework',evidence:'fix',
      replacement_lease_id:'R2',replacement_generation:2,workspace:replacement.workspace}
    const calls=[]
    const {$}=host(async(tool,args)=>{
      if(tool==='work_status') return text(snapshot())
      if(tool==='work_parallel_status') return text({version:9,active_device:1,
        policy:{enabled:true,paused:false,priority_limits:{P1:2,P2:0,P3:0,P4:0,P5:0}},leases:{R2:replacement},review:{version:4,pending:[review]}})
      if(tool==='work_parallel_event'){
        calls.push(args.event)
        if(args.event==='rework_launch_status') return text({finalization_version:4,launches:[{review_lease_id:'L1',lease_id:'R2',item_id:'n2',generation:2,dispatch_name:null,state:'none',attempt:0,manual_requested:false,manual_from:null,intent_nonce_present:false,same_process:null,observed_agent_id:null}]})
        if(args.event==='rework_dispatch') return text({lease_id:'R2',worker_token:'b'.repeat(64),plan_version:7,
          item_id:'n2',priority:'P1',role:'resume',generation:2,workspace:replacement.workspace})
        if(args.event==='rework_launch_intent') return text({state:'launch_intent'})
        if(args.event==='rework_launch_denied') return text({state:'spawn_denied'})
        if(args.event==='reserve_next') return {isError:true,...text('no eligible candidate')}
        return text({})
      }
      return text({})
    })
    $.session.repo=async()=>null; $.session.root=async()=>'/repo'
    $.agent.spawn=async()=>({deny:'native denied'})
    await handlers['session.start']($,{isInteractive:true},next(undefined))
    return {calls}
  },
  // Durable rework launch protocol. `bind` is scripted per case; agents is the
  // native $.agent.list(); dispatchName models the lease's durable launch name.
  async 'parallel-rework-launch'() {
    const mode = process.argv[3]
    const replacement = { id:'R2', item_id:'n2', priority:'P1', role:'resume', state:'reserved', slot:true, host_id:null, plan_version:7,
      workspace:{ path:'C:/w/rework', branch:'work/n2', head:'abc' } }
    const review = { lease_id:'L1', item_id:'n2', priority:'P1', state:'rework_ready', decision:'rework', evidence:'fix',
      replacement_lease_id:'R2', replacement_generation:2, workspace:replacement.workspace,
      launch:{state:['restart-row','restart-no-row','same-no-row','restart-no-row-nonce-never'].includes(mode)?'launch_intent':'none',
        attempt:['restart-row','restart-no-row','same-no-row','restart-no-row-nonce-never'].includes(mode)?1:0} }
    const calls=[]; const spawned=[]; let binds=0
    const agents = mode === 'restart-row' ? [{ id:'agent-earlier', name:'work-rework-R2-a1', status:'running' }] : []
    const {$,state}=host(async(tool,args)=>{
      if(tool==='work_status') return text(snapshot())
      if(tool==='work_parallel_status') return text({version:9,active_device:1,
        policy:{enabled:true,paused:false,priority_limits:{P1:2,P2:0,P3:0,P4:0,P5:0}},leases:{R2:replacement},review:{version:4,pending:[review]}})
      if(tool==='work_parallel_event'){
        calls.push(args.event)
        if(args.event==='rework_launch_status') return text({finalization_version:4,launches:[{review_lease_id:'L1',lease_id:'R2',item_id:'n2',generation:2,
          dispatch_name:review.launch.dispatch_name || (review.launch.state==='launch_intent'?'work-rework-R2-a1':null),state:review.launch.state,attempt:review.launch.attempt,
          manual_requested:false,manual_from:null,intent_nonce_present:review.launch.state!=='none',same_process:mode.startsWith('restart')?false:true,
          observed_agent_id:review.launch.agent_id || null}]})
        if(args.event==='rework_dispatch') return text({lease_id:'R2',worker_token:'b'.repeat(64),plan_version:7,
          item_id:'n2',priority:'P1',role:'resume',generation:2,workspace:replacement.workspace})
        if(args.event==='rework_launch_intent'){review.launch={state:'launch_intent',attempt:(review.launch.attempt||0)+1,dispatch_name:args.dispatch_name};return text({...review.launch})}
        if(args.event==='rework_launch_denied'){review.launch={state:'spawn_denied',attempt:review.launch.attempt,dispatch_name:args.dispatch_name};return text({...review.launch})}
        if(args.event==='rework_spawn_observed'){review.launch={state:'spawn_observed',attempt:review.launch.attempt,dispatch_name:args.dispatch_name,agent_id:args.agent_id};return text({...review.launch})}
        if(args.event==='rework_launch_absent'){review.launch={state:'spawn_denied',attempt:review.launch.attempt,dispatch_name:args.dispatch_name};return text({...review.launch})}
        if(args.event==='bind'){
          binds++
          // bind-lost: the first bind commits but its response is lost.
          if(mode==='bind-lost' && binds===1){ replacement.host_id=args._host?.host_id || 'agent-rework-1'; throw new Error('router connection reset') }
          if(mode==='bind-throw' && binds===1) return {isError:true,...text('ledger busy; reread')}
          replacement.host_id=args._host?.host_id; replacement.state='running'; return text({})
        }
        if(args.event==='reserve_next') return {isError:true,...text('no eligible candidate')}
        return text({})
      }
      return text({})
    })
    state.agents = agents
    // Fail closed when the process nonce is unavailable: set throws, or a write
    // never reads back.
    if (mode === 'nonce-set-throws') state.envFault = 'set-throws'
    if (mode === 'restart-no-row-nonce-never') state.envFault = 'never'
    $.session.repo=async()=>null; $.session.root=async()=>'/repo'
    $.agent.spawn=async args=>{
      spawned.push(args.name)
      if(mode==='deny-once' && spawned.length===1) return {deny:'native launch refused'}
      const row={id:'agent-rework-'+spawned.length,name:args.name,status:'running'}
      agents.push(row)
      return {agentId:row.id}
    }
    await handlers['session.start']($,{isInteractive:true},next(undefined))
    await handlers['turn.complete']($,{},next(undefined))
    await handlers['turn.complete']($,{},next(undefined))
    // The host tracks the bound fork: its task notification is owned, never main's.
    const tracked = replacement.host_id ? await handlers['session.receive']($, { origin:{kind:'task-notification'},
      text:'<task-notification><task-id>'+replacement.host_id+'</task-id></task-notification>' }, next({text:'leaked'})) : null
    const shown = mode === 'same-no-row' ? (await handlers['command.run']($, { args:'parallel' })).text || '' : ''
    // Read from the router args: a nonce counts only if the router received one.
    return {calls,spawned,binds,hostId:replacement.host_id,tracked,shown,
      nonceSent:state.routerNonces.some(row => typeof row.nonce === 'string' && row.nonce.length > 0),
      routerNonceEvents:state.routerNonces.map(row => row.event)}
  },
  // Function-level: the exact bytes of reworkLaunchNonce, from this run's
  // register.js, evaluated as one module instance and called directly by
  // `callers` concurrent first callers under the forced env interleave.
  async 'nonce-concurrent-function'() {
    const callers = Number(process.argv[3])
    const fault = process.argv[4] || null
    const { readFileSync } = await import('node:fs')
    const source = readFileSync(new URL('./register.js', import.meta.url), 'utf8')
    const from = source.indexOf('const LAUNCH_NONCE_PATTERN')
    const fn = source.indexOf('async function reworkLaunchNonce($) {', from)
    const to = source.indexOf('\n}\n', fn)
    if (from < 0 || fn < 0 || to < 0) throw new Error('reworkLaunchNonce not found in register.js')
    const reworkLaunchNonce = new Function(source.slice(from, to + 3) + '\nreturn reworkLaunchNonce')()
    const { $, state } = host(async () => text({}))
    state.nonceRace = nonceRace(callers)
    state.envFault = fault
    const values = await Promise.all(Array.from({ length: callers }, () => reworkLaunchNonce($)))
    // A later call after settle re-reads the env; nothing is cached in module state.
    const after = await reworkLaunchNonce($)
    const envValue = state.env.get('CIEL_WORK_HOST_PROCESS_NONCE') ?? null
    // Only counts and equality leave the harness, never a raw value.
    return { callers, concurrentGets: state.nonceRace.concurrentGets, log: state.nonceRace.log,
      distinct: new Set(values).size, nulls: values.filter(v => v === null).length,
      allEqualEnv: envValue !== null && values.every(v => v === envValue),
      afterEqualsEnv: envValue !== null ? after === envValue : after === null,
      envSet: envValue !== null, sets: state.nonceRace.log.filter(op => op === 'set').length }
  },
  // Function-level, ported from review-restart-terminal's probe_nonce_reorder.mjs:
  // the exact reworkLaunchNonce bytes against a fake env whose get answers with
  // the value at CALL time and is delivered only when the script releases it.
  // `reordered` delivers caller B's first get (issued at the start, so it saw
  // the env unset) only after caller A's set and read-back. Whether a real host
  // ever delivers in that order is not measured.
  async 'nonce-reorder-function'() {
    const order = process.argv[3]
    const { readFileSync } = await import('node:fs')
    const source = readFileSync(new URL('./register.js', import.meta.url), 'utf8')
    const from = source.indexOf('const LAUNCH_NONCE_PATTERN')
    const fn = source.indexOf('async function reworkLaunchNonce($) {', from)
    const to = source.indexOf('\n}\n', fn)
    if (from < 0 || fn < 0 || to < 0) throw new Error('reworkLaunchNonce not found in register.js')
    const reworkLaunchNonce = new Function(source.slice(from, to + 3) + '\nreturn reworkLaunchNonce')()
    const scripts = {
      fifo: [['A', 'get'], ['B', 'get'], ['A', 'set'], ['A', 'get']],
      reordered: [['A', 'get'], ['A', 'set'], ['A', 'get'], ['B', 'get']],
    }
    if (!scripts[order]) throw new Error('order must be fifo or reordered')
    const NONCE = 'CIEL_WORK_HOST_PROCESS_NONCE'
    const env = new Map()
    const pending = []
    const caller = who => ({ env: {
      get: key => { const seen = env.get(key); return new Promise(go => pending.push({ who, op: 'get', go: () => go(seen) })) },
      set: (key, value) => new Promise(go => pending.push({ who, op: 'set', go: () => { env.set(key, value); go({}) } })),
    } })
    const tick = async (n = 6) => { for (let i = 0; i < n; i++) await new Promise(go => setImmediate(go)) }
    const results = {}
    const runs = ['A', 'B'].map(who => reworkLaunchNonce(caller(who)).then(value => { results[who] = value }))
    const log = []
    for (const [who, op] of scripts[order]) {
      await tick()
      const index = pending.findIndex(row => row.who === who && row.op === op)
      if (index < 0) { log.push('missing ' + who + ':' + op); continue }
      const [row] = pending.splice(index, 1); log.push(who + ':' + op); row.go()
    }
    let settled = false
    void Promise.all(runs).then(() => { settled = true })
    for (let k = 0; k < 200 && !settled; k++) {
      await tick()
      const row = pending.shift()
      if (row) { log.push(row.who + ':' + row.op + '*'); row.go() }
    }
    await Promise.all(runs)
    const envValue = env.get(NONCE) ?? null
    // Only equality, counts and the delivery log leave the harness, never a raw value.
    return { order, log, distinct: new Set([results.A, results.B]).size,
      single: envValue !== null && results.A === envValue && results.B === envValue,
      sets: log.filter(entry => entry.endsWith(':set') || entry.endsWith(':set*')).length }
  },
  // A fixture-forced dispatch: this host runs session.start's hydrate and a
  // turn.complete's rework pump concurrently in one module instance, and both
  // read the launch status for one unbound resume lease before any nonce
  // exists. Whether Claude Code dispatches the two hooks concurrently is not measured.
  async 'parallel-rework-nonce-race'() {
    const fault = process.argv[3] || null
    const replacement = { id:'R2', item_id:'n2', priority:'P1', role:'resume', state:'reserved', slot:true, host_id:null, plan_version:7,
      workspace:{ path:'C:/w/rework', branch:'work/n2', head:'abc' } }
    const review = { lease_id:'L1', item_id:'n2', priority:'P1', state:'rework_ready', decision:'rework', evidence:'fix',
      replacement_lease_id:'R2', replacement_generation:2, workspace:replacement.workspace,
      launch:{ state:'launch_intent', attempt:1, dispatch_name:'work-rework-R2-a1' } }
    const { $, state } = host(async (tool, args) => {
      if (tool === 'work_status') return text(snapshot())
      if (tool === 'work_parallel_status') return text({ version:9, active_device:1,
        policy:{ enabled:true, paused:false, priority_limits:{ P1:2, P2:0, P3:0, P4:0, P5:0 } }, leases:{ R2:replacement },
        review:{ version:4, pending:[review] } })
      if (tool === 'work_parallel_event') {
        if (args.event === 'rework_launch_status') return text({ finalization_version:4, launches:[{ review_lease_id:'L1', lease_id:'R2',
          item_id:'n2', generation:2, dispatch_name:'work-rework-R2-a1', state:'launch_intent', attempt:1, manual_requested:false,
          manual_from:null, intent_nonce_present:true, same_process:true, observed_agent_id:null }] })
        if (args.event === 'reserve_next') return { isError:true, ...text('no eligible candidate') }
        return text({})
      }
      return text({})
    })
    state.agents = []
    state.nonceRace = nonceRace(2)
    state.envFault = fault
    $.session.repo = async () => null; $.session.root = async () => '/repo'
    $.agent.spawn = async () => ({ deny:'fixture: no spawn in this scenario' })
    await Promise.all([handlers['session.start']($, { isInteractive:true }, next(undefined)),
      handlers['turn.complete']($, {}, next(undefined))])
    await sleep(50)
    const envValue = state.env.get('CIEL_WORK_HOST_PROCESS_NONCE') ?? null
    const sent = state.routerNonces.filter(row => row.event === 'rework_launch_status')
    return { concurrentGets: state.nonceRace.concurrentGets, log: state.nonceRace.log,
      statusWithNonce: sent.length, distinct: new Set(sent.map(row => row.nonce)).size,
      allEqualEnv: envValue !== null && sent.every(row => row.nonce === envValue), envSet: envValue !== null,
      sets: state.nonceRace.log.filter(op => op === 'set').length }
  },
  async 'parallel-fill-beyond-twenty'() {
    let reserve=0; const spawned=[]
    const {$}=host(async(tool,args)=>{
      if(tool==='work_status') return text(snapshot())
      if(tool==='work_parallel_status') return text({version:9,active_device:0,
        policy:{enabled:true,paused:false,priority_limits:{P1:80,P2:0,P3:0,P4:0,P5:0}},leases:{}})
      if(tool==='work_parallel_event'){
        if(args.event==='reserve_next'){
          reserve++
          if(reserve>25) return {isError:true,...text('no eligible candidate')}
          return text({lease_id:'L'+reserve,plan_version:7,item_id:'n'+reserve,priority:'P1',worker_token:'d'.repeat(64)})
        }
        if(args.event==='bind') return text({})
        return text({})
      }
      return text({})
    })
    $.session.repo=async()=>null; $.session.root=async()=>'/repo'
    $.agent.spawn=async args=>{spawned.push(args);return {agentId:'agent-'+spawned.length}}
    await handlers['session.start']($,{isInteractive:true},next(undefined))
    return {spawned:spawned.length,reserve}
  },
  async 'parallel-report-refills-released-slot'() {
    let reserve=0; const spawned=[]
    const {$}=host(async(tool,args)=>{
      if(tool==='work_status') return text(snapshot())
      if(tool==='work_parallel_status') return text({version:9,active_device:0,
        policy:{enabled:true,paused:false,priority_limits:{P1:1,P2:0,P3:0,P4:0,P5:0}},leases:{}})
      if(tool==='work_parallel_event'){
        if(args.event==='reserve_next'){
          if(++reserve>1) return {isError:true,...text('no eligible candidate')}
          return text({lease_id:'L1',plan_version:7,item_id:'n2',priority:'P1',worker_token:'e'.repeat(64)})
        }
        if(args.event==='bind') return text({})
        return text({})
      }
      return text({})
    })
    $.session.repo=async()=>null; $.session.root=async()=>'/repo'
    $.agent.spawn=async args=>{spawned.push(args);return {agentId:'agent-new'}}
    await handlers['session.start']($,{isInteractive:false},next(undefined))
    await handlers['tool.call']($,{tool:'mcp__ciel-runtime-router__work_parallel_report',agentId:'worker'},next({ok:true}))
    await sleep(20)
    return {spawned:spawned.length,reserve}
  },
  async 'parallel-report-trigger-during-fill-latches'() {
    let reserve=0; const spawned=[]
    const {$}=host(async(tool,args)=>{
      if(tool==='work_status') return text(snapshot())
      if(tool==='work_parallel_status') return text({version:9,active_device:0,
        policy:{enabled:true,paused:false,priority_limits:{P1:3,P2:0,P3:0,P4:0,P5:0}},leases:{}})
      if(tool==='work_parallel_event'){
        if(args.event==='reserve_next'){
          reserve++
          if(reserve===1 || reserve===3) return text({lease_id:'L'+reserve,plan_version:7,item_id:'n'+reserve,priority:'P1',worker_token:'e'.repeat(64)})
          return {isError:true,...text('no eligible candidate')}
        }
        if(args.event==='bind') return text({})
        return text({})
      }
      return text({})
    })
    $.session.repo=async()=>null; $.session.root=async()=>'/repo'
    $.agent.spawn=async args=>{
      spawned.push(args)
      if(spawned.length===1) await handlers['tool.call']($,{tool:'mcp__ciel-runtime-router__work_parallel_report'},next({ok:true}))
      return {agentId:'agent-'+spawned.length}
    }
    await handlers['session.start']($,{isInteractive:true},next(undefined))
    return {spawned:spawned.length,reserve}
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
        if (args.event === 'resume') lease.state = 'running'
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
    return { afterWait, resumed: Boolean(resumed?.sent), slot: lease.slot, state: lease.state, calls: calls.map(c => c.event),
      resume: calls.find(c => c.event === 'resume') || null }
  },
  // Stage 3 (2026-11-17): the pump resumes a parked worker whose board wait is
  // cleared (reacquire -> resume -> session.send to the same agent), leaves one
  // still waiting alone, and records a refused delivery.
  async 'parallel-pump-resumes-cleared-parked-worker'() {
    const mode = process.argv[3] || 'delivered'
    // wait_clear_now / wait_cleared are the router's host-view verdicts
    // (parked_wait_cleared, _parked_wait_marks). L3 asked a question: nothing
    // is recorded on its item, so it waits for main and is never resumed here.
    const leases = {
      L1: { id:'L1', item_id:'n2', role:'worker', state:'waiting_input', host_id:'agent-a', agent_name:'work-auto-a',
        slot:false, plan_version:7, priority:'P2', wait_clear_now:true, wait_seen:true, wait_cleared:true },
      L2: { id:'L2', item_id:'n3', role:'worker', state:'waiting_input', host_id:'agent-b', agent_name:'work-auto-b',
        slot:false, plan_version:7, priority:'P2', wait_clear_now:false, wait_seen:true, wait_cleared:false },
      L3: { id:'L3', item_id:'n4', role:'worker', state:'waiting_input', host_id:'agent-c', agent_name:'work-auto-c',
        slot:false, plan_version:7, priority:'P2', wait_clear_now:true, wait_seen:false, wait_cleared:false } }
    const calls = [], noticeClaims = new Set()
    const { $, state } = host(async (tool, args) => {
      if (tool === 'work_status') return text(snapshot())
      if (tool === 'work_parallel_status') return text({ version:9, active_device:0,
        policy:{ enabled:true, paused:false, priority_limits:{P1:3,P2:2,P3:1,P4:1,P5:1}, launches_per_minute:4 }, leases })
      if (tool === 'work_parallel_event') {
        calls.push(args)
        if (args.event === 'reacquire') leases[args.lease_id].slot = true
        if (args.event === 'wait') leases[args.lease_id].slot = false
        if (args.event === 'resume') leases[args.lease_id].state = 'running'
        if (args.event === 'parked_notice_claim') { if (args.release) { noticeClaims.delete(args.lease_id); return text({ claimed:true }) }
          const fresh = !noticeClaims.has(args.lease_id); noticeClaims.add(args.lease_id); return text({ claimed:fresh }) }
        if (args.event === 'reserve' || args.event === 'reserve_next') throw new Error('no eligible candidate')
        return text({ version:10 })
      }
      return text({})
    })
    // The native list drops a parked fork after a while (E2E stub-3): agent-a is absent in 'unlisted'.
    state.agents = [{ id:'agent-a', status:'idle', type:'fork', spawnedBy:'ciel-work' },
      { id:'agent-b', status:'idle', type:'fork', spawnedBy:'ciel-work' },
      { id:'agent-c', status:'idle', type:'fork', spawnedBy:'ciel-work' }].filter(row => mode !== 'unlisted' || row.id !== 'agent-a')
    // As measured on 2.1.327: a send to an idle fork raises agent.spawn with
    // resume.agentId inside the plugin-origin frame; a denied spawn is undelivered.
    $.session.send = async args => { (state.sent ||= []).push(args)
      if (mode === 'refused') return { isDelivered:false, reason:'a plugin declined the resume' }
      const row = Object.values(leases).find(l => l.host_id === args.to?.agentId)
      const resumeNext = () => { const fn = async () => ({ agentId:args.to.agentId }); fn.origin = { plugin:'ciel-work' }; return fn }
      const started = await handlers['agent.spawn']($, { fork:true, subagentType:'fork', name:row.agent_name,
        description:'resume', prompt:'', resume:{ agentId:args.to.agentId } }, resumeNext())
      if (started?.deny) return { isDelivered:false, reason:'a plugin declined the resume: ' + started.deny }
      const listed = state.agents.find(agent => agent.id === args.to.agentId)
      if (listed) listed.status = 'running'
      return { isDelivered:true } }
    await handlers['session.start']($, { isInteractive:true }, next(undefined))
    await handlers['turn.complete']($, {}, next(undefined))
    await sleep(20)
    return { notices:(state.appended || []).map(row => row.message?.content?.[0]?.text || '').filter(t => t.startsWith('Ciel Work parked worker')),
      events:calls.map(c => c.event + ':' + (c.lease_id || '')), sent:(state.sent || []).map(s => s.to?.agentId),
      text:(state.sent || [])[0]?.text || '', l1:{ state:leases.L1.state, slot:leases.L1.slot },
      l2:{ state:leases.L2.state, slot:leases.L2.slot }, l3:{ state:leases.L3.state, slot:leases.L3.slot },
      logs:state.logs.filter(l => /parked|resumed parked/.test(l)) }
  },
  async 'parallel-missing-exit-report-requests-contract'() {
    const leases = { L1: { id:'L1', item_id:'n2', role:'worker', state:'running', host_id:'agent-old', slot:true, plan_version:7 } }
    const calls = [], tools = []
    const { $, state } = host(async (tool, args) => {
      if (tool === 'work_status') return text(snapshot())
      if (tool === 'work_parallel_status') return text({ version:9, active_device:1,
        policy:{enabled:true,paused:false,priority_limits:{P1:5,P2:3,P3:1,P4:1,P5:1}}, leases,
        coordination:{runs:[{lease_id:'L1',exit:null}],receipts:[]} })
      if (tool === 'work_parallel_event') { calls.push(args.event); return text({}) }
      return text({})
    })
    $.tool.call = async args => { tools.push(args); return {} }
    state.agents = [{ id:'agent-old', status:'running' }]
    await handlers['session.start']($, { isInteractive:false }, next(undefined))
    state.sent = []; state.agents[0].status='completed'
    await handlers['turn.complete']($, { agentId:'agent-old', reason:'answer' }, next(undefined))
    return { calls, tools, sent:state.sent, lease:leases.L1 }
  },
  async 'parallel-second-missing-exit-salvages-before-stop'() {
    const leases = { L1: { id:'L1', item_id:'n2', role:'worker', state:'running', host_id:'agent-old', slot:true, plan_version:7 } }
    const calls = [], tools = []
    const { $, state } = host(async (tool, args) => {
      if (tool === 'work_status') return text(snapshot())
      if (tool === 'work_parallel_status') return text({ version:9, active_device:1,
        policy:{enabled:true,paused:false,priority_limits:{P1:5,P2:3,P3:1,P4:1,P5:1}}, leases,
        coordination:{runs:[{lease_id:'L1',exit:null}],receipts:[]} })
      if (tool === 'work_parallel_event') {
        calls.push(args.event)
        if (args.event === 'host_salvage') return text({ receipt_digest:'a'.repeat(64), salvaged:true })
        if (args.event === 'unreported') { leases.L1.state='unreported'; leases.L1.slot=false; return text({state:'unreported'}) }
        return text({})
      }
      return text({})
    })
    $.tool.call = async args => { tools.push(args); return {} }
    state.agents = [{ id:'agent-old', status:'running' }]
    await handlers['session.start']($, { isInteractive:false }, next(undefined))
    state.sent = []; calls.length = 0; state.agents[0].status='completed'
    await handlers['turn.complete']($, { agentId:'agent-old', reason:'answer' }, next(undefined))
    await handlers['turn.complete']($, { agentId:'agent-old', reason:'answer' }, next(undefined))
    return { calls, tools, sent:state.sent, lease:leases.L1 }
  },
  async 'parallel-recovery-context'() {
    const lease = { id:'L1', item_id:'n2', role:'worker', state:'running', host_id:'agent-old',
      agent_name:'work-auto-exact', dispatch_name:'work-auto-exact', slot:true, plan_version:7 }
    const { $, state } = host(async tool => tool === 'work_parallel_status'
      ? text({ version:9, active_device:1, policy:{enabled:true,paused:false,priority_limits:{P1:5,P2:3,P3:1,P4:1,P5:1}}, leases:{L1:lease} })
      : text(snapshot()))
    state.agents = []
    await handlers['session.start']($, { isInteractive:false }, next(undefined))
    let seen = null
    const capture = async e => { seen=e; return e }; capture.origin={plugin:'engine'}
    await handlers['prompt.submit']($, { context:[] }, capture)
    const duplicate = await handlers['tool.call']($, { tool:'mcp__ciel-runtime-router__work_parallel_run',
      item_id:'n2', priority:'P1', role:'worker' }, next({}))
    return { context:JSON.stringify(seen), sent:state.sent || [], duplicate:verdict(duplicate) }
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
    const spawnNext = next({ agentId: 'agent-new' }); spawnNext.origin = { plugin: 'ciel-work' }
    const spawned = await handlers['agent.spawn']($, { name: 'work-auto-exact', subagentType: 'fork', fork: true,
      resume: { agentId: 'agent-old' }, prompt: 'resume', description: 'resume', background: true }, spawnNext)
    const duplicate = await handlers['agent.spawn']($, { name: 'work-auto-exact', subagentType: 'fork', fork: true,
      prompt: 'duplicate', description: 'duplicate', background: true }, spawnNext)
    return { sent: Boolean(sent.sent), spawned: spawned.agentId || '', duplicate: verdict(duplicate) }
  },
  // An external web/REST decision can add an awaiting-capacity review and then
  // resume policy without changing the Work board version. With main idle and
  // no fork running, the idle refresh is the only periodic host boundary.
  async 'parallel-idle-roll-forward'() {
    const timers = []
    let reviews = []
    const rolled = []
    const { $ } = host(async (tool, args) => {
      if (tool === 'work_status') return text(snapshot('executing', 5))
      if (tool === 'work_parallel_status') return text({ version:9, active_device:0,
        policy:{enabled:true,paused:false,priority_limits:{P1:1}}, leases:{},
        review:{version: reviews.length ? 2 : 1, pending:reviews} })
      if (tool === 'work_parallel_event') {
        if (args.event === 'review_roll_forward') { rolled.push(args.lease_id); return text({lease_id:args.lease_id,state:'rework_ready'}) }
        if (args.event === 'reserve_next') return {isError:true,...text('no eligible candidate')}
        return text({})
      }
      return text({})
    })
    $.session.repo=async()=>null; $.session.root=async()=>'/repo'
    $.prompt={submit:async()=>({})}; $.session.append=async()=>({})
    $.clock.after = (delay, callback) => {
      const timer = { delay, callback, cancelled:false }
      timers.push(timer)
      return { cancel() { timer.cancelled = true } }
    }
    await handlers['session.start']($, { isInteractive:true }, next(undefined))
    reviews = [{lease_id:'LA',item_id:'na',item_title:'Waiting',priority:'P1',state:'awaiting_capacity',
      decision:'rework',blocked:{code:'paused',message:'dispatch paused'}}]
    const idle = timers.find(timer => timer.delay === 1000 && !timer.cancelled)
    if (!idle) throw new Error('idle refresh timer was not armed')
    await idle.callback()
    await sleep(0)
    return { rolled, boardVersion:5, idleDelay:idle.delay }
  },
  // R2 B2 router model (added by continue-work-pump; the scenarios above model
  // a pre-B2 router that sends no owner_process). One owner class per run:
  // L1 running and unlisted (path 2 recovery, path 4 turn-end reconcile), L2
  // submitted with an exact completion receipt and unlisted (path 3).
  async 'parallel-owner-process-b2'() {
    const owner = process.argv[3]
    const leases = {
      L1: { id:'L1', item_id:'n2', role:'worker', state:'running', host_id:'agent-run', agent_name:'work-auto-run',
        dispatch_name:'work-auto-run', slot:true, plan_version:7, owner_process:owner },
      L2: { id:'L2', item_id:'n3', role:'worker', state:'submitted', host_id:'agent-sub', slot:false, plan_version:7,
        owner_process:owner } }
    const events = []
    const { $, state } = host(async (tool, args) => {
      if (tool === 'work_status') return text(snapshot())
      if (tool === 'work_parallel_status') return text({ version:9, active_device:1,
        policy:{ enabled:true, paused:false, priority_limits:{ P1:5, P2:3, P3:1, P4:1, P5:1 } }, leases,
        session_attachments:{ available:true, live:owner === 'other_live' ? 2 : 1, gone:owner === 'gone' ? 1 : 0,
          unknown:0, attachment_version:3 },
        coordination:{ runs:[{ lease_id:'L1', exit:null },
          { lease_id:'L2', generation:1, agent_id:'agent-sub', exit:{ kind:'completion', digest:'d'.repeat(64) } }], receipts:[] } })
      if (tool === 'work_parallel_event') { events.push(args.event); return text({}) }
      return text({})
    })
    state.agents = []
    await handlers['session.start']($, { isInteractive:false }, next(undefined))
    const recovery = (state.sent || []).filter(s => s.to?.agentId === 'agent-run').length
    const reconcileBefore = events.filter(e => e === 'reconcile').length
    await handlers['turn.complete']($, {}, next(undefined))
    const shown = (await handlers['command.run']($, { args:'parallel' })).text || ''
    return { owner, recovery, restart:events.filter(e => e === 'restart_submitted_terminal').length,
      turnReconciles:events.filter(e => e === 'reconcile').length - reconcileBefore,
      hosts:shown.split('\n').filter(line => line.startsWith('HOSTS:')) }
  },
  // A parent restart has no in-memory map; the durable lease alone restores worker identity.
  async 'parallel-restart-rebind'() {
    const lease = { id: 'L1', role: 'worker', state: 'running', host_id: 'agent-old', slot: true, plan_version: 7 }
    const { $, state } = host(async (tool, args) => {
      if (tool === 'work_status') return text(snapshot())
      if (tool === 'work_parallel_status') return text({ version: 9, policy: { enabled: true }, leases: { L1: lease } })
      if (tool === 'work_parallel_event') { (state.events ||= []).push(args); return text({ version: 10, leases: {} }) }
      return text({})
    })
    state.agents = []
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    const result = await handlers['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_parallel_event',
      agentId: 'agent-old', event: 'result', lease_id: 'L1', plan_version: 7, artifact: 'a'.repeat(64), evidence: 'r' }, next({}))
    await handlers['turn.complete']($, {}, next(undefined))
    return { result: verdict(result), events: state.events || [] }
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
  async 'side-receipt-list-uses-message-then-metadata-two-lines'() {
    const original = JSON.stringify({ specversion:'1.0', type:'com.ciel.walkie.support.message',
      data:{ event_type:'message.created', text:'GitHub · PR #94 validator 수정 결과를 검토해 주세요' } })
    const receipt = { receipt_id:'two-lines', stage:'received', original, recap:original }
    const { $ } = host(async tool => {
      if (tool === 'work_status') return text({ ...snapshot(), side_inbox_receipts:[receipt] })
      if (tool === 'work_side_status') return text({ enabled:true, side_inbox_receipts:[receipt] })
      if (tool === 'work_side_claim') return text({ claim:null, settings:{}, handoff_replies:[], main_wakes:[] })
      return text({})
    })
    $.ui.resolve = () => ({ Box:props => props, Text:props => props, Button:props => props })
    await sideHandlers['command.run']($, { args:'receipts' })
    const pane = await sideHandlers['ui.render']($, { component:'Pane', requestId:'ciel-side-intake',
      props:{ bodyColumns:64, scroll:{bodyRows:30} } }, next(null))
    const button = pane.children.find(row => row.key === 'side-row-two-lines')
    const meta = pane.children.find(row => row.key === 'side-meta-two-lines')
    return { button:button?.label || '', meta:meta?.children || '', rows:Boolean(button && meta) }
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
    const receipt = {receipt_id:'mail1',stage:'processed',original:'Q',reply_attempt_ref:'b'.repeat(16),reply_revision:2,
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
  // A router that refuses the pane's person-confirmed retries with its host gate
  // (side_intake_mcp._person_retry_host). Each pane retry must say where else
  // a person can retry; any other refusal keeps the plain message.
  async 'pane-retry-refused-by-host-gate'() {
    const gate = 'person-confirmed retry requires the verified Ciel Work host'
    const run = async (refusal) => {
      const toasts = []
      const handoff = { event_id:'r'.repeat(64), item_id:'n2', kind:'resume', summary:'Uncertain wake', question:'', waits:[],
        wake:{ state:'uncertain', phase:'started', revision:4, attempt_ref:'ref-pane', delivery_uncertain:true } }
      const receipt = { receipt_id:'u'.repeat(64), stage:'processed', item_id:'n2', recap:'approval 7 granted', original:'Q',
        notice_delivery_uncertain:true,
        result:{ action:'update', summary:'S', reply:'R', delivery:'uncertain', handoff:'none', version:1 } }
      const { $ } = host(async (tool) => {
        if (tool === 'work_status') return text({ ...snapshot(), side_handoffs:[handoff] })
        if (tool === 'work_side_status') return text({ enabled:true, side_inbox_receipts:[receipt] })
        if (tool === 'work_side_claim') return text({ claim:null, settings:{}, handoff_replies:[], main_wakes:[] })
        if (tool === 'work_side_wake' || tool === 'work_side_retry_reply') return { isError:true, ...text(refusal) }
        return text({}) })
      $.ui.toast = message => toasts.push(String(message))
      $.ui.ask = async (_question, options) => options[1]
      $.ui.resolve = () => ({ Box:props => props, Text:props => props, Button:props => props })
      await handlers['command.run']($, { args:'status' })
      await sideHandlers['command.run']($, { args:'receipts' })
      const pane = await sideHandlers['ui.render']($, { component:'Pane', requestId:'ciel-side-intake' }, next(null))
      const flat = []
      const walk = node => { if (!node || typeof node !== 'object') return; flat.push(node); for (const child of [].concat(node.children || [])) walk(child) }
      walk(pane)
      for (const label of ['Retry main wake…', 'Retry release notice…', 'Retry reply…']) {
        const button = flat.find(node => node.label === label)
        if (!button) { toasts.push('missing button ' + label); continue }
        await button.onPress()
      }
      return toasts
    }
    return { gate:await run(gate), other:await run('side wake changed; refresh before retry') }
  },
  // A conversation re-append whose outcome is unknown (side_wake.redact_wake:
  // reappend_uncertain). The pane offers a may-duplicate release; Cancel sends
  // nothing; the confirmed press sends only the redacted ref and revision.
  async 'side-reappend-pane-retry'() {
    const calls = []
    const answers = ['Cancel', 'Retry re-append (may duplicate)']
    const handoff = (id, uncertain) => ({ event_id:id.repeat(64), item_id:'n2', kind:'resume', summary:'Moved event ' + id,
      question:'', waits:[], wake:{ state:'appended', revision:7, attempt_ref:'a'.repeat(16), delivery_uncertain:false,
        reappend_state:uncertain ? 'uncertain' : 'submitting', reappend_ref:'r'.repeat(16), reappend_uncertain:uncertain } })
    const { $ } = host(async (tool, args) => { calls.push({ tool, args })
      if (tool === 'work_status') return text({ ...snapshot(), side_handoffs:[handoff('q', true), handoff('w', false)] })
      if (tool === 'work_side_status') return text({ enabled:true, side_inbox_receipts:[] })
      if (tool === 'work_side_claim') return text({ claim:null, settings:{}, handoff_replies:[], main_wakes:[] })
      if (tool === 'work_side_wake') return text({ protocol:2, state:'appended', revision:8 })
      return text({}) })
    $.ui.ask = async () => answers.shift()
    $.ui.resolve = () => ({ Box:props => props, Text:props => props, Button:props => props })
    await handlers['command.run']($, { args:'status' })
    const pane = await sideHandlers['ui.render']($, { component:'Pane', requestId:'ciel-side-intake' }, next(null))
    const rows = pane.children
    const labels = rows.filter(r => typeof r.children === 'string' && r.children.startsWith('Re-append uncertain')).map(r => r.children)
    const buttons = rows.filter(r => r.label === 'Retry re-append…')
    await buttons[0]?.onPress()
    const afterCancel = calls.filter(c => c.tool === 'work_side_wake').length
    await buttons[0]?.onPress()
    return { labels, buttons:buttons.length, afterCancel,
      retries:calls.filter(c => c.tool === 'work_side_wake').map(c => ({ ...c.args, _host:{ internal:c.args._host?.internal === true } })) }
  },
  // The same release for a parallel review re-append: its row comes from the
  // host-only side status's review_delivery view (review_delivery.public_view).
  async 'review-reappend-pane-retry'() {
    const calls = []
    const answers = ['Cancel', 'Retry review re-append (may duplicate)']
    const rows = [{ lease_id:'L-stuck', state:'appended', in_this_conversation:false, reappends:0, reappend_state:'uncertain',
      reappend_ref:'s'.repeat(16), reappend_uncertain:true },
      { lease_id:'L-live', state:'reappending', in_this_conversation:false, reappends:0, reappend_state:'submitting',
        reappend_ref:'l'.repeat(16), reappend_uncertain:false }]
    const { $ } = host(async (tool, args) => { calls.push({ tool, args })
      if (tool === 'work_status') return text({ ...snapshot(), side_handoffs:[] })
      if (tool === 'work_side_status') return text({ enabled:true, side_inbox_receipts:[],
        review_delivery:{ available:true, truncated:false, rows } })
      if (tool === 'work_side_claim') return text({ claim:null, settings:{}, handoff_replies:[], main_wakes:[] })
      if (tool === 'work_side_wake') return text({ protocol:2, lease_id:args.event_id, state:'released' })
      return text({}) })
    $.ui.ask = async () => answers.shift()
    $.ui.resolve = () => ({ Box:props => props, Text:props => props, Button:props => props })
    await sideHandlers['command.run']($, { args:'receipts' })
    const pane = await sideHandlers['ui.render']($, { component:'Pane', requestId:'ciel-side-intake' }, next(null))
    const labels = pane.children.filter(r => typeof r.children === 'string' && r.children.startsWith('Review re-append uncertain'))
      .map(r => r.children)
    const buttons = pane.children.filter(r => r.label === 'Retry review re-append…')
    await buttons[0]?.onPress()
    const afterCancel = calls.filter(c => c.tool === 'work_side_wake').length
    await buttons[0]?.onPress()
    return { labels, buttons:buttons.length, afterCancel,
      retries:calls.filter(c => c.tool === 'work_side_wake').map(c => ({ ...c.args, _host:{ internal:c.args._host?.internal === true } })) }
  },
  async 'side-receipt-visible'() {
    const board = { ...snapshot(), side_intake_receipts: [{ item_id: 'n77', recap: 'request recap' }] }
    const { $, state } = host(async () => text(board))
    await check($)
    await handlers['command.run']($, { args: 'status' })
    await handlers['command.run']($, { args: 'status' })
    return { logs: state.logs.filter(s => s.includes('mod queued')) }
  },
  async 'parallel-completion-gate-target-scoped'() {
    const leases={
      A:{id:'A',item_id:'n-other',role:'worker',state:'running',slot:true},
      B:{id:'B',item_id:'n-terminal',role:'worker',state:'unreported',slot:false},
    }
    const {$}=host(async(tool)=>tool==='work_parallel_status'
      ? text({version:1,policy:{enabled:true},leases,coordination:{runs:[],receipts:[]}})
      : text(snapshot()))
    const unrelated=await handlers['tool.call']($,{tool:'mcp__ciel-runtime-router__work_node_update',id:'n-target',status:'done'},next({ran:true}))
    leases.C={id:'C',item_id:'n-child',role:'worker',state:'running',slot:true}
    const blocked=await handlers['tool.call']($,{tool:'mcp__ciel-runtime-router__work_node_update',id:'n-child',status:'done'},next({ran:true}))
    return {unrelated:verdict(unrelated),blocked:verdict(blocked)}
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
  async 'parallel-stale-stop-no-spawn'() {
    const ready={...snapshot(),auto_continue:false}; let release
    const gate=new Promise(resolve=>{release=resolve}); const spawned=[]
    const {$}=host(async(tool,args)=>{
      if(tool==='work_status'){await gate;return text(ready)}
      if(tool==='work_parallel_status') return text({version:1,active_device:0,
        policy:{enabled:true,paused:false,priority_limits:{P1:2,P2:0,P3:0,P4:0,P5:0}},leases:{}})
      if(tool==='work_parallel_event'&&args.event==='reserve_next') return text({lease_id:'L1',plan_version:7,item_id:'n2',priority:'P1',worker_token:'f'.repeat(64)})
      if(tool==='work_parallel_event') return text({})
      return text({})
    })
    $.session.repo=async()=>null; $.session.root=async()=>'/repo'
    $.agent.spawn=async args=>{spawned.push(args);return {agentId:'agent-new'}}
    const pending=handlers['classic.Stop']($,{session_id:'S1'},next({}))
    await sleep(10); await handlers['turn.start']($,{},next(undefined)); release()
    await pending
    return {spawned:spawned.length}
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

// Adversarial Stop/wake schedules, ported from review-side-wake's
// stop_adversarial.mjs. The fake ledger follows side_wake.py: prepare elects an
// intent (same owner adopts; another owner gets already_intent), start fences
// intent -> started, a record needs started plus the exact attempt and owner.
// attempt_ref is the real sha256 prefix, as the redactor derives it.
const ADV_EVENT = 'e'.repeat(64)
function advLedger(opts = {}) {
  const L = { wake:{ state:'pending', revision:0 }, attempts:0, calls:[], wakeArgs:[], startFaults:opts.startFaults || 0,
    startCommitThenLose:opts.startCommitThenLose || 0, recordFaults:opts.recordFaults || 0 }
  L.pub = () => {
    const w = L.wake, out = { state:w.state, revision:w.revision }
    if (w.phase) out.phase = w.phase
    if (w.channel) out.channel = w.channel
    if (w.attempt_id) out.attempt_ref = attemptRef(w.attempt_id)
    if (w.needs_ack_retry) out.needs_ack_retry = true
    out.delivery_uncertain = w.state === 'uncertain' || (w.state === 'submitting' && w.phase === 'started')
    return out
  }
  L.row = hostView => ({ event_id:ADV_EVENT, item_id:'n2', kind:'time_limit', summary:'deadline', question:'', waits:[],
    time_limit:{ text:'within 30 min', evidence:'m1' },
    wake:hostView ? { ...L.pub(), attempt_id:L.wake.attempt_id, channel:L.wake.channel } : L.pub() })
  L.own = () => ({ protocol:2, state:L.wake.state, phase:L.wake.phase, attempt_id:L.wake.attempt_id,
    owner_nonce:L.wake.owner, channel:L.wake.channel, revision:L.wake.revision })
  L.call = async (tool, args) => {
    L.calls.push(tool + ':' + (args?.state || '') + (args?.channel ? ':' + args.channel : ''))
    const w = L.wake
    if (tool === 'work_status') return text({ ...snapshot(), auto_continue:false, side_handoffs:[L.row(false)] })
    if (tool === 'work_parallel_status') return text({ version:1, policy:{ enabled:false }, leases:{} })
    if (tool === 'work_side_status') return text({ enabled:true, side_inbox_receipts:[] })
    if (tool === 'work_side_claim') {
      const offered = ['pending', 'failed'].includes(w.state) || (w.state === 'submitting' && ['intent', 'started'].includes(w.phase))
        || (w.state === 'appended' && !w.read_at)
      return text({ claim:null, settings:{}, handoff_replies:[], main_wakes:offered ? [L.row(true)] : [] })
    }
    if (tool !== 'work_side_wake') return text({})
    if (String(args.state).startsWith('prompt_')) {
      if (args.state === 'prompt_prepare') {
        if (w.prompt && ['submitting','queued'].includes(w.prompt.state))
          return text({protocol:2,state:'already_'+w.prompt.state,revision:w.revision})
        w.prompt = {state:'submitting',attempt_id:'prompt-'+ADV_EVENT.slice(0,12),owner:args.owner_nonce}
        return text({protocol:2,state:'submitting',attempt_id:w.prompt.attempt_id,owner_nonce:args.owner_nonce,revision:w.revision})
      }
      if (!w.prompt || w.prompt.attempt_id !== args.attempt_id || w.prompt.owner !== args.owner_nonce)
        return {isError:true,...text('stale side wake prompt attempt')}
      w.prompt.state = args.state.slice(7)
      return text(L.own())
    }
    if (args.state === 'read') {
      if (w.state !== 'appended') return { isError:true, ...text('side wake is not appended') }
      L.wake = { ...w, read_at:1 }
      return text(L.own())
    }
    L.wakeArgs.push({ state:args.state, attempt_id:args.attempt_id ?? null, owner_nonce:args.owner_nonce ?? null,
      channel:args.channel ?? null })
    if (args.state === 'prepare') {
      if (['pending', 'failed'].includes(w.state)) {
        L.wake = { ...w, state:'submitting', phase:'intent', attempt_id:String(++L.attempts).repeat(32).slice(0, 32),
          owner:args.owner_nonce, channel:args.channel, revision:w.revision + 1 }
        return text(L.own())
      }
      if (w.state === 'submitting' && w.phase === 'intent')
        return w.owner === args.owner_nonce ? text(L.own()) : text({ protocol:2, state:'already_intent', revision:w.revision })
      return text({ protocol:2, state:'already_' + w.state, wake_state:w.state, phase:w.phase, revision:w.revision })
    }
    if (args.state === 'start') {
      if (L.startFaults > 0) { L.startFaults--; return { isError:true, ...text('transport') } }
      if (w.state !== 'submitting' || w.phase !== 'intent' || args.attempt_id !== w.attempt_id || args.owner_nonce !== w.owner
          || args.channel !== w.channel) return { isError:true, ...text('stale side wake intent') }
      L.wake = { ...w, phase:'started', revision:w.revision + 1 }
      if (L.startCommitThenLose > 0) { L.startCommitThenLose--; return { isError:true, ...text('answer lost') } }
      return text(L.own())
    }
    if (['queued', 'appended', 'uncertain', 'failed'].includes(args.state)) {
      if (L.recordFaults > 0) { L.recordFaults--; return { isError:true, ...text('transport') } }
      if (w.state === args.state && w.attempt_id === args.attempt_id) return text(L.own())
      if (w.state !== 'submitting' || w.phase !== 'started' || args.attempt_id !== w.attempt_id || args.owner_nonce !== w.owner)
        return { isError:true, ...text('stale side wake attempt') }
      L.wake = { ...w, state:args.state, revision:w.revision + 1 }
      return text(L.own())
    }
    if (args.state === 'retry') { L.retry = { ...args }; L.retryInternal = args._host?.internal === true
      delete L.retry.session_id; delete L.retry._host; return text({ protocol:2, state:'failed' }) }
    return text({})
  }
  return L
}
function advHost(L) {
  const state = { prompts:[], appends:[], logs:[], asks:[] }
  const hostStore = new Map()
  const $ = {
    session:{ id:async () => 'S1', model:async () => 'm', messages:async () => [], send:async () => ({ isDelivered:true }),
      append:async a => { state.appends.push(a.message.content[0].text); return { uuid:'u' + state.appends.length } } },
    prompt:{ submit:async a => { state.prompts.push(a.text); return { text:a.text } } },
    state:{get:async ref=>{const held=hostStore.get(ref.plugin+'/'+ref.key);return {value:held?structuredClone(held.value):undefined,version:held?.version||0}},set:async (ref,value,options={})=>{const name=ref.plugin+'/'+ref.key,held=hostStore.get(name)||{version:0};if(options.ifVersion!==undefined&&options.ifVersion!==held.version)return {isSet:false,version:held.version};hostStore.set(name,{value:structuredClone(value),version:held.version+1});return {isSet:true,version:held.version+1}}},
    mcp:{ connect:async () => ({}), call:async (server, tool, args) => L.call(tool, args) },
    ui:{ invalidate(){}, toast(){}, log(t){ state.logs.push(String(t)) }, open:async () => {},
      ask:async (q, opts) => { state.asks.push(opts); return state.answer || 'Cancel' } },
    clock:{ after(){ return { cancel(){} } } }, env:{ get:async () => 'host-token', set:async () => ({}) },
    tool:{ list:async () => [], call:async () => ({}), check:async q => typeof q?.input?.ciel_work_gate_probe === 'string' && state.forkGate !== false ? { decision:'ask', plugin:'ciel-work-gate', reason:'ciel-work-gate 1 ' + q.input.ciel_work_gate_probe } : { decision:'allow' } },
    agent:{ list:async () => [], register:async () => ({}), spawn:async () => ({ deny:'fixture' }) },
    command:{ register:async () => {} },
  }
  return { $, state }
}
const advStop = async ($, extra = {}) => handlers['classic.Stop']($, { session_id:'S1', ...extra }, next({}))
const advStep = ($, turnId = 'T', index = 1) => mainStep($, turnId, index)
const advTurnStart = async ($, turnId = 'T2') => handlers['turn.start']($, { turnId, text:'' }, next(undefined))
const advTurnComplete = async ($, turnId = 'T', reason = 'answer') =>
  handlers['turn.complete']($, { turnId, reason, answer:'x' }, next(undefined))
const advPane = async $ => {
  $.ui.resolve = () => ({ Box:p => p, Text:p => p, Button:p => p })
  await handlers['command.run']($, { args:'status' })
  return sideHandlers['ui.render']($, { component:'Pane', requestId:'ciel-side-intake' }, next(null))
}
const advEvidence = s => String(s || '').includes(ADV_EVENT)
const advWakeCalls = L => L.calls.filter(c => c.startsWith('work_side_wake'))
const advLeak = t => /[0-9a-f]{32}/.test(String(t || '').replace(new RegExp(ADV_EVENT, 'g'), ''))
Object.assign(scenarios, {
  // Lost start answer: the pump delivers under the same started attempt and
  // records it with that attempt and owner; never a second attempt.
  async 'adv-start-lost'() {
    const committed = process.argv[3] === 'committed'
    const L = advLedger(committed ? { startCommitThenLose:1 } : { startFaults:1 })
    const { $, state } = advHost(L)
    await advTurnStart($, 'T1')
    const s1 = await advStop($)
    await sidePump($); await sleep(10); await sidePump($); await sleep(10)
    const starts = L.wakeArgs.filter(a => a.state === 'start')
    const records = L.wakeArgs.filter(a => ['queued', 'appended', 'uncertain', 'failed'].includes(a.state))
    return { stopBlock:advEvidence(s1?.block), prompts:state.prompts.length, appends:state.appends.length, attempts:L.attempts,
      wake:L.wake.state + '/' + L.wake.channel, routerAttempt:L.wake.attempt_id, routerOwner:L.wake.owner,
      starts, records, calls:advWakeCalls(L) }
  },
  // Review defect C: another process superseded the stop intent after its lease,
  // started and died; Stop's start fails on the CAS. The stale unpresented
  // entry must not hide the foreign started row.
  async 'adv-stale-unpresented'() {
    const L = advLedger(); const { $, state } = advHost(L)
    const base = L.call
    L.call = async (tool, args) => {
      if (tool === 'work_side_wake' && args.state === 'start' && args.channel === 'stop') {
        L.wake = { ...L.wake, attempt_id:'9'.repeat(32), owner:'other-process', channel:'prompt', phase:'started', revision:L.wake.revision + 2 }
        L.calls.push('work_side_wake:start:stop(stale)')
        return { isError:true, ...text('stale side wake intent') }
      }
      return base(tool, args)
    }
    await advTurnStart($, 'T1')
    const s1 = await advStop($)
    await sidePump($); await sleep(10)
    await advTurnComplete($, 'T1'); await advTurnStart($, 'T2')
    const s2 = await advStop($)
    const pane = await advPane($)
    return { firstStopBlock:advEvidence(s1?.block), wake:L.wake.state + '/' + L.wake.phase + '/' + L.wake.owner,
      laterStopUncertainBlock:String(s2?.block || '').includes('delivery to main is uncertain'),
      paneRetryOffered:(pane?.children || []).some(r => r.label === 'Retry main wake…'), prompts:state.prompts.length }
  },
  async 'adv-control-uncertain-visible'() {
    const L = advLedger()
    L.wake = { state:'submitting', phase:'started', attempt_id:'9'.repeat(32), owner:'other-process', channel:'prompt', revision:4 }
    const { $ } = advHost(L)
    await advTurnStart($, 'T1')
    const s = await advStop($)
    const pane = await advPane($)
    return { stopUncertainBlock:String(s?.block || '').includes('delivery to main is uncertain'),
      paneRetryOffered:(pane?.children || []).some(r => r.label === 'Retry main wake…') }
  },
  // Review defect B, recordFaults=1: the record lands inside the next Stop's
  // retry loop; that Stop must not call the (now queued) wake uncertain.
  async 'adv-record-late-success'() {
    const L = advLedger({ recordFaults:1 }); const { $ } = advHost(L)
    await advTurnStart($, 'T1')
    const s1 = await advStop($)
    await advStep($, 'T1', 1)
    await advTurnComplete($, 'T1'); await advTurnStart($, 'T2')
    const s2 = await advStop($)
    const s3 = await advStop($)
    return { first:advEvidence(s1?.block), secondBlock:s2?.block || '', thirdBlock:s3?.block || '', wake:L.wake.state,
      calls:advWakeCalls(L) }
  },
  // recordFaults=2: the record fails at the proving step and at the
  // continuation's Stop, then lands; never re-presented, never "uncertain".
  async 'adv-record-fail-after-proof'() {
    const L = advLedger({ recordFaults:2 }); const { $ } = advHost(L)
    await advTurnStart($, 'T1')
    const s1 = await advStop($)
    await advStep($, 'T1', 1)
    const s2 = await advStop($, { stop_hook_active:true })
    await advTurnComplete($, 'T1'); await advTurnStart($, 'T2')
    const s3 = await advStop($)
    await sidePump($); await sleep(10)
    const s4 = await advStop($)
    return { first:advEvidence(s1?.block), second:s2?.block || '', third:s3?.block || '', fourth:s4?.block || '',
      wake:L.wake.state, calls:advWakeCalls(L) }
  },
  // Review defect A: a new main turn starts while the Stop election is in flight.
  async 'adv-turn-start-during-election'() {
    const L = advLedger(); const { $ } = advHost(L)
    let release; const gate = new Promise(r => { release = r })
    const base = L.call
    L.call = async (tool, args) => { if (tool === 'work_side_wake' && args.state === 'start') await gate; return base(tool, args) }
    await advTurnStart($, 'T1')
    const pending = advStop($)
    await sleep(20)
    await advTurnStart($, 'T2')
    release()
    const s1 = await pending
    await advStep($, 'T2', 0)
    await advStep($, 'T2', 1)
    await sleep(10)
    await advTurnComplete($, 'T2'); await advTurnStart($, 'T3')
    const s2 = await advStop($)
    return { staleStopReturnedBlock:advEvidence(s1?.block), wake:L.wake.state + '/' + L.wake.phase,
      records:L.calls.filter(c => c.startsWith('work_side_wake:queued')).length,
      laterUncertain:String(s2?.block || '').includes('delivery to main is uncertain') }
  },
  // Presented; the turn ends (interrupt or answer) before any step of it.
  async 'adv-turn-end-before-step'() {
    const L = advLedger(); const { $ } = advHost(L)
    const reason = process.argv[3] || 'aborted'
    await advTurnStart($, 'T1')
    const s1 = await advStop($)
    await advTurnComplete($, 'T1', reason)
    // A stray main step of the ended turn, with no turn.start, must not settle it.
    await advStep($, 'T1', 2)
    await advTurnStart($, 'T2')
    await advStep($, 'T2', 0)
    await sleep(10)
    return { presented:advEvidence(s1?.block), wake:L.wake.state + '/' + L.wake.phase,
      records:L.calls.filter(c => c.startsWith('work_side_wake:queued')).length }
  },
  async 'adv-stop-hook-active-loop'() {
    const L = advLedger(); const { $ } = advHost(L)
    await advTurnStart($, 'T1')
    const s1 = await advStop($)
    await advStep($, 'T1', 1)
    const s2 = await advStop($, { stop_hook_active:true })
    const s3 = await advStop($, { stop_hook_active:true })
    return { first:advEvidence(s1?.block), second:Boolean(s2?.block), third:Boolean(s3?.block), wake:L.wake.state }
  },
  async 'adv-uncertain-stop-hook-active'() {
    const L = advLedger()
    L.wake = { state:'submitting', phase:'started', attempt_id:'9'.repeat(32), owner:'other', channel:'prompt', revision:4 }
    const { $ } = advHost(L)
    await advTurnStart($, 'T1')
    const r = []
    for (let i = 0; i < 4; i++) r.push(await advStop($, { stop_hook_active:i > 0 }))
    return { blocks:r.map(x => Boolean(x?.block)), leak:r.some(x => advLeak(x?.block)) }
  },
  // An ACK conflict bumps the revision and sets needs_ack_retry: only the ACK
  // notice follows; main has the event, so no fresh "uncertain" notice.
  async 'adv-uncertain-then-ack-conflict'() {
    const L = advLedger()
    L.wake = { state:'submitting', phase:'started', attempt_id:'9'.repeat(32), owner:'other', channel:'prompt', revision:4 }
    const { $ } = advHost(L)
    await advTurnStart($, 'T1')
    const a = await advStop($)
    L.wake = { ...L.wake, revision:5, needs_ack_retry:true }
    const b = await advStop($)
    const c = await advStop($)
    const pane = await advPane($)
    return { a:a?.block || '', b:b?.block || '', c:Boolean(c?.block),
      paneRetryOffered:(pane?.children || []).some(r => r.label === 'Retry main wake…') }
  },
  // A NEW event at a Stop-block continuation (stop_hook_active) is still elected
  // (time-limit events need same-turn presentation), exactly once.
  async 'adv-continuation-new-event'() {
    const A = 'a'.repeat(64), B = 'b'.repeat(64)
    const wakes = { [A]:{ state:'pending', revision:0 } }
    const presented = { [A]:0, [B]:0 }
    let attempts = 0
    const pub = w => ({ state:w.state, revision:w.revision, ...(w.phase ? { phase:w.phase } : {}),
      ...(w.attempt_id ? { attempt_ref:attemptRef(w.attempt_id) } : {}),
      delivery_uncertain:w.state === 'submitting' && w.phase === 'started' })
    const rows = () => Object.entries(wakes).map(([event_id, w]) => ({ event_id, item_id:'n2', kind:'time_limit',
      summary:'deadline ' + event_id.slice(0, 1), question:'', waits:[], wake:pub(w) }))
    const own = w => ({ protocol:2, state:w.state, phase:w.phase, attempt_id:w.attempt_id, owner_nonce:w.owner,
      channel:w.channel, revision:w.revision })
    const L = { call:async (tool, args) => {
      if (tool === 'work_status') return text({ ...snapshot(), auto_continue:false, side_handoffs:rows() })
      if (tool === 'work_parallel_status') return text({ version:1, policy:{ enabled:false }, leases:{} })
      if (tool === 'work_side_claim') return text({ claim:null, settings:{}, handoff_replies:[], main_wakes:[] })
      if (tool !== 'work_side_wake') return text({})
      const w = wakes[args.event_id]
      if (args.state === 'prepare') {
        if (['pending', 'failed'].includes(w.state)) Object.assign(w, { state:'submitting', phase:'intent',
          attempt_id:String(++attempts).repeat(32), owner:args.owner_nonce, channel:args.channel, revision:w.revision + 1 })
        return text(own(w))
      }
      if (args.state === 'start') { Object.assign(w, { phase:'started', revision:w.revision + 1 }); return text(own(w)) }
      if (['queued','appended'].includes(args.state)) { Object.assign(w, { state:args.state, revision:w.revision + 1 }); return text(own(w)) }
      return text({})
    } }
    const { $ } = advHost(L)
    const count = r => { for (const id of [A, B]) if (String(r?.block || '').includes(id)) presented[id]++; return Boolean(r?.block) }
    await advTurnStart($, 'T1')
    const s1 = count(await advStop($))
    await advStep($, 'T1', 1)
    wakes[B] = { state:'pending', revision:0 }
    const s2 = count(await advStop($, { stop_hook_active:true }))
    await advStep($, 'T1', 2)
    const s3 = count(await advStop($, { stop_hook_active:true }))
    await advTurnComplete($, 'T1'); await advTurnStart($, 'T2')
    const s4 = count(await advStop($))
    return { blocks:[s1, s2, s3, s4], presented:[presented[A], presented[B]], states:[wakes[A].state, wakes[B].state] }
  },
  async 'adv-pump-between-stop-and-step'() {
    const L = advLedger(); const { $, state } = advHost(L)
    await advTurnStart($, 'T1')
    const s1 = await advStop($)
    await sidePump($); await sleep(10)
    await advStep($, 'T1', 1); await sleep(10)
    return { stop:advEvidence(s1?.block), prompts:state.prompts.length, appends:state.appends.length, wake:L.wake.state,
      calls:advWakeCalls(L) }
  },
  async 'adv-two-handoffs'() {
    const L = advLedger(); const { $ } = advHost(L)
    const second = { event_id:'f'.repeat(64), item_id:'n3', kind:'resume', summary:'second', question:'', waits:[],
      wake:{ state:'pending', revision:0, delivery_uncertain:false } }
    const base = L.call
    L.call = async (tool, args) => {
      if (tool === 'work_status') return text({ ...snapshot(), auto_continue:false, side_handoffs:[L.row(false), second] })
      if (tool === 'work_side_wake' && args.event_id === second.event_id) { L.calls.push('second:' + args.state); return text({ protocol:2, state:'already_intent' }) }
      return base(tool, args)
    }
    await advTurnStart($, 'T1')
    const s1 = await advStop($)
    return { first:advEvidence(s1?.block), firstIncludesSecond:String(s1?.block).includes('f'.repeat(64)),
      secondCalls:L.calls.filter(c => c.startsWith('second')) }
  },
  async 'adv-pane-retry'() {
    const L = advLedger()
    L.wake = { state:'uncertain', phase:'started', attempt_id:'7'.repeat(32), owner:'other', channel:'prompt', revision:6 }
    const { $, state } = advHost(L)
    const pane = await advPane($)
    const button = pane?.children?.find(r => r.label === 'Retry main wake…')
    if (!button) return { button:false }
    state.answer = 'Cancel'; await button.onPress()
    const afterCancel = L.retry || null
    state.answer = 'Retry main wake (may duplicate)'; await button.onPress()
    return { button:true, afterCancel, retry:L.retry, retryInternal:L.retryInternal === true, ref:attemptRef('7'.repeat(32)) }
  },
})

// Per-key idle prompts (side-intake.js sidePromptSubmit). The main-review E2E
// interleave (run-20261111-161947, second-claude-debug.txt 22:22:46.501 /
// 22:22:47.650) issued a second plugin prompt before the first started its
// turn. Each fake $.prompt.submit stays pending until the scenario releases it
// (its turn started), as the engine's does; the engine, not the module, holds
// the second (owned probes prompt-gate-0dd78bf3, prompt-registry-e2bed500).
// argv[3]: reviews | sides | review-side | side-review, then :drop or :throw
// applied to the first prompt, and :reverse to release the second first.
Object.assign(scenarios, {
  async 'prompt-keys'() {
    const [mode, ...flags] = String(process.argv[3] || 'reviews').split(':')
    const fault = flags.find(flag => flag === 'drop' || flag === 'throw')
    const reverse = flags.includes('reverse')
    const leaseIds = mode === 'reviews' ? ['L1', 'L2'] : mode === 'sides' ? [] : ['L1']
    const eventIds = (mode === 'sides' ? ['E1', 'E2'] : mode === 'reviews' ? [] : ['E1']).map(id => id + '0'.repeat(62))
    // L1: a review prompt (payload). E1: a side wake's appended payload.
    // wake:E1: the prompt that wakes main for it, which holds none.
    const label = value => (/"lease_id":"(L\d)"/.exec(value) || /"event_id":"(E\d)/.exec(value) || [])[1]
      || (/^Ciel side wake (E\d)/.exec(value) ? 'wake:' + /^Ciel side wake (E\d)/.exec(value)[1] : undefined)
      || (/^Ciel Work review wake (L\d)/.exec(value) ? /^Ciel Work review wake (L\d)/.exec(value)[1] : '?')
    const reviews = [], wakes = {}, records = [], submits = [], invocations = []
    let active = 0, maxActive = 0
    const reviewRow = id => ({ lease_id:id, item_id:'n' + id, item_title:'Result ' + id, state:'pending', decision:null,
      summary:'built ' + id, verification:'ok', wake:{ state:'pending', attempts:0 } })
    const sideRows = []
    const sideOwn = w => ({ protocol:2, state:w.state, phase:w.phase, attempt_id:w.attempt_id, owner_nonce:w.owner,
      channel:w.channel, revision:w.revision })
    const { $, state:hostState } = host(async (tool, args) => {
      if (tool === 'work_status') return text({ ...snapshot(), auto_continue:false })
      if (tool === 'work_parallel_status') return text({ version:9, active_device:0,
        policy:{ enabled:true, paused:false, priority_limits:{ P1:1, P2:0, P3:0, P4:0, P5:0 } }, leases:{},
        review:{ version:4, pending:reviews } })
      if (tool === 'work_parallel_event') {
        const row = reviews.find(r => r.lease_id === args.lease_id)
        if (args.event === 'review_wake' && args.operation === 'prepare') {
          if (!row || !['pending', 'failed'].includes(row.wake.state)) return { isError:true, ...text('review wake already submitted') }
          row.wake = { state:'submitting', attempts:row.wake.attempts + 1 }
          return text({ lease_id:row.lease_id, state:'submitting', attempt_id:'attempt-' + row.lease_id, finalization_version:5 })
        }
        if (args.event === 'review_wake' && args.operation === 'record') {
          row.wake = { ...row.wake, state:args.state }; records.push(row.lease_id + ':' + args.state)
          return text({ state:args.state })
        }
        if (args.event === 'reserve_next') return { isError:true, ...text('no eligible candidate') }
        return text({})
      }
      if (tool === 'work_side_claim') return text({ claim:null, settings:{}, handoff_replies:[],
        main_wakes:sideRows.filter(r => ['pending', 'failed'].includes(wakes[r.event_id].state))
          .map(r => ({ ...r, wake:{ ...wakes[r.event_id] } })) })
      if (tool === 'work_side_wake') {
        const w = wakes[args.event_id]
        if (args.state === 'prepare') {
          if (['pending', 'failed'].includes(w.state)) Object.assign(w, { state:'submitting', phase:'intent',
            attempt_id:label('"event_id":"' + args.event_id + '"').toLowerCase().repeat(16), owner:args.owner_nonce,
            channel:args.channel, revision:w.revision + 1 })
          return text(sideOwn(w))
        }
        if (args.state === 'start') { Object.assign(w, { phase:'started', revision:w.revision + 1 }); return text(sideOwn(w)) }
        if (String(args.state).startsWith('prompt_')) {
          if (args.state === 'prompt_prepare') {
            if (w.prompt && ['submitting','queued'].includes(w.prompt.state))
              return text({protocol:2,state:'already_'+w.prompt.state})
            w.prompt = {state:'submitting',attempt_id:'prompt-'+label('"event_id":"'+args.event_id+'"'),owner:args.owner_nonce}
            return text({protocol:2,state:'submitting',attempt_id:w.prompt.attempt_id,owner_nonce:args.owner_nonce})
          }
          w.prompt.state = args.state.slice(7)
          return text(sideOwn(w))
        }
        if (['queued', 'appended', 'uncertain', 'failed'].includes(args.state)) {
          Object.assign(w, { state:args.state, revision:w.revision + 1 })
          records.push(label('"event_id":"' + args.event_id + '"') + ':' + args.state)
          return text(sideOwn(w))
        }
        if (args.state === 'read') { records.push(label('"event_id":"' + args.event_id + '"') + ':read'); return text(sideOwn(w)) }
        return text({})
      }
      return text({})
    })
    $.session.repo = async () => null; $.session.root = async () => '/repo'
    $.prompt = { submit:args => {
      active++; maxActive = Math.max(maxActive, active)
      invocations.push(label(args.text))
      return new Promise((resolve, reject) => submits.push({ label:label(args.text), release:outcome => {
        active--
        if (outcome === 'throw') reject(new Error('submit outcome unknown'))
        else resolve(outcome === 'drop' ? { drop:'fixture' } : { text:args.text })
      } }))
    } }
    const trigger = async kind => {
      if (kind === 'review') {
        for (const id of leaseIds) reviews.push(reviewRow(id))
        await handlers['tool.call']($, { tool:'mcp__ciel-runtime-router__work_parallel_report', agentId:'worker' }, next({ ok:true }))
      } else {
        for (const id of eventIds) {
          wakes[id] = { state:'pending', revision:0 }
          sideRows.push({ event_id:id, item_id:'n7', root_id:'n7', title:'deadline', kind:'time_limit', summary:'report ' + id.slice(0, 2),
            question:'', waits:[], time_limit:{ text:'within 30 min', evidence:'m1' } })
        }
        await sidePump($)
      }
      await sleep(30)
    }
    const stop = async () => String((await handlers['classic.Stop']($, { session_id:'S1' }, next({})))?.block || '')
    const lines = block => ['L1', 'L2'].filter(id => block.includes('"lease_id":"' + id + '"'))
    await handlers['session.start']($, { isInteractive:false }, next(undefined))
    const order = mode === 'side-review' ? ['side', 'review'] : mode === 'review-side' ? ['review', 'side']
      : mode === 'sides' ? ['side'] : ['review']
    for (const kind of order) await trigger(kind)
    const keys = [...leaseIds.map(id => 'parallel:' + id), ...eventIds.map(id => 'wake:' + id)]
    // Each lease's delivery is a fire-and-forget chain (register.js
    // finishParallelWake via `void`); its append record and prompt land when
    // that chain does, in either order between keys. Wait for every key's own
    // record and prompt instead of assuming they fit in trigger's 30 ms.
    const producers = [...leaseIds, ...eventIds.map(id => id.slice(0, 2))]
    await keyUntil(() => keys.every(key => sidePromptPending(key))
      && producers.every(id => records.some(row => row.startsWith(id + ':'))))
    const whileBothPending = { invocations:[...invocations], maxActive, pending:keys.filter(key => sidePromptPending(key)).length,
      records:[...records], appended:(hostState.appended || []).map(row => label(row.message.content[0].text)) }
    const stopWhileBothPending = lines(await stop())
    // Release by producer order (first triggered key first), not by submit
    // index: independent keys may reach $.prompt.submit in either order.
    const releaseOrder = order.flatMap(kind => kind === 'review' ? leaseIds : eventIds.map(id => 'wake:' + id.slice(0, 2)))
    const byLabel = id => submits.find(entry => entry.label === id)
    const [firstOut, secondOut] = reverse ? [byLabel(releaseOrder[1]), byLabel(releaseOrder[0])]
      : [byLabel(releaseOrder[0]), byLabel(releaseOrder[1])]
    // Wait for the released key to settle in the prompt registry (async $.state
    // CAS) rather than a fixed delay: under suite load 30 ms was not enough.
    const settledTo = async count => { await keyUntil(() => keys.filter(key => sidePromptPending(key)).length <= count); await sleep(30) }
    firstOut?.release(reverse ? 'ok' : fault || 'ok'); await settledTo(keys.length - 1)
    const afterFirst = { invocations:[...invocations], records:[...records], pending:keys.filter(key => sidePromptPending(key)) }
    const stopAfterFirst = lines(await stop())
    secondOut?.release(reverse ? fault || 'ok' : 'ok'); await settledTo(keys.length - 2)
    const stopAfterBoth = lines(await stop())
    return { whileBothPending, stopWhileBothPending, afterFirst, stopAfterFirst, stopAfterBoth,
      invocations, maxActive, records, sideWakes:Object.fromEntries(Object.entries(wakes).map(([id, w]) => [id.slice(0, 2), w.state])) }
  },
  // A key already issued shares its exact Promise object and never submits twice.
  async 'prompt-key-dedup'() {
    const calls = []; let release
    const $ = { prompt:{ submit:args => { calls.push(args.text); return new Promise(resolve => { release = resolve }) } },
      clock:{ after(){ return { cancel(){} } } }, state:keyState().api }
    const a = sidePromptSubmit($, 'side:X', 'first')
    const b = sidePromptSubmit($, 'side:X', 'second')
    // The submit follows a $.state CAS round trip (four event-loop turns here).
    // A fixed 5 ms was too short on a loaded Linux container (2026-11-16:
    // "release is not a function" once in a full Docker run, 0 of 8 reruns).
    await keyUntil(() => typeof release === 'function')
    const pendingBefore = sidePromptPending('side:X')
    release({ text:'first' })
    const results = await Promise.all([a, b])
    return { calls, sameEntry:a === b, pendingBefore, pendingAfter:sidePromptPending('side:X'), results }
  },
})

// One cell of the 4x4 liveness table: path (review | side | retry) against
// condition (finite | anomaly | restart | reload). Each module instance is a
// fresh copy of register.js + side-intake.js (a new environment, as a hot
// reload or a new process gives). reload shares the host-held $.state with
// the first instance; restart starts from empty state. The fake backend keeps
// side_wake.py and finalization semantics, with real attempt_ref digests.
const KEY_E1 = 'e1' + 'a'.repeat(62)
const KEY_E2 = 'e2' + 'b'.repeat(62)
const keyTurn = () => new Promise(resolve => setImmediate(resolve))
async function keyUntil(test, ms = 3000) {
  const end = Date.now() + ms
  while (Date.now() < end) { if (await test()) return true; await sleep(5) }
  return false
}
function keyBackend() {
  const B = { reviews:[], wakes:{}, sideRows:[], records:[], calls:[], attempts:0 }
  const short = id => id === KEY_E1 ? 'E1' : id === KEY_E2 ? 'E2' : id
  B.pub = id => {
    const w = B.wakes[id], out = { state:w.state, revision:w.revision }
    if (w.phase) out.phase = w.phase
    if (w.channel) out.channel = w.channel
    if (w.attempt_id) out.attempt_ref = attemptRef(w.attempt_id)
    out.delivery_uncertain = w.state === 'uncertain' || (w.state === 'submitting' && w.phase === 'started')
    return out
  }
  B.row = (id, hostView) => ({ ...B.sideRows.find(r => r.event_id === id),
    wake:hostView ? { ...B.pub(id), attempt_id:B.wakes[id].attempt_id } : B.pub(id) })
  B.own = id => { const w = B.wakes[id]; return { protocol:2, state:w.state, phase:w.phase, attempt_id:w.attempt_id,
    owner_nonce:w.owner, channel:w.channel, revision:w.revision } }
  // pin: the router's read-time review_pin (option 1, never persisted).
  B.addReview = (id, pin) => B.reviews.push({ lease_id:id, item_id:'n' + id, item_title:'Result ' + id, state:'pending',
    decision:null, summary:'built ' + id, verification:'ok', wake:{ state:'pending', attempts:0 },
    ...(pin ? { review_pin:pin } : {}) })
  B.addSide = id => { B.wakes[id] = { state:'pending', revision:0 }
    B.sideRows.push({ event_id:id, item_id:'n7', root_id:'n7', title:'deadline', kind:'time_limit', summary:'report',
      question:'', waits:[], time_limit:{ text:'within 30 min', evidence:'m1' } }) }
  B.call = async (tool, args) => {
    if (tool === 'work_status') return text({ ...snapshot(), auto_continue:false,
      side_handoffs:B.sideRows.map(r => B.row(r.event_id, false)) })
    if (tool === 'work_parallel_status') return text({ version:9, active_device:0,
      policy:{ enabled:true, paused:false, priority_limits:{ P1:1, P2:0, P3:0, P4:0, P5:0 } }, leases:{},
      review:{ version:4, pending:B.reviews } })
    if (tool === 'work_side_status') return text({ enabled:true, side_inbox_receipts:[] })
    if (tool === 'work_parallel_event') {
      const row = B.reviews.find(r => r.lease_id === args.lease_id)
      if (args.event === 'review_wake' && args.operation === 'prepare') {
        if (!row || !['pending', 'failed'].includes(row.wake.state)) return { isError:true, ...text('review wake already submitted') }
        const attempt = 'attempt-' + row.lease_id + '-' + (row.wake.attempts + 1)
        row.wake = { state:'submitting', attempt_id:attempt, attempts:row.wake.attempts + 1 }
        return text({ lease_id:row.lease_id, state:'submitting', attempt_id:attempt, finalization_version:5 })
      }
      if (args.event === 'review_wake' && args.operation === 'record') {
        if (row.wake.state !== 'submitting' || row.wake.attempt_id !== args.attempt_id)
          return { isError:true, ...text('stale review wake attempt') }
        row.wake = { ...row.wake, state:args.state }; B.records.push(row.lease_id + ':' + args.state)
        return text({ state:args.state })
      }
      if (args.event === 'reserve_next') return { isError:true, ...text('no eligible candidate') }
      return text({})
    }
    // side_intake_mcp: an appended wake is offered until main has had a request after it.
    if (tool === 'work_side_claim') return text({ claim:null, settings:{}, handoff_replies:[],
      main_wakes:B.sideRows.filter(r => { const w = B.wakes[r.event_id]
        return ['pending', 'failed'].includes(w.state) || (w.state === 'submitting' && ['intent', 'started'].includes(w.phase))
          || (w.state === 'appended' && !w.read_at) })
        .map(r => B.row(r.event_id, true)) })
    if (tool !== 'work_side_wake') return text({})
    if (String(args.state).startsWith('review_')) {
      // Mirrors review_delivery.py (covered on the real code by test_review_delivery).
      const D = (B.reviewDelivery ||= {}), row = B.reviews.find(r => r.lease_id === args.event_id)
      const d = D[args.event_id] && row && D[args.event_id].attempt === row.wake.attempt_id ? D[args.event_id] : null
      B.calls.push(args.event_id + ':' + args.state)
      const answer = value => text({ protocol:2, lease_id:args.event_id, ...value })
      if (args.state === 'review_appended') {
        if (!row || row.wake.state !== 'appended' || row.wake.attempt_id !== args.attempt_id)
          return { isError:true, ...text('stale review wake attempt') }
        if (!d) D[args.event_id] = { attempt:args.attempt_id, conversation:args.seen_receipt }
        return answer({ state:'appended' })
      }
      if (args.state === 'review_status')
        return answer({ state:!d ? 'unknown' : d.read ? 'read' : d.conversation === args.seen_receipt ? 'here' : 'elsewhere' })
      if (args.state === 'review_read') { if (d) d.read = true; return answer({ state:d ? 'read' : 'unknown' }) }
      if (args.state === 'review_reappend_prepare') {
        if (!d) return answer({ state:'unknown' })
        if (d.read) return answer({ state:'already_read' })
        if (d.conversation === args.seen_receipt) return answer({ state:'already_appended' })
        if (d.lease && d.lease.conversation === args.seen_receipt && d.lease.state !== 'failed')
          return answer({ state:'already_' + d.lease.state })
        d.lease = { state:'submitting', attempt:'reappend-' + (B.reappends = (B.reappends || 0) + 1), owner:args.owner_nonce,
          conversation:args.seen_receipt }
        return answer({ state:'submitting', attempt_id:d.lease.attempt, owner_nonce:args.owner_nonce })
      }
      if (args.state === 'review_reappend_retry') {
        if (!d?.lease || d.lease.state !== 'uncertain') return { isError:true, ...text('review re-append changed; refresh before retry') }
        d.lease = { ...d.lease, state:'failed', released:true, attempt:'released-' + d.lease.attempt }
        return answer({ state:'released' })
      }
      const outcome = String(args.state).slice('review_reappend_'.length)
      if (!d?.lease || d.lease.attempt !== args.attempt_id || d.lease.owner !== args.owner_nonce)
        return { isError:true, ...text('stale review reappend attempt') }
      d.lease.state = outcome
      if (outcome === 'appended') { d.conversation = d.lease.conversation; delete d.lease }
      return answer({ state:outcome })
    }
    const w = B.wakes[args.event_id]
    B.calls.push(short(args.event_id) + ':' + args.state)
    if (String(args.state).startsWith('prompt_')) {
      if (args.state === 'prompt_prepare') {
        if (w.prompt && ['submitting','queued'].includes(w.prompt.state))
          return text({protocol:2,state:'already_'+w.prompt.state,revision:w.revision})
        w.prompt = {state:'submitting',attempt_id:'prompt-'+short(args.event_id),owner:args.owner_nonce}
        return text({protocol:2,state:'submitting',attempt_id:w.prompt.attempt_id,owner_nonce:args.owner_nonce,revision:w.revision})
      }
      if (!w.prompt || w.prompt.attempt_id !== args.attempt_id || w.prompt.owner !== args.owner_nonce)
        return {isError:true,...text('stale side wake prompt attempt')}
      w.prompt.state = args.state.slice(7)
      return text(B.own(args.event_id))
    }
    if (args.state === 'read') {
      if (w.state !== 'appended') return { isError:true, ...text('side wake is not appended') }
      if (!w.read_at) Object.assign(w, { read_at:1, revision:w.revision + 1 })
      return text(B.own(args.event_id))
    }
    if (args.state === 'prepare') {
      if (['pending', 'failed'].includes(w.state)) {
        Object.assign(w, { state:'submitting', phase:'intent', attempt_id:String(++B.attempts).repeat(32).slice(0, 32),
          owner:args.owner_nonce, channel:args.channel, revision:w.revision + 1 })
        return text(B.own(args.event_id))
      }
      if (w.state === 'submitting' && w.phase === 'intent' && w.owner === args.owner_nonce) return text(B.own(args.event_id))
      return text({ protocol:2, state:'already_' + (w.phase === 'intent' ? 'intent' : w.state), revision:w.revision })
    }
    if (args.state === 'start') {
      if (w.state !== 'submitting' || w.phase !== 'intent' || w.attempt_id !== args.attempt_id || w.owner !== args.owner_nonce)
        return { isError:true, ...text('stale side wake intent') }
      Object.assign(w, { phase:'started', revision:w.revision + 1 }); return text(B.own(args.event_id))
    }
    if (['queued', 'appended', 'uncertain', 'failed'].includes(args.state)) {
      if (w.state === args.state && w.attempt_id === args.attempt_id && w.owner === args.owner_nonce) return text(B.own(args.event_id))
      if (w.state !== 'submitting' || w.phase !== 'started' || w.attempt_id !== args.attempt_id || w.owner !== args.owner_nonce)
        return { isError:true, ...text('stale side wake attempt') }
      Object.assign(w, { state:args.state, revision:w.revision + 1 })
      B.records.push(short(args.event_id) + ':' + args.state)
      return text(B.own(args.event_id))
    }
    if (args.state === 'retry') {
      if (!(w.state === 'uncertain' || (w.state === 'submitting' && w.phase === 'started'))
          || attemptRef(w.attempt_id) !== args.attempt_ref || w.revision !== args.revision || args.confirmed !== true)
        return { isError:true, ...text('side wake changed; refresh before retry') }
      Object.assign(w, { state:'failed', phase:'retry', revision:w.revision + 1 })
      B.records.push(short(args.event_id) + ':retry')
      return text({ protocol:2, state:'failed', revision:w.revision })
    }
    return text({})
  }
  return B
}
function keyState() {
  const map = new Map()
  const box = { map, failing:false }
  box.api = {
    get:async ref => { await keyTurn(); if (box.failing) throw new Error('state unavailable'); const held = map.get(ref.plugin + '/' + ref.key)
      return { value:held ? structuredClone(held.value) : undefined, version:held?.version || 0 } },
    set:async (ref, value, options = {}) => {
      await keyTurn()
      if (box.failing) throw new Error('state unavailable')
      const name = ref.plugin + '/' + ref.key, held = map.get(name) || { version:0 }
      if (options.ifVersion !== undefined && options.ifVersion !== held.version) return { isSet:false, version:held.version }
      map.set(name, { value:structuredClone(value), version:held.version + 1 })
      return { isSet:true, version:held.version + 1 }
    } }
  const label = row => row.key.replace(KEY_E1, 'E1').replace(KEY_E2, 'E2')
  // Pending rows only; terminal rows are listed separately.
  box.keys = () => (map.get('ciel-work/promptPending')?.value || []).filter(row => row.status !== 'terminal').map(label)
  box.terminals = () => (map.get('ciel-work/promptPending')?.value || []).filter(row => row.status === 'terminal').map(row => label(row) + ':' + row.outcome)
  return box
}
function keyClock() {
  const timers = []
  return { timers,
    after(ms, fn) { const t = { ms, fn, cancelled:false, fired:false }; timers.push(t); return { cancel() { t.cancelled = true } } },
    live() { return timers.filter(t => t.ms === 5000 && !t.cancelled && !t.fired).length },
    async fire() {
      for (const t of timers.filter(t => t.ms === 5000 && !t.cancelled && !t.fired)) { t.fired = true; await t.fn() }
      await sleep(30)
    } }
}
async function keyInstance(tag) {
  const dir = new URL('./key-instance-' + process.pid + '-' + tag + '/', import.meta.url)
  mkdirSync(dir, { recursive:true })
  for (const file of ['register.js', 'side-intake.js', 'texts.generated.js']) copyFileSync(new URL('./' + file, import.meta.url), new URL(file, dir))
  const side = await import(new URL('side-intake.js', dir).href)
  const reg = await import(new URL('register.js', dir).href)
  const own = {}, ownSide = {}
  reg.register((name, filter, handler) => {
    if (filter?.command === 'btw_with_mcp' || filter?.requestId === 'ciel-side-intake') ownSide[name] = handler
    else own[name] = typeof filter === 'function' ? filter : handler
  })
  return { handlers:own, sideHandlers:ownSide, side }
}
function keyPrompts() {
  const calls = []
  return { calls, submit:args => new Promise((resolve, reject) => calls.push({ text:args.text,
    release:outcome => outcome === 'throw' ? reject(new Error('unknown')) : resolve(outcome === 'drop' ? { drop:'fixture' } : { text:args.text }) })) }
}
function keyHost(B, store, clock, prompts) {
  const { $, state } = host(B.call)
  $.session.repo = async () => null; $.session.root = async () => '/repo'
  $.state = store.api; $.clock = clock; $.prompt = { submit:prompts.submit }
  $.ui.ask = async (question, options) => { (state.asks ||= []).push(options); return state.answer || 'Cancel' }
  // Claude Code 2.1.323 (append-views-54579a2e): plain messages() omits
  // plugin-append rows. Only the API view merges their user text.
  $.session.messages = async options => options?.as === 'api'
    ? (state.appended || []).map(row => ({ role:'user', content:row.message?.content || [] }))
    : []
  return { $, state }
}
// The turn an entered wake prompt starts on an idle main: its turn.start text
// carries the unique Wake ref token. That token, never messageCount or the
// prompt's queued outcome, proves its first request was built after the row.
async function keyPromptTurn(inst, $, turnId, text = 'wake') {
  await inst.handlers['turn.start']($, { turnId, text:'The ciel-work plugin sent a message:\n' + text }, next({}))
  // Probe values are 7 then 12, neither the transcript nor request count.
  for await (const _ of inst.handlers['turn.step']($, { turnId, index:0, messageCount:7 }, engineStream)) { void _ }
  await inst.handlers['turn.complete']($, { turnId, reason:'answer', answer:'ok' }, next(undefined))
}
// review: Stop's decision gate shows L1. uncertain: Stop's delivery-uncertain
// notice names E1 (shown once per attempt revision). retry: the pane offers the
// may-duplicate retry for E1.
async function keyPane(inst, $) {
  $.ui.resolve = () => ({ Box:p => p, Text:p => p, Button:p => p })
  await inst.handlers['command.run']($, { args:'status' })
  const flat = []
  const walk = node => { if (!node || typeof node !== 'object') return; flat.push(node); for (const child of [].concat(node.children || [])) walk(child) }
  walk(await inst.sideHandlers['ui.render']($, { component:'Pane', requestId:'ciel-side-intake' }, next(null)))
  return flat.find(node => node.label === 'Retry main wake…') || null
}
async function keyView(inst, $) {
  const block = String((await inst.handlers['classic.Stop']($, { session_id:'S1' }, next({})))?.block || '')
  return { review:block.includes('"lease_id":"L1"'),
    uncertain:block.includes('delivery to main is uncertain') && block.includes(KEY_E1),
    retry:Boolean(await keyPane(inst, $)) }
}
Object.assign(scenarios, {
  // Item 1, compat review. A pending review row without wake.state is an OLDER
  // router's projection: the host has only its lifecycle Set and the prompt
  // registry for it, so a fresh process (new module copy, empty $.state) issues
  // the review prompt again. A row of the current backend carries wake.state;
  // its durable attempt is refused there. argv[3]: old | current.
  async 'compat-review-state-loss'() {
    const old = process.argv[3] === 'old'
    const B = keyBackend()
    B.addReview('L1')
    if (old) delete B.reviews[0].wake
    const prepares = []
    const call = async (tool, args) => {
      if (tool === 'work_parallel_event' && args.event === 'review_wake' && args.operation === 'prepare') {
        const answer = await B.call(tool, args)
        prepares.push(answer.isError ? 'refused' : 'granted')
        return answer
      }
      return B.call(tool, args)
    }
    const rawSubmits = []
    for (const tag of ['compat-1', 'compat-2']) {
      const inst = await keyInstance(tag), prompts = keyPrompts()
      const { $ } = keyHost({ call }, keyState(), keyClock(), prompts)
      await inst.handlers['session.start']($, { isInteractive:false }, next(undefined))
      await inst.handlers['turn.complete']($, {}, next(undefined))
      await sleep(30)
      for (const prompt of prompts.calls) prompt.release('queued')
      await sleep(30)
      rawSubmits.push(prompts.calls.length)
    }
    return { projection:old ? 'old' : 'current', rawSubmits, prepares, records:B.records,
      wake:old ? null : B.reviews[0].wake.state }
  },
  async 'prompt-key-cell'() {
    const [path, condition] = String(process.argv[3] || 'review:finite').split(':')
    const B = keyBackend(), store = keyState()
    const one = await keyInstance('1'), c1 = keyClock(), p1 = keyPrompts()
    const { $:$1, state:s1 } = keyHost(B, store, c1, p1)
    await one.handlers['session.start']($1, { isInteractive:false }, next(undefined))
    if (path === 'review') {
      B.addReview('L1')
      await one.handlers['tool.call']($1, { tool:'mcp__ciel-runtime-router__work_parallel_report', agentId:'worker' }, next({ ok:true }))
    } else {
      B.addSide(KEY_E1)
      if (path === 'retry') {
        // An earlier attempt was released as uncertain; the person confirms a
        // may-duplicate retry from the pane, and the pump issues a new attempt.
        Object.assign(B.wakes[KEY_E1], { state:'uncertain', phase:'started', attempt_id:'9'.repeat(32), owner:'gone',
          channel:'prompt', revision:5 })
        const button = await keyPane(one, $1)
        if (!button) return { error:'no retry offered before the retry' }
        s1.answer = 'Retry main wake (may duplicate)'
        await button.onPress()
      } else await one.side.pollSide($1, 'claude:S1')
    }
    await keyUntil(() => p1.calls.length >= 1)
    if (path !== 'review') await keyUntil(() => B.records.some(row => row.endsWith(':appended')))
    const issued = { submits:p1.calls.length, registry:store.keys(), live:c1.live(),
      payloadPrompts:p1.calls.filter(call => call.text.includes('requires main action: {')).length,
      appended:(s1.appended || []).filter(row => row.message.content[0].text.includes(KEY_E1)).length,
      attempt:path === 'review' ? B.reviews[0].wake.attempt_id : B.wakes[KEY_E1].attempt_id }
    const before = await keyView(one, $1)
    const settled = state => keyUntil(() => B.records.some(row => row.endsWith(':' + state)))
    let after = null, second = null
    if (condition === 'finite') {
      // A review prompt records queued. A wake prompt that entered starts main's
      // turn; that turn's first step, not the prompt outcome, records the read.
      p1.calls[0]?.release('ok')
      if (path === 'review') await settled('queued')
      else { await keyUntil(() => B.calls.includes('E1:prompt_queued')); await keyPromptTurn(one, $1, 'P1', p1.calls[0]?.text)
        await keyUntil(() => B.calls.includes('E1:read')) }
      after = await keyView(one, $1)
    } else if (condition === 'anomaly') {
      // No main turn runs: the idle window releases the key.
      await c1.fire(); await (path === 'review' ? settled('uncertain') : sleep(60))
      after = await keyView(one, $1)
      // The engine prompt is never cancelled; its late resolution changes nothing.
      p1.calls[0]?.release('ok'); await sleep(100)
      second = { lateRecords:[...B.records], registry:store.keys() }
    } else {
      // A second module instance: reload keeps $.state, restart does not.
      const two = await keyInstance('2'), c2 = keyClock(), p2 = keyPrompts()
      const { $:$2 } = keyHost(B, condition === 'reload' ? store : keyState(), c2, p2)
      await two.handlers['session.start']($2, { isInteractive:false }, next(undefined))
      if (path !== 'review') await two.side.pollSide($2, 'claude:S1')
      if (path === 'review') await two.handlers['tool.call']($2, { tool:'mcp__ciel-runtime-router__work_parallel_report', agentId:'worker' }, next({ ok:true }))
      await sleep(100)
      const key = path === 'review' ? 'parallel:L1' : 'wake:' + KEY_E1
      const hydrated = { pending:two.side.sidePromptPending(key), live:c2.live() }
      const secondBefore = await keyView(two, $2)
      await c2.fire()
      if (condition === 'reload') await keyUntil(() => !store.keys().length)
      const secondAfter = await keyView(two, $2)
      const registryAfterWindow = condition === 'reload' ? store.keys() : null
      // After a reload the first environment's prompt can still resolve late
      // (it outlived the reload while one of its hooks ran:
      // prompt-registry-e2bed500). A restarted process cannot.
      if (condition === 'reload') { p1.calls[0]?.release('ok'); await (path === 'review' ? settled('queued') : sleep(60)) }
      second = { hydrated, before:secondBefore, after:secondAfter, submits:p2.calls.length, registryAfterWindow,
        payloadPrompts:p2.calls.filter(call => call.text.includes('requires main action: {')).length,
        lateRecords:[...B.records] }
    }
    return { path, condition, issued, before, after, second, records:[...B.records], registry:store.keys(),
      sideCalls:B.calls, review:B.reviews[0]?.wake || null, side:B.wakes[KEY_E1] ? { ...B.pub(KEY_E1) } : null }
  },
  // A never-settling review prompt L1 blocks neither a side wake (E1: payload
  // appended at once, its wake prompt enters) nor another review (L2). After
  // L1's idle window it is recorded uncertain and Stop's decision gate shows
  // it. No window runs during a main turn; turn.complete arms a full one.
  async 'prompt-key-latent'() {
    const B = keyBackend(), store = keyState()
    const one = await keyInstance('1'), c1 = keyClock(), p1 = keyPrompts()
    const { $, state } = keyHost(B, store, c1, p1)
    const report = () => one.handlers['tool.call']($, { tool:'mcp__ciel-runtime-router__work_parallel_report', agentId:'worker' }, next({ ok:true }))
    await one.handlers['session.start']($, { isInteractive:false }, next(undefined))
    B.addReview('L1')
    await report()
    await keyUntil(() => p1.calls.length === 1)
    B.addSide(KEY_E1); B.addReview('L2')
    await one.side.pollSide($, 'claude:S1')
    await report()
    await keyUntil(() => p1.calls.length === 3)
    const label = t => t.includes('"lease_id":"L1"') || t.startsWith('Ciel Work review wake L1') ? 'L1'
      : t.includes('"lease_id":"L2"') || t.startsWith('Ciel Work review wake L2') ? 'L2'
      : t.startsWith('Ciel side wake') ? 'wake' : '?'
    const issued = p1.calls.map(call => label(call.text))
    const registryWhileAll = store.keys()
    const appendedWhileAll = (state.appended || []).filter(row => row.message.content[0].text.includes(KEY_E1)).length
    for (const call of p1.calls) if (label(call.text) !== 'L1') call.release('ok')
    await keyUntil(() => B.records.includes('L2:queued') && B.calls.includes('E1:read'))
    const whileLatent = { records:[...B.records], registry:store.keys(), pendingL1:one.side.sidePromptPending('parallel:L1'),
      view:await keyView(one, $) }
    await one.handlers['turn.start']($, { turnId:'T1', text:'' }, next(undefined))
    const liveInTurn = c1.live()
    await c1.fire()
    const afterFireInTurn = { records:[...B.records], pendingL1:one.side.sidePromptPending('parallel:L1') }
    await one.handlers['turn.complete']($, { turnId:'T1', reason:'answer', answer:'x' }, next(undefined))
    const liveAfterTurn = c1.live()
    await c1.fire()
    await keyUntil(() => B.records.includes('L1:uncertain'))
    return { issued, registryWhileAll, appendedWhileAll, whileLatent, liveInTurn, afterFireInTurn, liveAfterTurn,
      afterWindow:{ records:[...B.records], registry:store.keys(), pendingL1:one.side.sidePromptPending('parallel:L1'),
        view:await keyView(one, $) } }
  },
})

// /clear while a raw prompt is unresolved (real register.js + side-intake.js).
// /clear resets $.state and keeps the module loaded; the same instance then
// sees classic.SessionStart (owned probe nonce-lifetime-probe-44e40778, the
// reset and the hook are measured; this re-claim path is harness-only). A hot
// reload follows. argv[3]: review|side, then reassert|skip|fails|end. end:
// session.end runs first (whether /clear raises it is not measured): it must
// not drop this module's prompt entries before the re-claim.
Object.assign(scenarios, {
  async 'prompt-key-clear'() {
    const [path, variant] = String(process.argv[3] || 'review:reassert').split(':')
    const B = keyBackend(), store = keyState()
    const one = await keyInstance('1'), c1 = keyClock(), p1 = keyPrompts()
    const { $:$1 } = keyHost(B, store, c1, p1)
    await one.handlers['session.start']($1, { isInteractive:false }, next(undefined))
    if (path === 'review') {
      B.addReview('L1')
      await one.handlers['tool.call']($1, { tool:'mcp__ciel-runtime-router__work_parallel_report', agentId:'worker' }, next({ ok:true }))
    } else {
      B.addSide(KEY_E1)
      await one.side.pollSide($1, 'claude:S1')
    }
    await keyUntil(() => p1.calls.length >= 1)
    const key = path === 'review' ? 'parallel:L1' : 'side:' + KEY_E1
    const generation = (store.map.get('ciel-work/promptPending')?.value || []).find(row => row.status !== 'terminal')?.generation
    if (variant === 'end') await one.handlers['session.end']($1, {}, next(undefined))
    store.map.clear()
    if (variant === 'fails') store.failing = true
    if (variant !== 'skip') await one.handlers['classic.SessionStart']($1, { source:'clear', hook_event_name:'SessionStart' }, next({}))
    store.failing = false
    const rows = store.map.get('ciel-work/promptPending')?.value || []
    const afterClear = { registry:store.keys(), sameGeneration:rows[0]?.generation === generation,
      pendingInFirst:one.side.sidePromptPending(key) }
    // Hot reload in the same process: $.state as left by the /clear boundary.
    const two = await keyInstance('2'), c2 = keyClock(), p2 = keyPrompts()
    const { $:$2 } = keyHost(B, store, c2, p2)
    await two.handlers['session.start']($2, { isInteractive:false }, next(undefined))
    if (path === 'review') await two.handlers['tool.call']($2, { tool:'mcp__ciel-runtime-router__work_parallel_report', agentId:'worker' }, next({ ok:true }))
    else await two.side.pollSide($2, 'claude:S1')
    await sleep(100)
    const reload = { hydrated:two.side.sidePromptPending(key), rawSubmits:p2.calls.length, view:await keyView(two, $2) }
    await c2.fire()
    const afterWindow = await keyView(two, $2)
    return { path, variant, afterClear, reload, afterWindow, firstRawSubmits:p1.calls.length, records:[...B.records] }
  },
})

// How many times one side event's payload is presented to main (real
// register.js + side-intake.js). One conversation is shared by every module
// instance of the process: a row is stored by $.session.append at once, and a
// plugin prompt is stored when it enters; a held prompt enters later and is
// never withdrawn (owned probes delivery-4f4c90b8, prompt-registry-e2bed500).
// A presentation is a stored row holding the wake payload, or a Stop block that
// holds the event's data ("Advice data"). A Stop reminder that only names the
// event is listed apart. argv[3]: anomaly | retry | clear-skip | clear-fails.
const SSE_WAKE = 'Ciel side event requires main action: {"event_id":"' + KEY_E1
function sseConversation() {
  const C = { rows:[], held:[], stopBlocks:[], stopReminders:0 }
  C.append = async args => { C.rows.push({ kind:'append', text:args.message.content[0].text }); return { uuid:'u' + C.rows.length } }
  C.submit = args => new Promise(resolve => C.held.push({ text:args.text, entered:false,
    enter() { if (this.entered) return; this.entered = true; C.rows.push({ kind:'prompt', text:args.text }); resolve({ text:args.text }) } }))
  C.wire = $ => { $.session.append = C.append; $.session.messages = async options => options?.as === 'api'
    ? C.rows.filter(row => row.kind === 'append').map(row => ({role:'user',content:[{type:'text',text:row.text}]}))
    : C.rows.filter(row => row.kind !== 'append').map(row => ({role:'user',text:row.text,toolUses:[]})); $.prompt = { submit:C.submit } }
  C.enterAll = () => { for (const held of C.held) held.enter() }
  C.view = () => ({ payloadRows:C.rows.filter(row => row.text.includes(SSE_WAKE)).map(row => row.kind),
    plainPrompts:C.rows.filter(row => row.kind === 'prompt' && !row.text.includes(SSE_WAKE)).length,
    heldPayloadPrompts:C.held.filter(held => !held.entered && held.text.includes(SSE_WAKE)).length,
    stopBlocks:[...C.stopBlocks], stopReminders:C.stopReminders })
  return C
}
async function sseTurn(inst, $, C, turnId, duringFirstStep = null) {
  const stop = async extra => {
    const block = String((await inst.handlers['classic.Stop']($, { session_id:'S1', ...extra }, next({})))?.block || '')
    if (block.includes(KEY_E1) && block.includes('Advice data: '))
      C.stopBlocks.push(block.includes('delivery to main is uncertain') ? 'uncertain' : 'advice')
    else if (block.includes(KEY_E1)) C.stopReminders++
    return block
  }
  const step = async index => { for await (const _ of inst.handlers['turn.step']($, { turnId, index, messageCount:index ? 12 : 7 }, engineStream)) { void _ } }
  await inst.handlers['turn.start']($, { turnId, text:'go' }, next({}))
  await step(0)
  // What happens while the model streams the answer of this (last) request.
  if (duringFirstStep) await duringFirstStep()
  let block = await stop({})
  for (let index = 1; block && index < 4; index++) { await step(index); block = await stop({ stop_hook_active:true }) }
  await inst.handlers['turn.complete']($, { turnId, reason:'answer', answer:'x' }, next(undefined))
  await sleep(30)
}
Object.assign(scenarios, {
  async 'sse-reads'() {
    const variant = process.argv[3] || 'anomaly'
    const B = keyBackend(), store = keyState(), C = sseConversation()
    const one = await keyInstance('1'), c1 = keyClock()
    const { $:$1, state:s1 } = keyHost(B, store, c1, keyPrompts())
    C.wire($1)
    await one.handlers['session.start']($1, { isInteractive:false }, next(undefined))
    B.addSide(KEY_E1)
    await one.side.pollSide($1, 'claude:S1')
    await keyUntil(() => C.held.length >= 1)
    await sleep(40)
    const issued = { ...C.view(), wake:B.wakes[KEY_E1].state }
    let two = null, $2 = null, retryOffered = null
    if (variant === 'anomaly' || variant === 'retry') {
      // Main stays idle: the 5 s idle window of the held prompt elapses.
      later(5000); await c1.fire(); await sleep(60)
    } else {
      // /clear resets $.state; the re-claim is missing (skip) or fails.
      store.map.clear()
      if (variant === 'clear-fails') {
        store.failing = true
        await one.handlers['classic.SessionStart']($1, { source:'clear', hook_event_name:'SessionStart' }, next({}))
        store.failing = false
      }
      two = await keyInstance('2')
      $2 = keyHost(B, store, keyClock(), keyPrompts()).$
      C.wire($2)
      await two.handlers['session.start']($2, { isInteractive:false }, next(undefined))
      await two.side.pollSide($2, 'claude:S1'); await sleep(60)
      // The person's own turn below starts some seconds after the reload.
      later()
    }
    const afterRelease = { ...C.view(), wake:B.wakes[KEY_E1].state }
    const inst = two || one, $ = $2 || $1
    if (variant === 'retry') {
      // A person confirms the may-duplicate retry, if the pane offers one.
      const button = await keyPane(one, $1)
      retryOffered = Boolean(button)
      if (button) {
        s1.answer = 'Retry main wake (may duplicate)'
        const before = C.held.length
        await button.onPress()
        await keyUntil(() => C.held.length > before, 500)
        // The retried attempt's prompt enters first, then the old held one.
        for (const held of C.held.slice(before)) held.enter()
        await sleep(40)
      }
    } else {
      // A person's own turn runs before the held prompt enters.
      await sseTurn(inst, $, C, 'T1')
    }
    C.enterAll(); await sleep(60)
    await sseTurn(inst, $, C, 'T2')
    const view = C.view()
    return { variant, issued, afterRelease, retryOffered, final:{ ...view, wake:B.wakes[KEY_E1].state },
      presentations:view.payloadRows.length + view.stopBlocks.length, records:[...B.records] }
  },
  // Item 7: main is in the last request of a turn when the wake is offered. The
  // payload is appended into the running turn, but no request of the turn
  // follows by itself. The Stop reminder continues the turn so its next
  // request carries the row; the reminder itself holds no event data.
  async 'sse-busy-final-step'() {
    const B = keyBackend(), store = keyState(), C = sseConversation()
    const one = await keyInstance('1')
    const { $ } = keyHost(B, store, keyClock(), keyPrompts())
    C.wire($)
    await one.handlers['session.start']($, { isInteractive:false }, next(undefined))
    let whileTurnRuns = null
    await sseTurn(one, $, C, 'T1', async () => {
      B.addSide(KEY_E1)
      await one.side.pollSide($, 'claude:S1'); await sleep(60)
      whileTurnRuns = { ...C.view(), held:C.held.length, wake:B.wakes[KEY_E1].state }
    })
    const view = C.view()
    return { whileTurnRuns, final:{ ...view, held:C.held.length, wake:B.wakes[KEY_E1].state },
      presentations:view.payloadRows.length + view.stopBlocks.length, sideCalls:[...B.calls] }
  },
  // Item 4: a Stop elected the wake, then a new main turn overtook it, so the
  // Stop returned no block. Nobody acts; only the pump runs.
  async 'sse-superseded-stop'() {
    const L = advLedger(); const { $, state } = advHost(L)
    let release; const gate = new Promise(r => { release = r })
    const base = L.call
    L.call = async (tool, args) => { if (tool === 'work_side_wake' && args.state === 'start') await gate; return base(tool, args) }
    await advTurnStart($, 'T1')
    const pending = advStop($)
    await sleep(20)
    await advTurnStart($, 'T2')
    release()
    const s1 = await pending
    await advStep($, 'T2', 0)
    for (let i = 0; i < 3; i++) { await sidePump($); await sleep(20) }
    const delivered = () => state.prompts.filter(t => t.includes(ADV_EVENT)).length + state.appends.filter(t => t.includes(ADV_EVENT)).length
    const whileT2Runs = { delivered:delivered(), appends:state.appends.length, wake:L.wake.state + '/' + L.wake.phase }
    // T2's next request follows its tool call, seconds later: it carries the row.
    later()
    await advStep($, 'T2', 1)
    const s2 = await advStop($)
    await advTurnComplete($, 'T2'); await advTurnStart($, 'T3')
    const s3 = await advStop($)
    return { staleStopReturnedBlock:advEvidence(s1?.block), whileT2Runs, delivered:delivered(),
      wake:L.wake.state + '/' + L.wake.phase, attempts:L.attempts,
      laterUncertain:[s2, s3].some(s => String(s?.block || '').includes('delivery to main is uncertain')),
      // Blocks that hold the event's data, and reminders that only name it
      // (this fixture's main never acknowledges, so it is reminded once).
      laterBlocks:[s2, s3].filter(s => advEvidence(s?.block) && String(s.block).includes('Advice data: ')).length,
      laterReminders:[s2, s3].filter(s => advEvidence(s?.block) && String(s.block).includes('holds no event data')).length }
  },
})

// Item G: a review payload appended in conversation S1 must reach S2 (/clear,
// --continue) exactly once, via the review_delivery sidecar, never via the
// frozen schema-5 journal. A second process in S2 adds nothing; S2's exact
// messageCount step records the read.
Object.assign(scenarios, {
  async 'review-conversation-change'() {
    const B = keyBackend(), payload = row => row.message.content[0].text.startsWith('Ciel Work result is awaiting main review')
    const report = (inst, $) => inst.handlers['tool.call']($, { tool:'mcp__ciel-runtime-router__work_parallel_report', agentId:'worker' }, next({ ok:true }))
    const one = await keyInstance('rc1'), p1 = keyPrompts()
    const { $:$1, state:s1 } = keyHost(B, keyState(), keyClock(), p1)
    await one.handlers['session.start']($1, { isInteractive:false }, next(undefined))
    B.addReview('L1')
    await report(one, $1)
    await keyUntil(() => B.calls.includes('L1:review_appended'))
    p1.calls[0]?.release('ok'); await sleep(40)
    const inS1 = { appends:(s1.appended || []).filter(payload).length, prompts:p1.calls.length, wake:B.reviews[0].wake.state }
    // The conversation changes: a new process (fresh $.state) in S2.
    const two = await keyInstance('rc2'), p2 = keyPrompts()
    const { $:$2, state:s2 } = keyHost(B, keyState(), keyClock(), p2)
    s2.session = 'S2'
    await two.handlers['session.start']($2, { isInteractive:false }, next(undefined))
    await report(two, $2); await sleep(60)
    await report(two, $2); await sleep(60)
    const inS2 = { appends:(s2.appended || []).filter(payload).length, prompts:p2.calls.length,
      promptHasPayload:p2.calls.some(call => call.text.includes('"lease_id"')) }
    // Another process already in S2 after the re-append: nothing more.
    const three = await keyInstance('rc3'), p3 = keyPrompts()
    const { $:$3, state:s3 } = keyHost(B, keyState(), keyClock(), p3)
    s3.session = 'S2'
    await three.handlers['session.start']($3, { isInteractive:false }, next(undefined))
    await report(three, $3); await sleep(60)
    const thirdProcess = { appends:(s3.appended || []).filter(payload).length, prompts:p3.calls.length }
    // S2's wake prompt enters; its turn's first step carries the re-appended row.
    p2.calls[0]?.release('ok'); await sleep(20)
    await keyPromptTurn(two, $2, 'R2', p2.calls[0]?.text)
    await keyUntil(() => B.calls.includes('L1:review_read'))
    return { inS1, inS2, thirdProcess, reviewCalls:B.calls.filter(call => call.startsWith('L1:')),
      prepares:B.records.length, journal:B.reviews[0].wake }
  },
})

// A review re-append into S2 whose append threw (no stored row, no observation:
// uncertain). A person releases it elsewhere (web/REST/console), not through
// this module. The running S2 host must ask again and re-append exactly once.
Object.assign(scenarios, {
  async 'review-reappend-released-elsewhere'() {
    const B = keyBackend(), payload = row => row.message.content[0].text.startsWith('Ciel Work result is awaiting main review')
    const report = (inst, $) => inst.handlers['tool.call']($, { tool:'mcp__ciel-runtime-router__work_parallel_report', agentId:'worker' }, next({ ok:true }))
    const one = await keyInstance('rr1'), p1 = keyPrompts()
    const { $:$1 } = keyHost(B, keyState(), keyClock(), p1)
    await one.handlers['session.start']($1, { isInteractive:false }, next(undefined))
    B.addReview('L1')
    await report(one, $1)
    await keyUntil(() => B.calls.includes('L1:review_appended'))
    p1.calls[0]?.release('ok'); await sleep(40)
    const two = await keyInstance('rr2'), p2 = keyPrompts()
    const { $:$2, state:s2 } = keyHost(B, keyState(), keyClock(), p2)
    s2.session = 'S2'
    let throws = 1
    const append = $2.session.append
    $2.session.append = async args => { if (payload(args) && throws > 0) { throws--; throw new Error('transport lost') } return append(args) }
    await two.handlers['session.start']($2, { isInteractive:false }, next(undefined))
    await report(two, $2); await sleep(60)
    const stuck = { appends:(s2.appended || []).filter(payload).length, lease:B.reviewDelivery?.L1?.lease?.state || null }
    await report(two, $2); await sleep(60)
    const prepares = () => B.calls.filter(call => call === 'L1:review_reappend_prepare').length
    const beforeRelease = { prepares:prepares() }
    // The person's release, outside this module (operator controller).
    await B.call('work_side_wake', { event_id:'L1', state:'review_reappend_retry' })
    await report(two, $2); await sleep(60)
    const releasedBeforeRecheck = { appends:(s2.appended || []).filter(payload).length, prepares:prepares() }
    later(31000)
    await report(two, $2); await sleep(60)
    await report(two, $2); await sleep(60)
    return { stuck, beforeRelease, releasedBeforeRecheck,
      after:{ appends:(s2.appended || []).filter(payload).length, prepares:prepares(),
        reappended:B.calls.filter(call => call === 'L1:review_reappend_appended').length } }
  },
})

// The causal read proof in the exact order of owned probe append-views-54579a2e
// (Claude Code 2.1.323), through the real register.js + side-intake.js:
//  T  appended from the pump while main is idle; its wake prompt starts the turn
//     (L2-L5: request 1 carries T);
//  S  appended while step 0's dispatch runs (L6/L7: absent from request 1,
//     present in request 2);
//  B  appended while a Stop dispatch runs, beneath this module's Stop hook
//     (L9/L10: carried by the continuation request 2);
//  then step 1, and the turn ends.
// Plain messages() lists no append and messageCount is 7 then 12, matching no
// count (L5, L11). argv[3]: probe (another plugin's Stop beneath this module's
// one blocks, as the probe's did) | interrupted (step 1 is a tool-result
// continuation with no Stop between the steps, and the person interrupts the
// turn after it: no Stop runs before turn.complete). A Stop that returns a
// block continues the turn with another step, as the engine does.
const KEY_E3 = 'e3' + 'c'.repeat(62)
Object.assign(scenarios, {
  async 'causal-probe-order'() {
    const variant = process.argv[3] || 'probe'
    const B = keyBackend(), store = keyState(), clock = keyClock(), prompts = keyPrompts()
    let afterStop = null
    const inst = await keyInstance('po-' + variant)
    const { $, state } = keyHost(B, store, clock, prompts)
    await inst.handlers['session.start']($, { isInteractive:false }, next(undefined))
    const label = id => id === KEY_E1 ? 'T' : id === KEY_E2 ? 'S' : id === KEY_E3 ? 'B' : '?'
    // Events whose read_at was recorded, in first-record order (a re-offered
    // appended row may record the same read again; it is idempotent).
    const reads = () => [...new Set(B.calls.filter(call => call.endsWith(':read')).map(call => label(
      call.startsWith('E1') ? KEY_E1 : call.startsWith('E2') ? KEY_E2 : call.slice(0, 64))))]
    const wakePrompts = () => prompts.calls.filter(call => call.text.startsWith('Ciel side wake')).length
    const appended = id => (state.appended || []).filter(row => row.message.content[0].text.includes(id)).length
    // A side event decided while main is busy: the pump appends it at once.
    const shortId = id => id === KEY_E1 ? 'E1' : id === KEY_E2 ? 'E2' : id
    const busyEvent = async id => { B.addSide(id); await inst.side.pollSide($, 'claude:S1')
      await keyUntil(() => B.records.includes(shortId(id) + ':appended')) }
    // T: idle append, then one token-bearing wake prompt.
    B.addSide(KEY_E1)
    await inst.side.pollSide($, 'claude:S1')
    await keyUntil(() => prompts.calls.length >= 1 && B.records.includes('E1:appended'))
    prompts.calls[0].release('ok')
    await keyUntil(() => B.calls.includes('E1:prompt_queued'))
    const issued = { T:appended(KEY_E1), wakePrompts:wakePrompts() }
    // The turn that prompt started.
    await inst.handlers['turn.start']($, { turnId:'P', text:'The ciel-work plugin sent a message:\n' + prompts.calls[0].text }, next({}))
    const step = async (index, during = null) => {
      const stream = async function* () { if (during) await during(); yield { kind:'engine' } }
      for await (const _ of inst.handlers['turn.step']($, { turnId:'P', index, messageCount:index ? 12 : 7 }, stream)) { void _ }
    }
    await step(0, () => busyEvent(KEY_E2))
    const afterStep0 = { reads:reads(), S:appended(KEY_E2) }
    if (variant === 'probe') {
      // Another plugin's Stop hook beneath this module's runs (B is stored in
      // its dispatch) and returns the continuation block.
      const beneath = async () => { await busyEvent(KEY_E3); return { block:'APPEND-VIEWS-CONTINUE' } }
      beneath.origin = { plugin:'append-views-probe' }
      const stop = await inst.handlers['classic.Stop']($, { session_id:'S1' }, beneath)
      afterStop = { block:Boolean(stop?.block), reads:reads(), B:appended(KEY_E3) }
      await step(1)
    } else {
      // Tool-result continuation: no Stop between the two steps.
      await step(1, () => busyEvent(KEY_E3))
    }
    const afterStep1 = { reads:reads() }
    const stops = []
    if (variant === 'interrupted') {
      await inst.handlers['turn.complete']($, { turnId:'P', reason:'aborted', answer:'' }, next(undefined))
    } else {
      // This module's own Stop: each block continues the turn with one more step.
      for (let index = 2; index < 6; index++) {
        const stop = await inst.handlers['classic.Stop']($, { session_id:'S1', stop_hook_active:true }, next({}))
        stops.push(String(stop?.block || '').slice(0, 60))
        if (!stop?.block) break
        await step(index)
      }
      await inst.handlers['turn.complete']($, { turnId:'P', reason:'answer', answer:'ok' }, next(undefined))
    }
    await sleep(80)
    return { variant, issued, afterStep0, afterStop, afterStep1, stops,
      afterTurn:{ reads:reads(), wakePromptsAfterTurn:wakePrompts() - issued.wakePrompts,
        promptPrepares:B.calls.filter(call => call.endsWith(':prompt_prepare')).length } }
  },
})

// Host-only side tools as a main model (engine next, no agentId) or a Work
// worker would call them, with the arguments a model could write; the same
// tool from this module's own code; and main's own acknowledgment tool.
Object.assign(scenarios, {
  async 'host-only-side-tools'() {
    const routed = []
    const { $ } = host(async (tool, args) => { routed.push(tool); return text({ ok:true, tool }) })
    const tools = ['work_side_wake', 'work_side_fail', 'work_side_fallback', 'work_side_renew', 'work_side_ack',
      'work_side_understanding', 'work_side_reply', 'work_side_claim', 'work_side_retry_reply']
    const args = { event_id:'e'.repeat(64), receipt_id:'e'.repeat(64), state:'notice_retry', confirmed:true,
      expected_state:'uncertain', session_id:'claude:S1', _host:{ internal:true, token:'guess' } }
    const run = async (tool, extra, origin) => {
      let ran = false
      const engine = async e => { ran = true; return { result:'engine-ran' } }
      engine.origin = { plugin:origin }
      const answer = await handlers['tool.call']($, { tool:'mcp__ciel-runtime-router__' + tool, ...args, ...extra }, engine)
      return { tool, deny:typeof answer?.deny === 'string' ? answer.deny : null, engineRan:ran }
    }
    const main = [], worker = []
    for (const tool of tools) main.push(await run(tool, {}, 'engine'))
    for (const tool of tools) worker.push(await run(tool, { agentId:'worker-1' }, 'engine'))
    const own = await run('work_side_wake', {}, 'ciel-work')
    const before = routed.length
    const handoff = await handlers['tool.call']($, { tool:'mcp__ciel-runtime-router__work_side_handoff',
      event_id:'e'.repeat(64), expected_version:5 }, next({ result:'engine-ran' }))
    return { main, worker, own, handoff:{ routed:routed.slice(before), deny:handoff?.deny || null } }
  },
})

// Option 1 (team-lead): the router projects a read-time review_pin on each
// pending row; main decides with expected_review_pin, which the wake's own
// prepare/record writes do not stale. Every wake text main reads, the first
// append and the changed-conversation re-append, must carry that pin.
Object.assign(scenarios, {
  async 'review-wake-text-carries-review-pin'() {
    // Contract shape (backend R1, ^rp1:[0-9a-f]{64}$): the view passes only a valid pin.
    const PIN = 'rp1:' + 'a1'.repeat(32)
    const B = keyBackend(), payload = row => row.message.content[0].text.startsWith('Ciel Work result is awaiting main review')
    const report = (inst, $) => inst.handlers['tool.call']($, { tool:'mcp__ciel-runtime-router__work_parallel_report', agentId:'worker' }, next({ ok:true }))
    // The serialized review view: the first '{' to the shortest '}' that parses.
    const views = texts => texts.map(text => {
      const start = text.indexOf('{')
      for (let end = text.indexOf('}', start); start >= 0 && end !== -1; end = text.indexOf('}', end + 1)) {
        try { return JSON.parse(text.slice(start, end + 1)) } catch {}
      }
      return null
    })
    const one = await keyInstance('rp1'), p1 = keyPrompts()
    const { $:$1, state:s1 } = keyHost(B, keyState(), keyClock(), p1)
    await one.handlers['session.start']($1, { isInteractive:false }, next(undefined))
    B.addReview('L1', PIN)
    await report(one, $1)
    await keyUntil(() => B.calls.includes('L1:review_appended'))
    p1.calls[0]?.release('ok'); await sleep(40)
    const first = (s1.appended || []).filter(payload).map(row => row.message.content[0].text)
    const two = await keyInstance('rp2'), p2 = keyPrompts()
    const { $:$2, state:s2 } = keyHost(B, keyState(), keyClock(), p2)
    s2.session = 'S2'
    await two.handlers['session.start']($2, { isInteractive:false }, next(undefined))
    await report(two, $2); await sleep(60)
    const moved = (s2.appended || []).filter(payload).map(row => row.message.content[0].text)
    return { pin:PIN, first:views(first).map(view => view?.expected_review_pin ?? null),
      moved:views(moved).map(view => view?.expected_review_pin ?? null),
      wakeAfter:B.reviews[0].wake.state, pinAfter:B.reviews[0].review_pin }
  },
})

// Blocker 5 mod half (lead ruling G6, M1-M5; continue-work-pump). After the
// person approves, the host records it itself under _host and the model's call
// does not go on. The token never reaches the model's result; a model-supplied
// _host or user_confirmed is never forwarded. argv: [answer] [agentId].
Object.assign(scenarios, {
  async 'execute-approval-attested'() {
    const answer = process.argv[3] || 'Approve', agentId = process.argv[4] || ''
    const calls = []
    const board = { ...snapshot('planning', 7), approval:'ask' }
    const { $ } = host(async (tool, args) => { calls.push({ tool, args })
      if (tool === 'work_status') return args.format === 'text' ? text('Goal plan text') : text(board)
      if (tool === 'work_execute') return text('Plan approved; Work is in the executing phase.')
      return text({}) })
    const asked = []
    $.ui.ask = async (question, options) => { asked.push({ question, options }); return answer }
    const engine = []
    const engineNext = async e => { engine.push(e); return { result:'engine-ran' } }
    engineNext.origin = { plugin:'engine' }
    const result = await handlers['tool.call']($, { tool:'mcp__ciel-runtime-router__work_execute', user_confirmed:true,
      ...(agentId ? { agentId } : {}), _host:{ token:'forged', internal:true, user_confirmed:true } }, engineNext)
    return { result, engine, asked:asked.map(a => a.question.split('\n').slice(0, 3)),
      executes:calls.filter(c => c.tool === 'work_execute').map(c => c.args),
      resultHasToken:JSON.stringify(result).includes('host-token') }
  },
  async 'execute-auto-forwards-clean'() {
    const { $ } = host(async (tool, args) => tool === 'work_status' ? text(snapshot('planning', 3)) : text('ok'))
    const engine = []
    const engineNext = async e => { engine.push(e); return { result:'engine-ran' } }
    engineNext.origin = { plugin:'engine' }
    const result = await handlers['tool.call']($, { tool:'mcp__ciel-runtime-router__work_execute', user_confirmed:true,
      _host:{ token:'forged' } }, engineNext)
    return { result, engine }
  },
})

// Real claude.exe 2.1.323 (run-w-L1r-router-20261113-094400, debug :380-403):
// ciel-work's $.tool.check presence query runs through ciel-work's OWN
// tool.check hook first (the re-entry skip covers only the calling hook frame),
// then through the gate below it. This host models that chain: the query goes
// to handlers['tool.check'] with next.origin ciel-work, and only a next() call
// reaches the gate's answer. argv[3]: board phase (planning|executing).
Object.assign(scenarios, {
  async 'fork-gate-probe-through-own-check'() {
    const phase = process.argv[3] || 'planning'
    const calls = []
    const { $, state } = host(async (tool, args) => { calls.push({ tool, event: args?.event })
      if (tool === 'work_status') return text(snapshot(phase))
      if (tool === 'work_parallel_status') return text({ version: 3, policy: { enabled: true, paused: false }, leases: {} })
      if (tool === 'work_parallel_event') return text({ fork_gate: {}, gate_presence_version: 1 })
      return text({}) })
    const reachedGate = []
    $.tool.check = async q => {
      const gate = async e => {
        reachedGate.push(e.tool)
        return typeof e.input?.ciel_work_gate_probe === 'string'
          ? { decision: 'ask', plugin: 'ciel-work-gate', reason: 'ciel-work-gate 1 ' + e.input.ciel_work_gate_probe }
          : { decision: 'allow' }
      }
      gate.origin = { plugin: 'ciel-work' }
      const answer = await handlers['tool.check']($, { tool: q.tool, input: q.input }, gate)
      // The engine pins plugin to the hook that answered; ciel-work's own answer has none.
      return reachedGate.length ? answer : { ...answer, plugin: 'ciel-work' }
    }
    await handlers['session.start']($, { isInteractive: false }, next(undefined))
    const reports = calls.filter(c => c.event === 'gate_presence').length
    const presence = state.logs.filter(l => /fork gate|FORK GATE/i.test(String(l)))
    return { reachedGate, reports, phase, toasts: (state.toasts || []).length }
  },
})

// Walkie 2026-11-16: a turn end's board read took longer than 2 s under the
// end-of-turn load (1.46-3.69 s measured), so Stop logged "could not read the
// Work status" and the turn ended with runnable work. A 3.5 s read must still
// continue the turn.
Object.assign(scenarios, {
  async 'slow-board-read-still-continues-the-turn'() {
    const board = { ...snapshot('executing', 9), done: 0, total: 2,
      continuation: { actionable: 1, next: { id: 'n2', title: 'Runnable step' }, waiting: [] },
      queue: [{ id: 'n1', title: 'goal', status: 'in_progress', done: 0, total: 2 }] }
    let reads = 0
    const { $, state } = host(async tool => {
      if (tool === 'work_status') { reads++; await sleep(3500); return text(board) }
      if (tool === 'work_parallel_status') return text({ version: 1, policy: { enabled: false }, leases: {} })
      if (tool === 'work_side_claim') return text({ claim: null, settings: {}, handoff_replies: [], main_wakes: [] })
      if (tool === 'work_side_status') return text({ side_inbox_receipts: [] })
      return text({})
    })
    const started = Date.now()
    const stop = await handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    return { block: stop?.block || '', reads, seconds: Math.round((Date.now() - started) / 1000),
      unreadable: state.logs.some(line => line.includes('could not read the Work status')) }
  },
})

// Walkie 2026-11-16: slots were free but no fork started while main worked
// inline through a long turn: nothing woke the pump until the turn ended.
Object.assign(scenarios, {
  // Stage 3 (2026-11-17): a periodic pump runs while a Work fork runs, and the
  // gate check runs with a fork running and side intake off: the host round
  // re-pumps, reads the diagnosis, claims one notice and records what it did.
  async 'stage3-periodic-pump-and-gate-host-round'() {
    const timers = [], gate = [], statusAt = []
    const diagnosis = { rows:[{ priority:'P1', limit:3, active:1, free:2, candidates:2, state:'can_fill', reason:'2 runnable' },
      { priority:'P2', limit:2, active:0, free:2, candidates:0, state:'no_candidates', reason:'free slot but no runnable item (goal in planning 1)' }],
      summary:'P1 1/3 can fill · P2 0/2 no candidates', attention:['P2'], attention_digest:'ab'.repeat(8) }
    const { $, state } = host(async (tool, args) => {
      if (tool === 'work_status') return text(snapshot())
      if (tool === 'work_parallel_status') { statusAt.push(tool); return text({ version:1, policy:{ enabled:true, paused:false,
        priority_limits:{P1:3,P2:2,P3:1,P4:1,P5:1}, launches_per_minute:4 },
        leases:{ L1:{ id:'L1', item_id:'n2', role:'worker', state:'running', slot:true, host_id:'agent-run', agent_name:'work-auto-r', plan_version:7, priority:'P1' } },
        priority_diagnosis:diagnosis }) }
      if (tool === 'work_parallel_event') throw new Error('no eligible candidate')
      if (tool === 'work_side_gate_check') {
        gate.push(args)
        const report = args.host_report
        if (!report) return text({ interval_minutes:5, queued:false, side_intake:false, host_only:true, host_due:true,
          reason:'side_intake_off', gates:['n3'] })
        if (report.claim === 'diagnosis') return text({ claimed: gate.filter(g => g.host_report?.claim === 'diagnosis').length === 1 })
        if (report.claim === 'discovery') return text({ claimed:false })
        return text({ recorded:true, host_round:report.round })
      }
      if (tool === 'work_side_claim') return text({ claim:null, settings:{}, handoff_replies:[], main_wakes:[] })
      if (tool === 'work_side_status') return text({ side_inbox_receipts:[] })
      return text({})
    })
    $.clock.after = (delay, fn) => { const t = { delay, fn, cancelled:false }; timers.push(t); return { cancel() { t.cancelled = true } } }
    state.agents = [{ id:'agent-run', status:'running', type:'fork', spawnedBy:'ciel-work' }]
    await handlers['session.start']($, { isInteractive:true }, next({ cwd:'C:/w' }))
    await sleep(30)
    const periodicArmed = timers.filter(t => t.delay === 60000 && !t.cancelled).length
    const fire = async () => { for (const t of timers.splice(0).filter(t => t.delay === 60000 && !t.cancelled)) { await t.fn(); await sleep(10) } }
    const beforePeriodic = statusAt.length
    await fire()
    const periodicPumped = statusAt.length > beforePeriodic
    const rearmed = timers.filter(t => t.delay === 60000 && !t.cancelled).length
    // Main's turn ends with a fork still running: the gate check is scheduled and runs.
    await handlers['turn.complete']($, {}, next(undefined))
    await sleep(30)
    await fire(); await sleep(30)
    const first = gate.find(g => !g.host_report)
    const rounds = gate.filter(g => g.host_report?.round).map(g => g.host_report.round)
    const appended = (state.appended || []).map(a => a.message?.content?.[0]?.text || '')
    // A second interval with the same diagnosis: the router refuses the claim, no second notice.
    await fire(); await sleep(30)
    const appendedAfter = (state.appended || []).map(a => a.message?.content?.[0]?.text || '').filter(t => t.startsWith('Ciel Work dispatch check')).length
    return { periodicArmed, periodicPumped, rearmed, firstGate:first ? { idle:first.idle, forks_running:first.forks_running } : null,
      claims:gate.filter(g => g.host_report?.claim).map(g => g.host_report.claim), round:rounds[0] || null, rounds:rounds.length,
      notice:appended.find(t => t.startsWith('Ciel Work dispatch check')) || '', notices:appendedAfter,
      wake:(state.submitted || []).map(s => s.text).find(t => /dispatch wake/.test(t)) || '',
      logs:state.logs.filter(l => /gate check \(host round\)/.test(l)) }
  },
  // 2026-11-18: each P0 goal's outcome reaches main once: fork started, or the
  // exact refusal. A pending launch says nothing; a failed delivery can retry.
  async 'parallel-p0-outcome-notices'() {
    const mode = process.argv[3] || 'mixed'
    const started = { root_id:'n1', title:'runs', forks:1, runnable:0, state:'running', code:'', reason:'1 fork(s) running',
      fork_agents:[{ agent_id:'agent-1', item_id:'n2' }], notified:{} }
    const blocked = { root_id:'n3', title:'refused', forks:0, runnable:1, state:'blocked', code:'workspace_git_untrusted',
      reason:'git refuses the repository (dubious ownership)', notified:{},
      refusal:{ code:'workspace_git_untrusted', item_id:'n4', detail:'detected dubious ownership in repository' } }
    const pending = { root_id:'n5', title:'pending', forks:0, runnable:1, state:'pending', code:'', reason:'launch pending; no refusal seen', notified:{} }
    const done = { root_id:'n6', title:'ran earlier', forks:0, runnable:0, state:'blocked', code:'open_lease', reason:'its leaves already hold open leases', notified:{ started:1 } }
    const goals = mode === 'started-only' ? [started] : [started, blocked, pending, done]
    const claimed = new Set(), claims = []
    const { $, state } = host(async (tool, args) => {
      if (tool === 'work_status') return text(snapshot())
      if (tool === 'work_parallel_status') return text({ version:1, leases:{}, p0_goals:goals,
        policy:{ enabled:true, paused:false, priority_limits:{P1:3,P2:2,P3:1,P4:1,P5:1}, launches_per_minute:4 } })
      if (tool === 'work_parallel_event') {
        if (args.event !== 'p0_outcome_claim') throw new Error('no eligible candidate')
        claims.push({ root:args.root_id, outcome:args.outcome, release:args.release === true, internal:args._host?.internal === true })
        const key = args.root_id + ':' + args.outcome
        if (args.release) { claimed.delete(key); return text({ claimed:true }) }
        const fresh = !claimed.has(key); claimed.add(key)
        return text({ claimed:fresh })
      }
      if (tool === 'work_side_claim') return text({ claim:null, settings:{}, handoff_replies:[], main_wakes:[] })
      if (tool === 'work_side_status') return text({ side_inbox_receipts:[] })
      return text({})
    })
    if (mode === 'append-denied') $.session.append = async () => ({ deny:'no' })
    await handlers['session.start']($, { isInteractive:true }, next({ cwd:'C:/w' }))
    await sleep(30)
    await handlers['turn.complete']($, {}, next(undefined))
    await sleep(30)
    return { claims, notices:(state.appended || []).map(row => row.message?.content?.[0]?.text || '').filter(t => t.startsWith('Ciel Work P0 outcome')),
      wakes:(state.submitted || []).map(row => row.text).filter(t => /P0 wake/.test(t)) }
  },
  async 'main-steps-wake-the-pump-during-a-long-turn'() {
    const pumps = []
    const { $ } = host(async (tool) => {
      if (tool === 'work_status') return text(snapshot())
      if (tool === 'work_parallel_status') { pumps.push(Date.now()); return text({ version:1, policy:{enabled:true}, leases:{} }) }
      if (tool === 'work_side_claim') return text({ claim:null, settings:{}, handoff_replies:[], main_wakes:[] })
      if (tool === 'work_side_status') return text({ side_inbox_receipts:[] })
      return text({})
    })
    await handlers['session.start']($, { isInteractive:false }, next({cwd:'C:/w'}))
    await sleep(50)
    await handlers['turn.start']($, { turnId:'T', text:'long inline work' }, next({}))
    await sleep(50)
    const started = pumps.length
    await mainStep($, 'T', 0)
    await sleep(50)
    const afterFirst = pumps.length
    await mainStep($, 'T', 1)
    await mainStep($, 'T', 2)
    await sleep(50)
    const withinWindow = pumps.length
    later(31000)
    await mainStep($, 'T', 3)
    await sleep(50)
    const afterWindow = pumps.length
    // A child (fork) step never wakes it.
    later(31000)
    for await (const _ of handlers['turn.step']($, { turnId:'C', index:0, agentId:'fork1', messageCount:0 }, engineStream)) { void _ }
    await sleep(50)
    return { firstStepPumped: afterFirst > started, throttled: withinWindow === afterFirst,
             nextWindowPumped: afterWindow > withinWindow, childStepPumped: pumps.length > afterWindow }
  },
})

const name = process.argv[2]
process.stdout.write(JSON.stringify({ scenario: name, ...(await scenarios[name]()) }) + '\n')

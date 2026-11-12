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
  const state = { session: 'S1', calls: 0, logs: [], env: new Map(), envFault: null, nonceRace: null, routerNonces: [] }
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
      return respond(tool, args, state)
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
// Main turn events in the order owned probes stopblock-838268bd/d9595ffa saw:
// turn.start T, turn.step T#0, Stop (block), turn.step T#1, Stop, turn.complete T.
const engineStream = async function* () { yield { kind:'engine' } }
const mainStep = async ($, turnId, index) => {
  for await (const _ of handlers['turn.step']($, { turnId, index, messageCount:9 }, engineStream)) { void _ }
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
      if (tool === 'work_side_wake' && args.state === 'queued') {
        wake = { state:'queued', phase:'started', revision:3, attempt_ref:attemptRef(attempt), delivery_uncertain:false }
        return text({ protocol:2, state:'queued', revision:3 })
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
        return text({ claim: { receipt_id: id, generation: 'g1', text: 'event', route: {}, lease_until: Date.now() / 1000 + 600,
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
        return text({claim:{receipt_id:id,generation:'g1',text:'event',route:{},lease_until:Date.now()/1000+600,
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
      return text({ claim:null, settings:{}, handoff_replies:[], main_wakes:offered ? [L.row(true)] : [] })
    }
    if (tool !== 'work_side_wake') return text({})
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
    if (args.state === 'retry') { L.retry = { ...args }; delete L.retry.session_id; delete L.retry._host; return text({ protocol:2, state:'failed' }) }
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
    tool:{ list:async () => [], call:async () => ({}) },
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
      if (args.state === 'queued') { Object.assign(w, { state:'queued', revision:w.revision + 1 }); return text(own(w)) }
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
    return { button:true, afterCancel, retry:L.retry, ref:attemptRef('7'.repeat(32)) }
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
    const label = value => (/"lease_id":"(L\d)"/.exec(value) || /"event_id":"(E\d)/.exec(value) || [])[1] || '?'
    const reviews = [], wakes = {}, records = [], submits = [], invocations = []
    let active = 0, maxActive = 0
    const reviewRow = id => ({ lease_id:id, item_id:'n' + id, item_title:'Result ' + id, state:'pending', decision:null,
      summary:'built ' + id, verification:'ok', wake:{ state:'pending', attempts:0 } })
    const sideRows = []
    const sideOwn = w => ({ protocol:2, state:w.state, phase:w.phase, attempt_id:w.attempt_id, owner_nonce:w.owner,
      channel:w.channel, revision:w.revision })
    const { $ } = host(async (tool, args) => {
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
        if (['queued', 'appended', 'uncertain', 'failed'].includes(args.state)) {
          Object.assign(w, { state:args.state, revision:w.revision + 1 })
          records.push(label('"event_id":"' + args.event_id + '"') + ':' + args.state)
          return text(sideOwn(w))
        }
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
    const keys = [...leaseIds.map(id => 'parallel:' + id), ...eventIds.map(id => 'side:' + id)]
    const whileBothPending = { invocations:[...invocations], maxActive, pending:keys.filter(key => sidePromptPending(key)).length }
    const stopWhileBothPending = lines(await stop())
    // Release by producer order (first triggered key first), not by submit
    // index: independent keys may reach $.prompt.submit in either order.
    const releaseOrder = order.flatMap(kind => kind === 'review' ? leaseIds : eventIds.map(id => id.slice(0, 2)))
    const byLabel = id => submits.find(entry => entry.label === id)
    const [firstOut, secondOut] = reverse ? [byLabel(releaseOrder[1]), byLabel(releaseOrder[0])]
      : [byLabel(releaseOrder[0]), byLabel(releaseOrder[1])]
    firstOut?.release(reverse ? 'ok' : fault || 'ok'); await sleep(30)
    const afterFirst = { invocations:[...invocations], records:[...records], pending:keys.filter(key => sidePromptPending(key)) }
    const stopAfterFirst = lines(await stop())
    secondOut?.release(reverse ? fault || 'ok' : 'ok'); await sleep(30)
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
    await sleep(5)
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
  B.addReview = id => B.reviews.push({ lease_id:id, item_id:'n' + id, item_title:'Result ' + id, state:'pending',
    decision:null, summary:'built ' + id, verification:'ok', wake:{ state:'pending', attempts:0 } })
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
    if (tool === 'work_side_claim') return text({ claim:null, settings:{}, handoff_replies:[],
      main_wakes:B.sideRows.filter(r => { const w = B.wakes[r.event_id]
        return ['pending', 'failed'].includes(w.state) || (w.state === 'submitting' && ['intent', 'started'].includes(w.phase)) })
        .map(r => B.row(r.event_id, true)) })
    if (tool !== 'work_side_wake') return text({})
    const w = B.wakes[args.event_id]
    B.calls.push(short(args.event_id) + ':' + args.state)
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
  for (const file of ['register.js', 'side-intake.js']) copyFileSync(new URL('./' + file, import.meta.url), new URL(file, dir))
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
  return { $, state }
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
    const issued = { submits:p1.calls.length, registry:store.keys(), live:c1.live(),
      attempt:path === 'review' ? B.reviews[0].wake.attempt_id : B.wakes[KEY_E1].attempt_id }
    const before = await keyView(one, $1)
    const settled = state => keyUntil(() => B.records.some(row => row.endsWith(':' + state)))
    let after = null, second = null
    if (condition === 'finite') {
      p1.calls[0]?.release('ok'); await settled('queued')
      after = await keyView(one, $1)
    } else if (condition === 'anomaly') {
      // No main turn runs: the idle window releases the key.
      await c1.fire(); await settled('uncertain')
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
      const key = path === 'review' ? 'parallel:L1' : 'side:' + KEY_E1
      const hydrated = { pending:two.side.sidePromptPending(key), live:c2.live() }
      const secondBefore = await keyView(two, $2)
      await c2.fire()
      if (condition === 'reload') await keyUntil(() => !store.keys().length)
      const secondAfter = await keyView(two, $2)
      const registryAfterWindow = condition === 'reload' ? store.keys() : null
      // After a reload the first environment's prompt can still resolve late
      // (it outlived the reload while one of its hooks ran:
      // prompt-registry-e2bed500). A restarted process cannot.
      if (condition === 'reload') { p1.calls[0]?.release('ok'); await settled('queued') }
      second = { hydrated, before:secondBefore, after:secondAfter, submits:p2.calls.length, registryAfterWindow,
        lateRecords:[...B.records] }
    }
    return { path, condition, issued, before, after, second, records:[...B.records], registry:store.keys(),
      sideCalls:B.calls, review:B.reviews[0]?.wake || null, side:B.wakes[KEY_E1] ? { ...B.pub(KEY_E1) } : null }
  },
  // A never-settling A does not block B (another side wake) or L1 (a review);
  // after A's idle window it is recorded uncertain and visible. No window runs
  // during a main turn; turn.complete arms a full one.
  async 'prompt-key-latent'() {
    const B = keyBackend(), store = keyState()
    const one = await keyInstance('1'), c1 = keyClock(), p1 = keyPrompts()
    const { $ } = keyHost(B, store, c1, p1)
    await one.handlers['session.start']($, { isInteractive:false }, next(undefined))
    B.addSide(KEY_E1)
    await one.side.pollSide($, 'claude:S1')
    await keyUntil(() => p1.calls.length === 1)
    B.addSide(KEY_E2); B.addReview('L1')
    await one.side.pollSide($, 'claude:S1')
    await one.handlers['tool.call']($, { tool:'mcp__ciel-runtime-router__work_parallel_report', agentId:'worker' }, next({ ok:true }))
    await keyUntil(() => p1.calls.length === 3)
    const label = t => t.includes(KEY_E1) ? 'A' : t.includes(KEY_E2) ? 'B' : t.includes('"lease_id":"L1"') ? 'L1' : '?'
    const issued = p1.calls.map(call => label(call.text))
    const registryWhileAll = store.keys()
    for (const call of p1.calls) if (label(call.text) !== 'A') call.release('ok')
    await keyUntil(() => B.records.length === 2)
    const whileALatent = { records:[...B.records], registry:store.keys(), pendingA:one.side.sidePromptPending('side:' + KEY_E1),
      view:await keyView(one, $) }
    await one.handlers['turn.start']($, { turnId:'T1', text:'' }, next(undefined))
    const liveInTurn = c1.live()
    await c1.fire()
    const afterFireInTurn = { records:[...B.records], pendingA:one.side.sidePromptPending('side:' + KEY_E1) }
    await one.handlers['turn.complete']($, { turnId:'T1', reason:'answer', answer:'x' }, next(undefined))
    const liveAfterTurn = c1.live()
    await c1.fire()
    await keyUntil(() => B.records.includes('E1:uncertain'))
    const view = await keyView(one, $)
    return { issued, registryWhileAll, whileALatent, liveInTurn, afterFireInTurn, liveAfterTurn,
      afterWindow:{ records:[...B.records], registry:store.keys(), pendingA:one.side.sidePromptPending('side:' + KEY_E1),
        view, viewAgain:await keyView(one, $) } }
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

const name = process.argv[2]
process.stdout.write(JSON.stringify({ scenario: name, ...(await scenarios[name]()) }) + '\n')

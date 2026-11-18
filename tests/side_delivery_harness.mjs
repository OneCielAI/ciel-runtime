// What main's conversation receives for one side event (side-intake.js only).
// The host below is an engine model built from recorded owned probes:
//  - $.session.append stores its row at once and answers {uuid}; an idle append
//    starts no turn and the next prompt's request carries it (delivery-4f4c90b8);
//  - $.prompt.submit resolves when the prompt enters; a held prompt enters
//    later and is never withdrawn (prompt-gate-0dd78bf3, prompt-registry-e2bed500);
//  - one main model request carries every stored row not carried before.
// The fake backend follows side_wake.py (prepare/start/record, read, notice
// claim). argv[2] is mode[:variant]; one JSON line is printed.
import * as first from './side-intake.js'
const second = await import('./side-intake.js?second')
const [mode, variant] = String(process.argv[2] || '').split(':')
const EVENT = 'e'.repeat(64)
const WAKE = 'Ciel side event requires main action: {"event_id":"' + EVENT
const NOTICE = 'Ciel side released recorded wait n3'
const tick = () => new Promise(resolve => setTimeout(resolve, 5))
// The module's clock, moved by the scenario: a main step proves a row was
// carried only once the row is older than the module's hook-delay grace.
const realNow = Date.now.bind(Date)
let skew = 0
Date.now = () => realNow() + skew
const later = (ms = 2000) => { skew += ms }
const reply = (value, error = false) => ({isError:error, content:[{type:'text', text:typeof value === 'string' ? value : JSON.stringify(value)}]})

// Durable backend shared by every module instance and process of the session.
function backend(options = {}) {
  const B = {wake:{state:'pending', revision:0}, attempts:0, promptAttempts:0, calls:[], notice:null, noticeClaims:0, prompt:null,
    claimsLeft:options.claims ?? 1, handoff:options.handoff ?? 'time_limit', released:options.released ?? false,
    oldRouter:options.oldRouter === true, failRecord:0}
  const own = () => ({protocol:2, state:B.wake.state, wake_state:B.wake.state, phase:B.wake.phase, attempt_id:B.wake.attempt_id,
    owner_nonce:B.wake.owner_nonce, channel:B.wake.channel, revision:B.wake.revision})
  const handoff = {kind:'time_limit', item_id:'n7', root_id:'n7', title:'report', summary:'deadline reported',
    queue:{state:'active'}, time_limit:{text:'within 4 hours', evidence:'message 41: within 4 hours'}, waits:[], question:''}
  B.requested = false
  const offered = () => B.requested && (['pending', 'failed'].includes(B.wake.state)
    || (B.wake.state === 'submitting' && ['intent', 'started'].includes(B.wake.phase))
    || (!B.oldRouter && B.wake.state === 'appended' && !B.wake.read_at))
  B.call = async (name, args) => {
    B.calls.push(name + (args?.state ? ':' + args.state : ''))
    if (name === 'work_side_claim') {
      const claim = B.claimsLeft > 0 ? {receipt_id:EVENT, generation:'10000000000000000000000000000006', text:'raw secret thread', event_key:'x',
        lease_until:Date.now() / 1000 + 120, source_read:null, route:{},
        workload:{version:1, truncated:false, nodes:[{id:'n7', parent_id:null, title:'report'},
          {id:'n3', parent_id:'n7', title:'gate', waiting_on:'approval'}], queue_order:[]}} : null
      B.claimsLeft--
      const {owner_nonce, ...visible} = B.wake
      return reply({claim, settings:{}, handoff_replies:[],
        main_wakes:offered() ? [{event_id:EVENT, state:'requested', ...handoff, wake:visible}] : []})
    }
    if (name === 'work_side_resolve') {
      if (B.handoff === 'time_limit') B.requested = true
      return reply({receipt:{receipt_id:EVENT, stage:'processed'}, result:{action:'update', item_id:'n7', summary:'deadline reported',
        duplicate:false, ...(B.handoff === 'time_limit' ? {main_handoff:handoff} : {}),
        ...(B.released ? {released_waits:[{id:'n3', waiting_on:'approval'}], handoff:'not_required'} : {})}})
    }
    if (name === 'work_parallel_status') return reply({version:1, policy:{}, leases:{}})
    if (name === 'work_side_reply') return reply({receipt:{}, arguments:null})
    if (name !== 'work_side_wake') return reply({})
    const state = args.state
    if (String(state).startsWith('prompt_')) {
      if (state === 'prompt_prepare') {
        if (B.prompt && B.prompt.conversation === args.receipt && ['submitting','queued'].includes(B.prompt.state))
          return reply({protocol:2,state:'already_'+B.prompt.state})
        B.prompt = {state:'submitting',attempt_id:'p'+(++B.promptAttempts),owner_nonce:args.owner_nonce,conversation:args.receipt}
        return reply({protocol:2,state:'submitting',attempt_id:B.prompt.attempt_id,owner_nonce:args.owner_nonce})
      }
      if (!B.prompt || B.prompt.attempt_id !== args.attempt_id) return reply('stale side wake prompt attempt',true)
      B.prompt = {...B.prompt,state:state.slice(7)}
      return reply({protocol:2,state:B.wake.state})
    }
    if (String(state).startsWith('notice_')) {
      if (B.oldRouter) return reply('invalid side wake state', true)
      if (state === 'notice_prepare') {
        B.noticeClaims++
        if (B.notice) return reply({protocol:2, state:'already_' + B.notice.state})
        B.notice = {state:'issuing', attempt_id:'n' + (++B.attempts), owner_nonce:args.owner_nonce}
        return reply({protocol:2, state:'claimed', attempt_id:B.notice.attempt_id})
      }
      if (!B.notice || B.notice.attempt_id !== args.attempt_id) return reply('stale side notice attempt', true)
      B.notice = {...B.notice, state:state.slice(7), receipt:args.receipt || null}
      return reply({protocol:2, state:B.notice.state})
    }
    if (state === 'read') {
      if (B.oldRouter) return reply('invalid side wake state', true)
      if (B.wake.state !== 'appended') return reply('side wake is not appended', true)
      B.wake = {...B.wake, read_at:B.wake.read_at || Date.now()}
      return reply(own())
    }
    if (state === 'prepare') {
      const intent = B.wake.state === 'submitting' && B.wake.phase === 'intent'
      if (['pending', 'failed'].includes(B.wake.state)) {
        B.wake = {...B.wake, state:'submitting', phase:'intent', attempt_id:'w' + (++B.attempts), owner_nonce:args.owner_nonce,
          channel:args.channel, revision:B.wake.revision + 1}
        return reply(own())
      }
      if (intent && B.wake.owner_nonce === args.owner_nonce) return reply(own())
      return reply({protocol:2, state:'already_' + (intent ? 'intent' : B.wake.state), wake_state:B.wake.state, revision:B.wake.revision})
    }
    if (state === 'start') {
      if (B.wake.state !== 'submitting' || B.wake.phase !== 'intent' || args.attempt_id !== B.wake.attempt_id
          || args.owner_nonce !== B.wake.owner_nonce) return reply('stale side wake intent', true)
      B.wake = {...B.wake, phase:'started', revision:B.wake.revision + 1}
      return reply(own())
    }
    if (B.failRecord > 0) { B.failRecord--; return reply('record transport failure', true) }
    if (B.wake.state === state && B.wake.attempt_id === args.attempt_id && B.wake.owner_nonce === args.owner_nonce) return reply(own())
    if (B.wake.state !== 'submitting' || B.wake.phase !== 'started' || args.attempt_id !== B.wake.attempt_id
        || args.owner_nonce !== B.wake.owner_nonce) return reply('stale side wake attempt', true)
    B.wake = {...B.wake, state, receipt:args.receipt || B.wake.receipt, revision:B.wake.revision + 1}
    return reply(own())
  }
  return B
}
// One claude.exe conversation: rows, held prompts and requests.
function conversation() {
  const C = {rows:[], carried:0, held:[], submits:[], appends:[], requests:[], turns:0, appendFaults:[], promptFaults:[], hold:false}
  C.append = async a => {
    const text = a.message.content[0].text
    const fault = C.appendFaults.shift()
    if (fault === 'throw') { C.appends.push({text, outcome:'threw'}); throw new Error('append outcome unknown') }
    if (fault === 'stored-throw' || fault === 'stored-throw-twice') {
      C.appends.push({text, outcome:'stored'}); C.rows.push({kind:'append', text})
      // twice: an anomaly, the same exact ref appears in two user rows.
      if (fault === 'stored-throw-twice') C.rows.push({kind:'append', text})
      throw new Error('append result lost after store')
    }
    if (fault === 'deny') { C.appends.push({text, outcome:'deny'}); return {deny:'fixture'} }
    C.appends.push({text, outcome:'stored'})
    C.rows.push({kind:'append', text})
    return {message:a.message, uuid:'uuid-' + C.appends.length}
  }
  C.submit = a => {
    const fault = C.promptFaults.shift()
    C.submits.push(a.text)
    if (fault === 'throw') return Promise.reject(new Error('submit outcome unknown'))
    if (fault === 'drop') return Promise.resolve({drop:'fixture'})
    return new Promise(resolve => {
      const enter = () => { C.rows.push({kind:'prompt', text:a.text}); C.turns++; resolve({text:a.text}) }
      if (fault === 'hold' || C.hold) C.held.push({text:a.text, enter}); else enter()
    })
  }
  // The next main model request: every stored row not carried before. hold:
  // the newest `hold` rows were stored after this request was built.
  C.request = (hold = 0) => { const end = C.rows.length - hold, now = C.rows.slice(C.carried, end).map(row => row.text)
    C.carried = end; C.requests.push(now); return now }
  return C
}
function hostState() {
  const map = new Map()
  return {map, api:{
    get:async ref => { await tick(); const held = map.get(ref.plugin + '/' + ref.key)
      return {value:held ? structuredClone(held.value) : undefined, version:held?.version || 0} },
    set:async (ref, value, options = {}) => { await tick()
      const name = ref.plugin + '/' + ref.key, held = map.get(name) || {version:0}
      if (options.ifVersion !== undefined && options.ifVersion !== held.version) return {isSet:false, version:held.version}
      map.set(name, {value:structuredClone(value), version:held.version + 1}); return {isSet:true, version:held.version + 1} }}}
}
function clock() {
  const timers = []
  return {timers, after(ms, fn) { const t = {ms, fn, cancelled:false, fired:false}; timers.push(t); return {cancel() { t.cancelled = true }} },
    async fire(ms = 5000) { for (const t of timers.filter(t => t.ms === ms && !t.cancelled && !t.fired)) { t.fired = true; await t.fn() } }}
}
// One module instance bound to a conversation. busy mirrors register.js `working`.
function instance(mod, B, C, state, timer) {
  const I = {mod, busy:false, logs:[]}
  mod.sideMainBusy(() => I.busy)
  // I.inHook: the append is raised inside one of this module's hook dispatches,
  // so the engine runs its own session.append hook before the call settles.
  // From a $.clock.after timer it does not (append-views-54579a2e, 2.1.323).
  I.inHook = true
  const append = async a => {
    const before = C.rows.length
    const hooked = I.inHook
    try {
      const result = await C.append(a)
      if (hooked && C.rows.length > before) mod.sideAppendStored?.({message:a.message,door:'note',origin:{plugin:'ciel-work'},uuid:result?.uuid,agentId:undefined},result)
      return result
    } catch (error) {
      // stored-throw: bottom stored a row and the hook saw it before the caller
      // received a transport exception.
      if (hooked && C.rows.length > before) {
        const uuid='uuid-'+C.appends.length
        mod.sideAppendStored?.({message:a.message,door:'note',origin:{plugin:'ciel-work'},uuid,agentId:undefined},{message:a.message,uuid})
      }
      throw error
    }
  }
  // 2.1.323: plain messages() lists no plugin append row; {as:'api'} carries
  // their text as user rows (append-views-54579a2e L2-L13).
  const messages = async options => options?.as === 'api'
    ? C.rows.map(row => ({role:'user',content:[{type:'text',text:row.text}]}))
    : C.rows.filter(row => row.kind !== 'append').map(row => ({role:'user',text:row.text,toolUses:[]}))
  I.$ = {session:{id:async () => 'parent', model:async () => 'fixture-main',
      messages, append,
      send:async () => ({isDelivered:true})},
    prompt:{submit:C.submit}, ...(state ? {state:state.api} : {}), clock:timer,
    mcp:{connect:async () => ({}), call:async (server, name, args) => B.call(name, args)},
    tool:{list:async () => [], call:async () => ({})}, agent:{list:async () => [], spawn:async () => ({agentId:'actor'})},
    env:{get:async () => 'x'.repeat(40)}, ui:{invalidate:() => {}, log:text => I.logs.push(String(text)), status:() => {}}}
  I.poll = () => mod.pollSide(I.$, 'claude:parent')
  I.complete = () => mod.completeSide(I.$, {agentId:'actor', reason:'answer', answer:JSON.stringify({decision:{action:'update',
    kind:'request', item_id:'n7', summary:'deadline reported', reply:'noted', note:'n',
    ...(B.released ? {release_waits:[{id:'n3', waiting_on:'approval'}]} : {}),
    ...(B.handoff === 'time_limit' ? {time_limit:{text:'within 4 hours', evidence:'message 41: within 4 hours'}} : {})},
    expected_version:1})})
  // Main turn events as register.js forwards them to side-intake.js. Measured
  // (append-views-54579a2e): a step's messages are pinned when turn.step is
  // raised; a row stored during that dispatch is in the next request only.
  I.turnStart = async (text = C.rows.filter(row => row.kind === 'prompt').at(-1)?.text || '') => {
    I.busy = true; mod.sidePromptTurnStart?.(); await mod.sideMainTurnStart?.(I.$, text) }
  I.step = async () => { const request = C.request(); await mod.sideMainStep?.(I.$); return request }
  // A classic.Stop that returns a block: the continuation step follows.
  I.stopBlock = async () => { await mod.sideMainFence?.(I.$) }
  I.turnComplete = () => { I.busy = false; mod.sidePromptTurnComplete?.(I.$) }
  return I
}
const settle = async (B, C) => {
  for (let stable = 0, last = ''; stable < 10;) {
    await tick()
    const now = B.calls.length + ':' + C.submits.length + ':' + C.appends.length + ':' + C.rows.length
    stable = now === last ? stable + 1 : 0; last = now
  }
}
const payload = text => text.includes(WAKE) || text.includes(NOTICE)
const view = (B, C) => ({
  payloadRows:C.rows.filter(row => payload(row.text)).length,
  payloadAppends:C.appends.filter(a => a.outcome === 'stored' && payload(a.text)).length,
  payloadPrompts:C.submits.filter(payload).length, plainPrompts:C.submits.filter(text => !payload(text)).length,
  heldPrompts:C.held.length, turns:C.turns, requests:C.requests.map(rows => rows.filter(payload).length),
  wake:{state:B.wake.state, phase:B.wake.phase || null, read:Boolean(B.wake.read_at), attempts:B.attempts},
  notice:B.notice ? B.notice.state : null})

const cases = {
  // Idle main, a time-limit event: the payload reaches main once, in the turn
  // that the host starts.
  async 'wake-idle'() {
    const B = backend(), C = conversation(), I = instance(first, B, C, hostState(), clock())
    await first.startSide(I.$); await I.poll(); await I.complete(); await settle(B, C)
    const before = view(B, C)
    if (C.turns) { await I.turnStart(); await I.step(); I.turnComplete() }
    await settle(B, C)
    return {before, after:view(B, C)}
  },
  // Busy main inside a tool call: the payload is appended before the turn ends
  // and the next request of the same turn carries it. No prompt is needed.
  async 'wake-busy-next-request'() {
    const B = backend(), C = conversation(), I = instance(first, B, C, hostState(), clock())
    await first.startSide(I.$); await I.poll()
    await I.turnStart(); await I.step()
    await I.complete(); await settle(B, C)
    const whileTurnRuns = view(B, C)
    // The next step's request carries the row in the engine (delivery-4f4c90b8),
    // but nothing pins its build after the append: that step only fences it.
    const carried = (await I.step()).filter(payload).length
    await settle(B, C)
    const afterFirstStep = view(B, C)
    // young: the turn ends after that step. Otherwise the turn continues (a tool
    // result, or this module's Stop reminder) and that next request is proof.
    if (variant !== 'young') { await I.stopBlock(); await I.step(); await settle(B, C) }
    I.turnComplete(); await settle(B, C)
    return {whileTurnRuns, carriedByNextRequest:carried, afterFirstStep, after:view(B, C)}
  },
  // Busy main in its final step: the turn ends with no further request, so no
  // request of that turn carried the appended row. Main must still be woken.
  async 'wake-busy-turn-ends'() {
    const B = backend(), C = conversation(), I = instance(first, B, C, hostState(), clock())
    await first.startSide(I.$); await I.poll()
    await I.turnStart(); await I.step()
    await I.complete(); await settle(B, C)
    I.turnComplete(); await settle(B, C)
    const afterTurnEnd = view(B, C)
    for (let i = 0; i < 3; i++) { await I.poll(); await settle(B, C) }
    if (C.turns) { await I.turnStart(); await I.step(); I.turnComplete() }
    await settle(B, C)
    return {afterTurnEnd, after:view(B, C)}
  },
  // The first prompt submit throws. No person acts; polls continue.
  async 'wake-prompt-throw'() {
    const B = backend(), C = conversation(), I = instance(first, B, C, hostState(), clock())
    C.promptFaults.push('throw')
    await first.startSide(I.$); await I.poll(); await I.complete(); await settle(B, C)
    const afterThrow = view(B, C)
    for (let i = 0; i < 4; i++) { await I.poll(); await settle(B, C) }
    if (C.turns) { await I.turnStart(); await I.step(); I.turnComplete() }
    await settle(B, C)
    for (let i = 0; i < 2; i++) { await I.poll(); await settle(B, C) }
    return {afterThrow, after:view(B, C)}
  },
  // The first prompt is held by the engine past the 5 s idle window, then
  // enters. What did the release make durable, and what entered?
  async 'wake-prompt-held'() {
    const B = backend(), C = conversation(), timer = clock(), I = instance(first, B, C, hostState(), timer)
    C.promptFaults.push('hold')
    await first.startSide(I.$); await I.poll(); await I.complete(); await settle(B, C)
    await timer.fire(); await settle(B, C)
    const afterWindow = view(B, C)
    for (let i = 0; i < 3; i++) { await I.poll(); await settle(B, C) }
    for (const held of C.held.splice(0)) held.enter()
    await settle(B, C)
    await I.turnStart(); await I.step(); I.turnComplete(); await settle(B, C)
    return {afterWindow, after:view(B, C)}
  },
  // The append itself has no known outcome: this stays visible, never resent.
  async 'wake-append-throw'() {
    const B = backend(), C = conversation(), I = instance(first, B, C, hostState(), clock())
    // stored: hook path (own session.append hook ran). timer / timer-twice: the
    // append was raised from a $.clock.after callback (own hook skipped,
    // append-views-54579a2e L2); the row was stored once / twice.
    C.appendFaults.push(variant === 'stored' || variant === 'timer' ? 'stored-throw'
      : variant === 'timer-twice' ? 'stored-throw-twice' : 'throw')
    if (variant === 'timer' || variant === 'timer-twice') I.inHook = false
    await first.startSide(I.$); await I.poll()
    if (variant === 'busy') { await I.turnStart(); await I.step() }
    await I.complete(); await settle(B, C)
    for (let i = 0; i < 3; i++) { await I.poll(); await settle(B, C) }
    return {after:view(B, C), receipt:B.wake.receipt || null, logs:I.logs, appendCalls:C.appends.length}
  },
  // A released-wait notice. variant: once | restart | reload | clear-reload |
  // evict | old-router | busy. Every variant but once and busy drives the same
  // event's completion a second time after the notice identity was lost from
  // the module and from $.state; the backend is the same durable store.
  async notice() {
    const B = backend({handoff:'none', released:true, claims:variant === 'once' || variant === 'busy' ? 1 : 2,
      oldRouter:variant === 'old-router'})
    const C = conversation(), state = hostState()
    const one = instance(first, B, C, state, clock())
    await first.startSide(one.$); await one.poll()
    if (variant === 'busy') { one.turnStart(); await one.step() }
    await one.complete(); await settle(B, C)
    if (variant === 'busy') { later(); await one.step(); one.turnComplete() } else if (C.turns) { one.turnStart(); await one.step(); one.turnComplete() }
    await settle(B, C)
    const firstIssue = view(B, C)
    if (variant === 'once' || variant === 'busy') return {firstIssue, logs:one.logs}
    // The module that issued it is gone; what the next one can still see differs.
    let nextState = state
    if (variant === 'restart' || variant === 'old-router') nextState = hostState()
    if (variant === 'clear-reload') state.map.clear()
    if (variant === 'evict') state.map.set('ciel-work/promptPending', {version:999, value:Array.from({length:200}, (_, i) =>
      ({key:'notice:newer' + i, generation:'t' + i, status:'terminal', outcome:'queued', settledAt:Date.now() + i + 1}))})
    const two = instance(second, B, C, nextState, clock())
    await second.startSide(two.$); await two.poll(); await two.complete(); await settle(B, C)
    if (C.turns > firstIssue.turns) { two.turnStart(); await two.step(); two.turnComplete() }
    await settle(B, C)
    return {firstIssue, after:view(B, C), noticeClaims:B.noticeClaims, logs:[...one.logs, ...two.logs]}
  },
}
console.log(JSON.stringify({mode, variant:variant || null, ...(await cases[mode]())}))
process.exit(0)

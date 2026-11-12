// Host harness for the protocol-2 side wake election (side-intake.js).
// The fake backend mirrors side_wake.py: pending|failed -> intent (attempt,
// owner); same owner adopts intent, another owner supersedes intent only;
// start fences intent -> started; only the started owner records an outcome.
// The real backend is covered in test_side_handoff_flow.py.
import * as first from './side-intake.js'
const [mode, variant] = String(process.argv[2] || 'idle').split(':')
let busyNow = mode === 'busy' || variant === 'busy' || ['deny', 'append-idle'].includes(mode)
first.sideMainBusy(() => busyNow)
const second = ['reload', 'reload-failed', 'stale-loser', 'reload-intent', 'livelock', 'old-router-reload'].includes(mode)
  ? await import('./side-intake.js?reload') : null
if (second) second.sideMainBusy(() => busyNow)
const EVENT = 'e'.repeat(64)
const appends = [], submits = [], calls = [], messages = [], logs = []
const handoff = {kind:'time_limit', item_id:'n7', root_id:'n7', title:'report', summary:'deadline reported',
  queue:{state:'active'}, time_limit:{text:'within 4 hours', evidence:'message 41: within 4 hours'}, waits:[], question:''}
let claimed = false, offerWake = false, attempts = 0
let failPrepare = mode === 'retry' ? 1 : 0
let losePrepare = mode === 'lost-prepare' ? 1 : 0
let failRecord = ['record-fail', 'old-router-dup'].includes(mode) ? 3 : 0
let staleOnce = false
let prepareGate = null, openPrepare = () => {}
// Logical clock (ms) for the intent lease; only lease modes advance it.
const LEASE = 5000
let clock = 0
let startGate = null, openStart = () => {}
let heldSubmit = null, releaseSubmit = () => {}
let wake = mode === 'expired-intent'
  ? {state:'submitting', phase:'intent', attempt_id:'w0', owner_nonce:'crashed-module', channel:'prompt', intent_until:LEASE, revision:1}
  : mode === 'orphan-started'
  ? {state:'submitting', phase:'started', attempt_id:'w0', owner_nonce:'other-module', channel:'prompt', revision:2}
  : {state:'pending', revision:0}
const view = () => {
  if (staleOnce) { staleOnce = false; return {event_id:EVENT, state:'requested', ...handoff, wake:{state:'pending', revision:0}} }
  const {owner_nonce, ...visible} = wake
  return {event_id:EVENT, state:'requested', ...handoff, wake:visible}
}
// Old routers offered only pending/failed (no phase field exists there).
const offered = () => ['pending', 'failed'].includes(wake.state)
  || (wake.state === 'submitting' && ['intent', 'started'].includes(wake.phase))
  || (mode === 'old-router-dup' && wake.state === 'submitting') // stale claim snapshot
const reply = (value, error = false) => ({isError:error, content:[{type:'text', text:typeof value === 'string' ? value : JSON.stringify(value)}]})
const own = () => ({protocol:2, state:wake.state, wake_state:wake.state, phase:wake.phase, attempt_id:wake.attempt_id,
  owner_nonce:wake.owner_nonce, channel:wake.channel, revision:wake.revision})
// Exact old router contract (side_wake 4b85569d): protocol fields ignored;
// prepare returns {event_id, ...wake, version}; a duplicate prepare returns
// the stored row with its attempt (the pre-fix duplicate); record requires
// submitting + matching attempt.
const OLD_ATTEMPT = 'a'.repeat(31) + '1'
async function oldRouter(args) {
  if (args.state === 'prepare') {
    if (prepareGate) await prepareGate
    if (['submitting', 'queued', 'appended', 'uncertain'].includes(wake.state))
      return reply({event_id:args.event_id, ...wake, version:4})
    wake = {state:'submitting', requested_at:1, attempt_id:OLD_ATTEMPT, started_at:2}
    return reply({event_id:args.event_id, ...wake, version:4})
  }
  if (!['queued', 'appended', 'uncertain', 'failed'].includes(args.state)) return reply('invalid side wake state', true)
  if (failRecord > 0) { failRecord--; return reply('record transport failure', true) }
  if (wake.state !== 'submitting' || !args.attempt_id || wake.attempt_id !== args.attempt_id)
    return reply('stale side wake attempt', true)
  wake = {...wake, state:args.state, updated_at:3}
  return reply({event_id:args.event_id, ...wake, version:4})
}
async function wakeCall(args) {
  if (['old-router', 'old-router-dup', 'old-router-reload', 'old-router-exhaust'].includes(mode)) return oldRouter(args)
  if (mode === 'malformed' && args.state === 'prepare')
    return reply({protocol:2, state:'submitting', phase:'started', attempt_id:'b'.repeat(32), channel:'prompt'})
  if (args.state === 'prepare') {
    if (prepareGate) await prepareGate
    if (failPrepare > 0) { failPrepare--; return reply('transient prepare failure', true) }
    // Mirrors side_wake.prepare: a fresh intent is leased for LEASE ms; another
    // owner may supersede only after it expires.
    const intent = wake.state === 'submitting' && wake.phase === 'intent'
    if (['pending', 'failed'].includes(wake.state)
        || (intent && wake.owner_nonce !== args.owner_nonce && clock >= wake.intent_until)) {
      wake = {...wake, state:'submitting', phase:'intent', attempt_id:'w'+(++attempts),
        owner_nonce:args.owner_nonce, channel:args.channel, intent_until:clock + LEASE, revision:wake.revision+1}
    } else if (intent && wake.owner_nonce !== args.owner_nonce) {
      return reply({protocol:2, state:'already_intent', wake_state:wake.state, phase:wake.phase, revision:wake.revision})
    } else if (!(intent && wake.owner_nonce === args.owner_nonce)) {
      return reply({protocol:2, state:'already_'+wake.state, wake_state:wake.state, phase:wake.phase, revision:wake.revision})
    }
    // Committed durably; the response may still be lost in transport.
    if (losePrepare > 0) { losePrepare--; return reply('response lost', true) }
    return reply(own())
  }
  if (args.state === 'start') {
    if (startGate) { const gate = startGate; startGate = null; await gate }
    if (wake.state !== 'submitting' || wake.phase !== 'intent' || args.attempt_id !== wake.attempt_id
        || args.owner_nonce !== wake.owner_nonce) return reply('stale side wake intent', true)
    wake = {...wake, phase:'started', revision:wake.revision+1}
    return reply(own())
  }
  // The host never reconciles from the transcript (no row carries a uuid).
  if (args.state === 'reconcile') return reply('unexpected reconcile', true)
  if (failRecord > 0) { failRecord--; return reply('record transport failure', true) }
  if (wake.state === args.state && wake.attempt_id === args.attempt_id && wake.owner_nonce === args.owner_nonce)
    return reply(own())
  if (wake.state !== 'submitting' || wake.phase !== 'started' || args.attempt_id !== wake.attempt_id
      || args.owner_nonce !== wake.owner_nonce) return reply('stale side wake attempt', true)
  wake = {...wake, state:args.state, receipt:args.receipt || wake.receipt, revision:wake.revision+1}
  return reply(own())
}
let denyAppend = mode === 'deny' ? 1 : 0
// Rows in the shape the engine returns (SessionMessage: role/text/toolUses; no
// uuid, measured in owned probe state-fence-74f330ba). The append result alone
// carries the uuid, as the engine's does.
const row = (role, text) => ({role, text, toolUses:[]})
// Host-held $.state shared by both module instances (one claude.exe):
// version + ifVersion compare-and-set, with an await between read and write.
const hostState = new Map()
// old-router-exhaust (review-side-wake legacy_fence_probe.mjs): another writer
// lands between every read and write, so every compare-and-set misses.
let foreignWrites = 0
const hostStateApi = {
  get:async ref => { await tick(); const held = hostState.get(ref.plugin+'/'+ref.key); return {value:held?.value, version:held?.version || 0} },
  set:async (ref, value, options = {}) => {
    await tick()
    const name = ref.plugin+'/'+ref.key, held = hostState.get(name) || {version:0}
    // Only the legacy fence key is contended; the prompt registry is not under test here.
    if (mode === 'old-router-exhaust' && ref.key === 'legacyWakes') {
      foreignWrites++
      hostState.set(name, {value:[...(Array.isArray(held.value) ? held.value : []), 'f'.repeat(31) + (foreignWrites % 10)],
        version:held.version + 1})
      return {isSet:false, version:held.version + 1}
    }
    if (options.ifVersion !== undefined && options.ifVersion !== held.version) return {isSet:false, version:held.version}
    hostState.set(name, {value, version:held.version + 1})
    return {isSet:true, version:held.version + 1}
  }}
const $ = {
  session:{id:async () => 'parent', messages:async () => messages,
    append:async a => {
      const text = a.message.content[0].text
      appends.push(text)
      if (denyAppend > 0) { denyAppend--; return {deny:true} }
      if (mode === 'append-idle') busyNow = false
      messages.push(row('user', text))
      return {message:a.message, uuid:'append-'+appends.length}
    }},
  prompt:{submit:async a => {
    submits.push(a.text)
    if (heldSubmit) { const held = heldSubmit; heldSubmit = null; return held }
    if (mode === 'drop' && submits.length === 1) return {drop:true}
    if (mode === 'throw' && submits.length === 1) throw new Error('submit outcome unknown')
    if (mode === 'spoof' && submits.length === 1) {
      // Prompt outcome unknown; an unrelated row carries the event text.
      messages.push(row('assistant', '[ciel-side-wake event='+EVENT+' attempt=w1] '+a.text))
      messages.push(row('user', a.text))
      throw new Error('submit outcome unknown')
    }
    messages.push(row('user', a.text))
    return {}
  }},
  ...(variant === 'nostate' ? {} : {state:hostStateApi}),
  mcp:{connect:async () => ({}), call:async (server, name, args) => {
    calls.push({name, args})
    if (name === 'work_side_claim') {
      const claim = claimed ? null : {receipt_id:EVENT, generation:'g', text:'raw secret thread', event_key:'x',
        lease_until:Date.now()/1000+120, source_read:null, route:{},
        workload:{version:1, truncated:false, nodes:[{id:'n7', parent_id:null, title:'report'}], queue_order:[]}}
      claimed = true
      return reply({claim, settings:{}, handoff_replies:[], main_wakes:offerWake && offered() ? [view()] : []})
    }
    if (name === 'work_side_resolve') return reply({receipt:{receipt_id:EVENT},
      result:{action:'answer', item_id:'n7', duplicate:false, main_handoff:handoff}})
    if (name === 'work_side_wake') return wakeCall(args)
    if (name === 'work_side_reply') return reply({receipt:{}, arguments:null})
    return reply({})
  }},
  tool:{call:async () => ({})}, agent:{list:async () => [], spawn:async () => ({agentId:'actor'})},
  env:{get:async () => 'x'.repeat(40)}, ui:{invalidate:() => {}, log:text => logs.push(text), status:() => {}},
  clock:{after:() => ({cancel(){}})},
}
const tick = () => new Promise(resolve => setTimeout(resolve, 5))
// No call, submit or append added for 8 consecutive ticks. A prompt is issued
// only after its prompt-registry claim and attempt_ref digest, so a fixed
// single tick no longer covers it.
const settle = async () => {
  for (let stable = 0, last = ''; stable < 8;) {
    await tick()
    const now = calls.length+':'+submits.length+':'+appends.length
    stable = now === last ? stable + 1 : 0; last = now
  }
}
const until = async (test, label) => {
  for (let i = 0; i < 400; i++) { if (test()) return; await tick() }
  throw new Error('timeout '+label)
}
const count = state => calls.filter(c => c.name === 'work_side_wake' && c.args.state === state).length
const answer = {agentId:'actor', reason:'answer', answer:JSON.stringify({decision:{action:'answer', kind:'info',
  item_id:'n7', summary:'deadline reported', reply:'noted',
  time_limit:{text:'within 4 hours', evidence:'message 41: within 4 hours'}}, expected_version:1})}
const poll = (instance = first) => instance.pollSide($, 'claude:parent')
const extra = {}
await first.startSide($)
if (second) await second.startSide($)
if (['busy', 'idle'].includes(mode)) {
  await poll(); await first.completeSide($, answer)
} else if (mode === 'race') {
  // Completion and a concurrent claim offer the same freshly committed wake.
  await poll(); offerWake = true
  prepareGate = new Promise(resolve => { openPrepare = resolve })
  const completing = first.completeSide($, answer)
  await until(() => count('prepare') === 1, 'first prepare')
  let returned = false
  const polling = poll().then(() => { returned = true })
  await until(() => returned || count('prepare') === 2, 'second caller')
  extra.prepares_while_paused = count('prepare'); extra.poll_returned_while_paused = returned
  prepareGate = null; openPrepare(); await completing; await polling
} else if (mode === 'reload') {
  // Two module instances both paused inside prepare.
  offerWake = true; claimed = true
  prepareGate = new Promise(resolve => { openPrepare = resolve })
  const a = poll(first), b = poll(second)
  await until(() => count('prepare') === 2, 'both prepares')
  extra.prepares_while_paused = count('prepare')
  prepareGate = null; openPrepare(); await a; await b; await settle()
} else if (mode === 'reload-intent') {
  // Same process: the old module holds an intent and pauses before start; the
  // reloaded module supersedes it. The old start must be refused.
  offerWake = true; claimed = true
  startGate = new Promise(resolve => { openStart = resolve })
  const old = poll(first)
  await until(() => count('start') === 1, 'old start paused')
  // Inside the old module's lease the reloaded one does not supersede it.
  await poll(second)
  extra.inside_lease = {prepares:count('prepare'), deliveries:appends.length + submits.length}
  clock = LEASE
  await poll(second); await until(() => appends.length + submits.length === 1, 'new delivery')
  openStart(); await old; await settle()
  extra.start_calls = count('start')
} else if (mode === 'reload-failed' || mode === 'stale-loser') {
  // A owns started and waits for idle main. B loses without caching it; A's
  // prompt is dropped (definite failure); B must then re-elect exactly once.
  offerWake = true; claimed = true
  heldSubmit = new Promise(resolve => { releaseSubmit = () => resolve({drop:true}) })
  await poll(first); await until(() => submits.length === 1, 'A submit')
  // stale-loser: B's claim snapshot still shows pending, so B reaches prepare
  // and gets already_submitting, which must not be cached.
  staleOnce = mode === 'stale-loser'
  await poll(second)
  extra.loser_calls = calls.filter(c => c.name === 'work_side_wake').map(c => c.args.state)
  releaseSubmit(); await until(() => wake.state === 'failed', 'A failed')
  for (let i = 0; i < 5; i++) await poll(second)
} else if (mode === 'livelock') {
  // Reviewer's adversarial schedule: each instance prepares while the other
  // holds an intent, then starts. Without a lease every prepare superseded the
  // other and no start ever succeeded.
  offerWake = true; claimed = true
  startGate = new Promise(resolve => { openStart = resolve })
  const a = poll(first)
  await until(() => count('start') === 1, 'A start paused')
  for (let round = 0; round < 6 && !(appends.length + submits.length); round++) {
    clock += 1000
    await poll(second)
    if (round === 0) { openStart(); await a }
  }
  await until(() => appends.length + submits.length === 1, 'bounded start')
  extra.rounds_clock = clock; extra.prepare_calls = count('prepare'); extra.start_calls = count('start')
} else if (mode === 'expired-intent') {
  // Crashed owner left an intent. Inside the lease nobody supersedes it;
  // after the lease a live owner recovers it and delivers once.
  offerWake = true; claimed = true
  await poll(first)
  extra.inside_lease = {deliveries:submits.length + appends.length, owner:wake.owner_nonce}
  clock = LEASE
  await poll(first)
} else if (mode === 'old-router' || mode === 'old-router-dup') {
  // New host on an old/reused router.
  offerWake = true; claimed = true
  if (mode === 'old-router-dup') {
    // Delivered, then all record calls failed, so the old row stays
    // submitting. A stale offer makes the same module prepare again; the old
    // router returns the same submitting attempt (its duplicate answer).
    await poll(first); await until(() => submits.length === 1, 'legacy submit'); await settle()
    extra.after_record_failure = {state:wake.state, deliveries:submits.length + appends.length}
    for (let i = 0; i < 3; i++) { await poll(first); await settle() }
  } else for (let i = 0; i < 3; i++) { await poll(first); await settle() }
} else if (mode === 'old-router-reload') {
  // Review measurement: two module instances on an old router. Both prepares
  // answer the same submitting attempt; only the host-held claim's winner delivers.
  offerWake = true; claimed = true
  prepareGate = new Promise(resolve => { openPrepare = resolve })
  const a = poll(first), b = poll(second)
  await until(() => count('prepare') === 2, 'both prepares')
  prepareGate = null; openPrepare(); await a; await b
  // Without $.state the per-key prompt registry fails closed: nothing is sent.
  if (variant !== 'nostate') await until(() => submits.length + appends.length >= 1, 'legacy delivery')
  for (let i = 0; i < 20; i++) await tick()
  extra.host_state = [...hostState.entries()]
} else if (mode === 'old-router-exhaust') {
  // Every claim try misses: the attempt is still delivered once (at-least-once)
  // and recorded through the legacy path; repeated polls never deliver again.
  offerWake = true; claimed = true
  for (let i = 0; i < 6; i++) { await poll(first); await settle() }
  extra.foreign_writes = foreignWrites
} else if (mode === 'malformed') {
  offerWake = true; claimed = true
  await poll(first); await settle()
  extra.logs = logs
} else if (mode === 'orphan-started') {
  // Started by a module that is gone: no automatic delivery, ever.
  offerWake = true; claimed = true
  for (let i = 0; i < 3; i++) await poll(first)
} else if (mode === 'lost-prepare') {
  // prepare committed an intent but its response was lost; same owner adopts.
  offerWake = true; claimed = true
  await poll(); await settle()
  extra.after_loss = {deliveries:submits.length + appends.length, state:wake.state, phase:wake.phase}
  await poll()
} else if (mode === 'retry') {
  offerWake = true; claimed = true
  await poll(); await settle()
  extra.after_failure = {prepares:count('prepare'), deliveries:submits.length + appends.length, state:wake.state}
  await poll()
} else {
  // record-fail, drop, throw, spoof, deny, append-idle
  offerWake = true; claimed = true
  await poll(); await settle()
  for (let i = 0; i < 3; i++) { await poll(); await settle() }
}
await settle()
console.log(JSON.stringify({appends, submits, durable:wake,
  wakes:calls.filter(c => c.name === 'work_side_wake').map(c => ({state:c.args.state, attempt_id:c.args.attempt_id || null})),
  claims:calls.filter(c => c.name === 'work_side_claim').length, ...extra}))

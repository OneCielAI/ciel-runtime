// Revised draft side-intake.js (b3ef7d0b): old-router legacy fence via $.state.
// Modes:
//  shared      two module instances share one host $.state (ifVersion CAS) -> expect 1 submit
//  throws      $.state throws in both -> module-Set-only degrade (expected 2 submits)
//  exhaust     one instance; every set misses because another writer lands each time -> ?
//  record-fail append channel (protocol 2): record fails 4x; polls repeat -> 1 append, settled
import * as first from './side-intake.js'
const mode = process.argv[2]
const second = ['shared','throws'].includes(mode) ? await import('./side-intake.js?reload') : null
const busy = mode === 'record-fail'
first.sideMainBusy(() => busy); if (second) second.sideMainBusy(() => busy)
const EVENT = 'e'.repeat(64), ATTEMPT = 'a'.repeat(32)
const submits = [], appends = [], calls = []
let wake = { state:'pending' }
const handoff = { kind:'time_limit', item_id:'n7', root_id:'n7', title:'r', summary:'s', time_limit:{ text:'t', evidence:'e' }, waits:[], question:'' }
const reply = (v, e = false) => ({ isError:e, content:[{ type:'text', text:typeof v === 'string' ? v : JSON.stringify(v) }] })
// Shared host state: ref key -> {value, version}; CAS like the engine (state-fence probes).
const store = new Map()
let foreignWrites = 0
const hostState = {
  get:async ref => { const k = ref.plugin + '/' + ref.key; const s = store.get(k) || { value:undefined, version:0 }; return { value:s.value, version:s.version } },
  set:async (ref, value, opts = {}) => {
    const k = ref.plugin + '/' + ref.key; const s = store.get(k) || { value:undefined, version:0 }
    if (mode === 'exhaust') { // another writer lands between every get and set
      foreignWrites++; store.set(k, { value:[...(Array.isArray(s.value) ? s.value : []), 'f'.repeat(31) + (foreignWrites % 10)], version:s.version + 1 })
      return { isSet:false, version:s.version + 1 }
    }
    if (opts.ifVersion !== undefined && opts.ifVersion !== s.version) return { isSet:false, version:s.version }
    store.set(k, { value, version:s.version + 1 }); return { isSet:true, version:s.version + 1 }
  } }
let recordFaults = mode === 'record-fail' ? 4 : 0
let gate = null, release = () => {}
if (['shared','throws'].includes(mode)) gate = new Promise(r => { release = r })
let prepares = 0
const wakeCall = async a => {
  if (mode === 'record-fail') { // protocol-2 router
    if (a.state === 'prepare') {
      if (wake.state === 'pending') wake = { state:'submitting', phase:'intent', attempt_id:'b'.repeat(32), owner:a.owner_nonce, channel:a.channel, revision:1 }
      return reply({ protocol:2, state:wake.state, phase:wake.phase, attempt_id:wake.attempt_id, owner_nonce:wake.owner, channel:wake.channel, revision:wake.revision })
    }
    if (a.state === 'start') { wake = { ...wake, phase:'started', revision:2 }; return reply({ protocol:2, state:'submitting', phase:'started', attempt_id:wake.attempt_id, owner_nonce:wake.owner, channel:wake.channel, revision:2 }) }
    if (recordFaults > 0) { recordFaults--; return reply('record transport failure', true) }
    if (wake.state === a.state && wake.attempt_id === a.attempt_id) return reply({ protocol:2, state:wake.state })
    if (wake.state !== 'submitting' || wake.phase !== 'started' || a.attempt_id !== wake.attempt_id || a.owner_nonce !== wake.owner) return reply('stale side wake attempt', true)
    wake = { ...wake, state:a.state, receipt:a.receipt, revision:3 }; return reply({ protocol:2, state:a.state })
  }
  // exact old router (side_wake 4b85569d)
  if (a.state === 'prepare') {
    prepares++
    if (gate) await gate
    if (['submitting','queued','appended','uncertain'].includes(wake.state)) return reply({ event_id:EVENT, ...wake })
    wake = { ...wake, state:'submitting', attempt_id:ATTEMPT, started_at:Date.now() / 1000 }
    return reply({ event_id:EVENT, ...wake })
  }
  if (!['queued','appended','uncertain','failed'].includes(a.state)) return reply('invalid side wake state', true)
  if (wake.state !== 'submitting' || a.attempt_id !== wake.attempt_id) return reply('stale side wake attempt', true)
  wake = { ...wake, state:a.state }; return reply({ event_id:EVENT, ...wake })
}
const offered = () => mode === 'record-fail'
  ? (['pending','failed'].includes(wake.state) || (wake.state === 'submitting' && ['intent','started'].includes(wake.phase)))
  : ['pending','failed'].includes(wake.state)
const view = () => { const { owner, ...w } = wake; return { event_id:EVENT, state:'requested', ...handoff, wake:w } }
const host = tag => ({
  session:{ id:async () => 'parent', messages:async () => [], append:async a => { appends.push(tag); return { message:a.message, uuid:'u-' + appends.length } } },
  prompt:{ submit:async a => { submits.push(tag); return { text:a.text } } },
  state:mode === 'throws' ? { get:async () => { throw new Error('no host state') }, set:async () => { throw new Error('no host state') } } : hostState,
  mcp:{ connect:async () => ({}), call:async (s, name, a) => { calls.push(tag + ':' + name + ':' + (a.state || ''))
    if (name === 'work_side_claim') return reply({ claim:null, settings:{}, handoff_replies:[], main_wakes:offered() ? [view()] : [] })
    if (name === 'work_side_wake') return wakeCall(a)
    return reply({}) } },
  tool:{ call:async () => ({}) }, agent:{ list:async () => [], spawn:async () => ({ agentId:'x' }) }, env:{ get:async () => 'x'.repeat(40) },
  ui:{ invalidate(){}, log(){}, status(){} }, clock:{ after:() => ({ cancel(){} }) } })
const $A = host('A'), $B = host('B'), tick = () => new Promise(r => setTimeout(r, 5))
await first.startSide($A); if (second) await second.startSide($B)
if (second) {
  const a = first.pollSide($A, 'claude:parent'), b = second.pollSide($B, 'claude:parent')
  while (prepares < 2) await tick()
  release(); await a; await b
} else {
  for (let i = 0; i < 6; i++) { await first.pollSide($A, 'claude:parent'); await tick() }
}
for (let i = 0; i < 6; i++) await tick()
console.log(JSON.stringify({ mode, submits, appends, wake:{ state:wake.state, receipt:wake.receipt || null },
  fence:store.get('ciel-work/legacyWakes') || null, foreignWrites,
  wakeCalls:calls.filter(c => c.includes('work_side_wake')).length }))

// Review-only adversarial cases for the per-key prompt registry (freeze cb2f0172).
// Imports side-intake.js twice: two module instances of one claude.exe.
import * as first from './side-intake.js'
const second = await import('./side-intake.js?second')
const mode = process.argv[2]
const turn = () => new Promise(resolve => setImmediate(resolve))
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
async function until(test, ms = 2000) {
  const end = Date.now() + ms
  while (Date.now() < end) { if (await test()) return true; await sleep(5) }
  return false
}
let busy1 = false, busy2 = false
first.sideMainBusy(() => busy1)
second.sideMainBusy(() => busy2)
function hostState(options = {}) {
  const map = new Map()
  const name = ref => ref.plugin + '/' + ref.key
  let gate = null
  return { map, options,
    rows:() => map.get('ciel-work/promptPending')?.value || [],
    reset:() => map.clear(),
    // Hold the next set until released (to land callers inside settle's clear).
    holdNextSet:() => { let release; const p = new Promise(r => { release = r }); gate = p; return release },
    api:{
      get:async ref => { await turn(); if (options.throws) throw new Error('x')
        const held = map.get(name(ref)); return { value:held ? structuredClone(held.value) : undefined, version:held?.version || 0 } },
      set:async (ref, value, o = {}) => { await turn(); if (gate) { const g = gate; gate = null; await g }
        if (options.throws) throw new Error('x')
        const held = map.get(name(ref)) || { version:0 }
        if (o.ifVersion !== undefined && o.ifVersion !== held.version) return { isSet:false, version:held.version }
        map.set(name(ref), { value:structuredClone(value), version:held.version + 1 }); return { isSet:true, version:held.version + 1 } } } }
}
function clock() {
  const timers = []
  return { timers,
    after(ms, fn) { const t = { ms, fn, cancelled:false, fired:false }; timers.push(t); return { cancel() { t.cancelled = true } } },
    live() { return timers.filter(t => t.ms === 5000 && !t.cancelled && !t.fired).length },
    // Fire every timer ever created for 5000 ms that has not fired, INCLUDING
    // cancelled ones when force (an engine whose cancel is late/no-op).
    async fire(force = false) {
      for (const t of timers.filter(t => t.ms === 5000 && !t.fired && (force || !t.cancelled))) { t.fired = true; await t.fn() }
      await sleep(20)
    } }
}
function prompts() {
  const calls = []
  return { calls, submit:args => new Promise((resolve, reject) => calls.push({ text:args.text,
    release:o => o === 'throw' ? reject(new Error('x')) : resolve(o === 'drop' ? { drop:'f' } : { text:args.text }) })) }
}
const host = (state, timer, prompt) => ({ ...(state ? { state:state.api } : {}), clock:timer, prompt:{ submit:prompt.submit } })
const box = p => { const b = { value:null }; p.then(v => { b.value = v }); return b }
const keys = s => s.rows().map(r => r.key)

const cases = {
  // B2 without any explicit retry: a duplicate arriving while the expiry is
  // clearing the registry (settled=true, entry still mapped) auto-resubmits.
  async 'dup-during-expiry-clear'() {
    const s = hostState(), t = clock(), p = prompts(), $ = host(s, t, p)
    const a = box(first.sidePromptSubmit($, 'side:K', 'K'))
    await until(() => p.calls.length === 1)
    const release = s.holdNextSet()                 // hold the clear's CAS write
    const firing = t.fire()                          // idle window expires -> settle -> clear (held)
    await sleep(10)
    const dup = box(first.sidePromptSubmit($, 'side:K', 'K duplicate offer'))   // e.g. a pump re-offer
    release(); await firing
    await until(() => p.calls.length === 2, 500)
    return { first:a.value, dup:dup.value, rawSubmits:p.calls.map(c => c.text), registry:keys(s) }
  },
  // Same, with no $.state at all: settle has no awaited clear, so the window is
  // only the synchronous gap; check whether a duplicate after expiry resubmits.
  async 'dup-after-expiry'() {
    const out = {}
    for (const v of ['state', 'nostate']) {
      const s = v === 'state' ? hostState() : null, t = clock(), p = prompts(), $ = host(s, t, p)
      const a = box(first.sidePromptSubmit($, 'parallel:' + v, 'L'))
      await until(() => p.calls.length === 1)
      await t.fire(); await until(() => a.value)
      const after = box(first.sidePromptSubmit($, 'parallel:' + v, 'L again'))
      await until(() => p.calls.length === 2, 300)
      out[v] = { first:a.value, rawSubmits:p.calls.length, afterValue:after.value }
    }
    return out
  },
  // /clear: $.state reset in the SAME module; session.start -> startSide -> hydrate.
  // Is the local unresolved key re-claimed? Then a reload: does the new
  // instance resubmit the same key?
  async 'clear-then-reload'() {
    const s = hostState(), t1 = clock(), p1 = prompts(), $1 = host(s, t1, p1)
    const a = box(first.sidePromptSubmit($1, 'side:C', 'C', { ref:'c'.repeat(16) }))
    await until(() => p1.calls.length === 1)
    const before = keys(s)
    s.reset()                                          // /clear resets $.state
    await first.sidePromptHydrate($1)                  // what startSide does at session.start
    const afterClearHydrate = { registry:keys(s), localPending:first.sidePromptPending('side:C') }
    // Hot reload: new instance hydrates and the same key is offered again.
    const t2 = clock(), p2 = prompts(), $2 = host(s, t2, p2)
    await second.sidePromptHydrate($2)
    const hydratedInSecond = second.sidePromptPending('side:C')
    const dup = box(second.sidePromptSubmit($2, 'side:C', 'C offered again', { ref:'c'.repeat(16) }))
    await until(() => p2.calls.length === 1, 300)
    return { before, afterClearHydrate, hydratedInSecond, secondInstanceRawSubmits:p2.calls.length, firstStill:a.value }
  },
  // Stale timer: an engine whose cancel() is ineffective fires an old window
  // callback while main is idle again after a turn, for an entry whose prompt
  // has not resolved. Does it expire inside the new window early?
  async 'stale-timer-after-turn'() {
    const s = hostState(), t = clock(), p = prompts(), $ = host(s, t, p)
    const a = box(first.sidePromptSubmit($, 'side:S', 'S'))
    await until(() => p.calls.length === 1)
    busy1 = true; first.sidePromptTurnStart()          // cancels window 1
    busy1 = false; first.sidePromptTurnComplete($)     // arms window 2
    const liveAfter = t.live()
    // Fire ONLY the cancelled first timer (late-cancel engine).
    const old = t.timers.find(x => x.cancelled && !x.fired); old.fired = true; await old.fn(); await sleep(20)
    return { liveAfter, valueAfterStaleFire:a.value, pending:first.sidePromptPending('side:S') }
  },
  // Busy-turn expiry: arm only while idle; a callback that runs during a busy
  // turn must not expire. Also check issue while busy arms nothing.
  async 'busy-expiry'() {
    const s = hostState(), t = clock(), p = prompts(), $ = host(s, t, p)
    const a = box(first.sidePromptSubmit($, 'side:B', 'B'))
    await until(() => p.calls.length === 1)
    busy1 = true                                       // turn started but turn.start hook not yet run
    await t.fire()                                     // armed timer fires during busy turn
    const duringBusy = { value:a.value, pending:first.sidePromptPending('side:B') }
    busy1 = false
    return { duringBusy, liveAfterBusyFire:t.live() }
  },
  // Engine accepts the prompt late after expiry: queued must not overwrite uncertain.
  async 'late-raw-after-expiry'() {
    const s = hostState(), t = clock(), p = prompts(), $ = host(s, t, p)
    const results = []
    const a = first.sidePromptSubmit($, 'parallel:LR', 'LR'); a.then(v => results.push(v))
    await until(() => p.calls.length === 1)
    await t.fire(); await sleep(10)
    p.calls[0].release('ok'); await sleep(20)
    return { results, rawSubmits:p.calls.length, registry:keys(s), pending:first.sidePromptPending('parallel:LR') }
  },
  // Fail-closed contract: CAS contended / $.state unavailable / full -> no raw
  // submit; uncertain after a verified idle window.
  async 'fail-closed'() {
    const out = {}
    const full = hostState()
    full.map.set('ciel-work/promptPending', { value:Array.from({ length:200 }, (_, i) => ({ key:'parallel:F' + i, generation:'g' + i })), version:7 })
    const contended = hostState()
    const base = contended.api.set
    contended.api.set = async (ref, value, o = {}) => { const k = ref.plugin + '/' + ref.key; const h = contended.map.get(k) || { version:0 }
      contended.map.set(k, { value:h.value ?? [], version:h.version + 1 }); return { isSet:false } }
    for (const [name, s] of [['full', full], ['contended', contended], ['unavailable', hostState({ throws:true })], ['nostate', null]]) {
      const t = clock(), p = prompts(), $ = host(s, t, p)
      const a = box(first.sidePromptSubmit($, 'side:' + name, name))
      await sleep(80)
      const rawBeforeWindow = p.calls.length
      await t.fire(); await sleep(20)
      out[name] = { rawBeforeWindow, rawAfterWindow:p.calls.length, value:a.value }
    }
    return out
  },
  // $.clock.after throws while main is idle: must settle uncertain, no resubmit.
  async 'clock-failure'() {
    const s = hostState(), p = prompts()
    const $ = { state:s.api, prompt:{ submit:p.submit }, clock:{ after() { throw new Error('timer refused') } } }
    const a = box(first.sidePromptSubmit($, 'side:CF', 'CF'))
    await sleep(80)
    return { raw:p.calls.length, value:a.value, pending:first.sidePromptPending('side:CF'), registry:keys(s) }
  },
  // Pane-retry ruling: after expiry, the SAME ref must not raw-submit again;
  // a person-confirmed retry under a NEW ref may.
  async 'retry-ref'() {
    const s = hostState(), t = clock(), p = prompts(), $ = host(s, t, p)
    const a = box(first.sidePromptSubmit($, 'side:RR', 'RR', { ref:'a'.repeat(16) }))
    await until(() => p.calls.length === 1)
    await t.fire(); await until(() => a.value)
    const same = box(first.sidePromptSubmit($, 'side:RR', 'RR same ref', { ref:'a'.repeat(16) }))
    await sleep(80)
    const afterSame = p.calls.length
    const fresh = box(first.sidePromptSubmit($, 'side:RR', 'RR new ref', { ref:'b'.repeat(16) }))
    await sleep(80)
    return { first:a.value, rawAfterSameRef:afterSame, rawAfterNewRef:p.calls.length, sameValue:same.value, freshValue:fresh.value }
  },
  // Lead question 1: can reassert flip registered=false and lose the clear?
  // (a) Hydrated foreign row whose generation was replaced by another instance
  //     between hydrate and reassert -> 'existing' -> registered=false.
  // (b) Reassert after a /clear that a second instance already re-claimed with
  //     ITS hydrated generation (same generation value) -> 'claimed'.
  // (c) Reassert while $.state throws -> registered=false; later settle never
  //     clears the stale row restored later.
  async 'reassert-registered'() {
    const out = {}
    // (c) own claimed entry; /clear; reassert throws; $.state recovers; then the
    // prompt resolves queued: is the restored-by-someone row cleared?
    {
      const s = hostState(), t = clock(), p = prompts(), $ = host(s, t, p)
      const a = box(first.sidePromptSubmit($, 'parallel:RC', 'RC'))
      await until(() => p.calls.length === 1)
      const gen = s.rows()[0].generation
      s.reset(); s.options.throws = true
      await first.sidePromptReassert($)
      s.options.throws = false
      // A later successful reassert (next session.start) re-claims with gen.
      await first.sidePromptReassert($)
      const afterSecondReassert = { registry:keys(s), sameGen:s.rows()[0]?.generation === gen }
      p.calls[0].release('ok'); await until(() => a.value)
      out.throwsThenRecover = { afterSecondReassert, value:a.value, registryAfterSettle:keys(s) }
    }
    // (d) reassert lands 'existing' because a reloaded instance re-registered
    // the SAME key under its own NEW generation after a /clear (e.g. it issued
    // a new attempt). First instance's entry: registered=false -> its settle
    // must not clear the other's row (correct) and must still resolve.
    {
      const s = hostState(), t1 = clock(), p1 = prompts(), t2 = clock(), p2 = prompts()
      const $1 = host(s, t1, p1), $2 = host(s, t2, p2)
      const a = box(first.sidePromptSubmit($1, 'side:RE', 'RE', { ref:'a'.repeat(16) }))
      await until(() => p1.calls.length === 1)
      s.reset()                                           // /clear without reassert yet
      const b = box(second.sidePromptSubmit($2, 'side:RE', 'RE second', { ref:'b'.repeat(16) }))   // reloaded instance claims new gen
      await until(() => p2.calls.length === 1, 300)
      await first.sidePromptReassert($1)                  // first instance's reassert finds other gen
      const firstPendingExactRef = first.sidePromptPending('side:RE', 'a'.repeat(16))
      p1.calls[0].release('ok'); await until(() => a.value)
      out.otherGeneration = { secondRaw:p2.calls.length, registryAfterFirstSettle:keys(s), firstValue:a.value, firstPendingExactRef }
      p2.calls[0]?.release('ok'); await until(() => b.value)
    }
    return out
  },
  // Lead question 2: a hydrated entry (rawLive true, foreign ref) in this
  // module: does a person-confirmed NEW ref get blocked until the window?
  async 'hydrated-blocks-new-ref'() {
    const s = hostState(), t1 = clock(), p1 = prompts(), t2 = clock(), p2 = prompts()
    const $1 = host(s, t1, p1), $2 = host(s, t2, p2)
    const old = box(first.sidePromptSubmit($1, 'side:H', 'H', { ref:'a'.repeat(16) }))
    await until(() => p1.calls.length === 1)
    await second.sidePromptHydrate($2)                     // reload: hydrated foreign entry, rawLive
    const pendingOld = second.sidePromptPending('side:H', 'a'.repeat(16)), pendingNew = second.sidePromptPending('side:H', 'b'.repeat(16))
    const retry = box(second.sidePromptSubmit($2, 'side:H', 'H retry new ref', { ref:'b'.repeat(16) }))
    await sleep(60)
    const rawBeforeWindow = p2.calls.length, retryValueBeforeWindow = retry.value
    await t2.fire(); await sleep(60)
    return { pendingOld, pendingNewRef:pendingNew, rawBeforeWindow, retryValueBeforeWindow,
      rawAfterWindow:p2.calls.length, retryValueAfterWindow:retry.value }
  },
  // Reassert during a transient $.state failure WITHOUT a /clear (row still
  // present): registered flips false, settle skips the clear -> orphan row.
  // Then the next same-key delivery in this module (e.g. a person-confirmed
  // retry under a new ref) finds the orphan as 'existing' and never submits.
  async 'reassert-orphan'() {
    const s = hostState(), t = clock(), p = prompts(), $ = host(s, t, p)
    const a = box(first.sidePromptSubmit($, 'side:O', 'O', { ref:'a'.repeat(16) }))
    await until(() => p.calls.length === 1)
    s.options.throws = true
    await first.sidePromptReassert($)          // e.g. classic.SessionStart (compact/resume) with a transient failure
    s.options.throws = false
    p.calls[0].release('ok'); await until(() => a.value)
    const afterSettle = { value:a.value, registry:keys(s), localPending:first.sidePromptPending('side:O') }
    const retry = box(first.sidePromptSubmit($, 'side:O', 'O retry new ref', { ref:'b'.repeat(16) }))
    await sleep(80)
    const rawBeforeWindow = p.calls.length
    await t.fire(); await sleep(40)
    return { afterSettle, rawBeforeWindow, rawAfterWindow:p.calls.length, retryValue:retry.value, registryAfter:keys(s) }
  },
}
console.log(JSON.stringify({ mode, ...(await cases[mode]()) }))
process.exit(0)

// Per-key prompt registry in side-intake.js (sidePromptSubmit and friends),
// driven directly with a scripted host. Two imports are two module instances
// of one claude.exe (a hot reload); a fresh state store is a new process.
// argv[2] names the case; each prints one JSON line.
import * as first from './side-intake.js'
const second = await import('./side-intake.js?second')
const third = await import('./side-intake.js?third')
const mode = process.argv[2]
const turn = () => new Promise(resolve => setImmediate(resolve))
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
async function until(test, ms = 3000) {
  const end = Date.now() + ms
  while (Date.now() < end) { if (await test()) return true; await sleep(5) }
  return false
}
let busyFirst = false, busySecond = false
first.sideMainBusy(() => busyFirst)
second.sideMainBusy(() => busySecond)
third.sideMainBusy(() => false)
// Host-held $.state: version + ifVersion compare-and-set; options make it
// throw, or land a foreign write before every compare-and-set.
function hostState(options = {}) {
  const map = new Map()
  const name = ref => ref.plugin + '/' + ref.key
  const box = { map, options, holdSet:null,
    rows:() => map.get('ciel-work/promptPending')?.value || [],
    version:() => map.get('ciel-work/promptPending')?.version || 0,
    api:{
      get:async ref => {
        await turn()
        if (options.throws) throw new Error('state unavailable')
        const held = map.get(name(ref))
        return { value:held ? structuredClone(held.value) : undefined, version:held?.version || 0 }
      },
      set:async (ref, value, setOptions = {}) => {
        await turn()
        // Hold one write (a terminal row being written while a duplicate arrives).
        if (box.holdSet) { const hold = box.holdSet; box.holdSet = null; await hold }
        if (options.throws) throw new Error('state unavailable')
        const held = map.get(name(ref)) || { version:0 }
        if (options.foreign) {
          map.set(name(ref), { value:held.value ?? [], version:held.version + 1 })
          return { isSet:false, version:held.version + 1 }
        }
        if (setOptions.ifVersion !== undefined && setOptions.ifVersion !== held.version) return { isSet:false, version:held.version }
        map.set(name(ref), { value:structuredClone(value), version:held.version + 1 })
        return { isSet:true, version:held.version + 1 }
      } } }
  return box
}
function clock() {
  const timers = []
  return { timers,
    after(ms, fn) { const t = { ms, fn, cancelled:false, fired:false }; timers.push(t); return { cancel() { t.cancelled = true } } },
    live() { return timers.filter(t => t.ms === 5000 && !t.cancelled && !t.fired).length },
    async fire() {
      for (const t of timers.filter(t => t.ms === 5000 && !t.cancelled && !t.fired)) { t.fired = true; await t.fn() }
      await sleep(20)
    } }
}
function prompts() {
  const calls = []
  return { calls, submit:args => new Promise((resolve, reject) => calls.push({ text:args.text,
    release:outcome => outcome === 'throw' ? reject(new Error('unknown'))
      : resolve(outcome === 'drop' ? { drop:'fixture' } : { text:args.text }) })) }
}
const host = (state, timer, prompt) => ({ ...(state ? { state:state.api } : {}), clock:timer, prompt:{ submit:prompt.submit } })
// Pending rows only; terminal rows are the bounded terminal records.
const keysOf = state => state.rows().filter(row => row.status !== 'terminal').map(row => row.key)
const terminalsOf = state => state.rows().filter(row => row.status === 'terminal').map(row => row.key + ':' + row.outcome)
const outcome = promise => { const box = { value:null }; promise.then(value => { box.value = value }); return box }

const cases = {
  // Same-module duplicates get the exact Promise object the first call
  // returned (no wrapper); one submit only.
  async identity() {
    const state = hostState(), timer = clock(), prompt = prompts()
    const $ = host(state, timer, prompt)
    const key = 'side:state'
    const a = first.sidePromptSubmit($, key, 'first')
    const b = first.sidePromptSubmit($, key, 'second')
    await until(() => prompt.calls.length === 1)
    const pendingBefore = first.sidePromptPending(key)
    const registryBefore = keysOf(state)
    prompt.calls[0].release('ok')
    const results = await Promise.all([a, b])
    return { identical:a === b, calls:prompt.calls.map(call => call.text), pendingBefore, registryBefore,
      pendingAfter:first.sidePromptPending(key), registryAfter:keysOf(state), results }
  },
  // A latent key never blocks another key; its idle window exposes it.
  async independent() {
    const state = hostState(), timer = clock(), prompt = prompts()
    const $ = host(state, timer, prompt)
    const a = outcome(first.sidePromptSubmit($, 'side:A', 'A'))
    const b = outcome(first.sidePromptSubmit($, 'parallel:B', 'B'))
    const n = outcome(first.sidePromptSubmit($, 'notice:N', 'N', { kind:'notice' }))
    await until(() => prompt.calls.length === 3)
    const issued = prompt.calls.map(call => call.text)
    prompt.calls[1].release('ok'); prompt.calls[2].release('ok')
    await until(() => b.value && n.value)
    const whileA = { a:a.value, b:b.value, n:n.value, registry:keysOf(state), pendingA:first.sidePromptPending('side:A') }
    await timer.fire()
    await until(() => a.value)
    return { issued, whileA, afterWindow:{ a:a.value, registry:keysOf(state), pendingA:first.sidePromptPending('side:A') } }
  },
  // No window runs during a main turn; turn.complete arms a full one. A key
  // issued while main is busy (append fell through) gets no window until then.
  async turns() {
    const state = hostState(), timer = clock(), prompt = prompts()
    const $ = host(state, timer, prompt)
    const a = outcome(first.sidePromptSubmit($, 'side:T', 'T'))
    await until(() => prompt.calls.length === 1)
    const liveAtIssue = timer.live()
    busyFirst = true; first.sidePromptTurnStart()
    const liveInTurn = timer.live()
    await timer.fire()
    const inTurn = { value:a.value, pending:first.sidePromptPending('side:T') }
    const b = outcome(first.sidePromptSubmit($, 'side:U', 'U'))
    await until(() => prompt.calls.length === 2)
    const liveBusyIssue = timer.live()
    busyFirst = false; first.sidePromptTurnComplete($)
    const liveAfterTurn = timer.live()
    await timer.fire()
    await until(() => a.value && b.value)
    return { liveAtIssue, liveInTurn, inTurn, liveBusyIssue, liveAfterTurn, after:{ a:a.value, b:b.value, registry:keysOf(state) } }
  },
  // The same key from two module instances at once: one submit.
  async 'cross-instance'() {
    const state = hostState(), t1 = clock(), t2 = clock(), p1 = prompts(), p2 = prompts()
    const a = outcome(first.sidePromptSubmit(host(state, t1, p1), 'parallel:L1', 'from first'))
    const b = outcome(second.sidePromptSubmit(host(state, t2, p2), 'parallel:L1', 'from second'))
    await sleep(50)
    const submits = p1.calls.length + p2.calls.length
    const owner = p1.calls.length ? p1 : p2, waiter = p1.calls.length ? 'second' : 'first'
    const registryWhile = keysOf(state)
    owner.calls[0].release('ok')
    await until(() => (waiter === 'second' ? a.value : b.value))
    const ownerValue = waiter === 'second' ? a.value : b.value
    const waiterPending = (waiter === 'second' ? second : first).sidePromptPending('parallel:L1')
    await (waiter === 'second' ? t2 : t1).fire()
    await until(() => a.value && b.value)
    return { submits, registryWhile, ownerValue, waiterPendingAfterOwner:waiterPending,
      waiterValue:waiter === 'second' ? b.value : a.value, registryAfter:keysOf(state) }
  },
  // Hot reload: the second instance hydrates the first's unresolved delivery,
  // never resubmits it, and its own idle window releases it as a terminal
  // uncertain. The same key + ref stays terminal in both instances (0 raw);
  // only a new durable attempt (new ref) may issue. The first instance's late
  // raw settlement never overwrites the terminal row or clears the new claim.
  async reload() {
    const state = hostState(), t1 = clock(), t2 = clock(), p1 = prompts(), p2 = prompts()
    const R = 'r'.repeat(16), S = 's'.repeat(16)
    const old = outcome(first.sidePromptSubmit(host(state, t1, p1), 'side:R', 'R', { ref:R }))
    await until(() => p1.calls.length === 1)
    const $2 = host(state, t2, p2)
    const beforeHydrate = second.sidePromptPending('side:R')
    await second.sidePromptHydrate($2)
    const hydrated = { pending:second.sidePromptPending('side:R'), exactRef:second.sidePromptPending('side:R', R),
      otherRef:second.sidePromptPending('side:R', 'x'.repeat(16)), live:t2.live() }
    const duplicate = outcome(second.sidePromptSubmit($2, 'side:R', 'R again', { ref:R }))
    await sleep(30)
    const noResubmit = p2.calls.length
    await t2.fire()
    await until(() => duplicate.value)
    const released = { duplicate:duplicate.value, registry:keysOf(state), terminals:terminalsOf(state),
      pending:second.sidePromptPending('side:R') }
    // Same ref after expiry: terminal outcome, no raw submit, in this and a
    // third (later reloaded) instance.
    const sameSecond = outcome(second.sidePromptSubmit($2, 'side:R', 'R same ref', { ref:R }))
    const sameThird = outcome(third.sidePromptSubmit(host(state, clock(), p2), 'side:R', 'R same ref later', { ref:R }))
    await until(() => sameSecond.value && sameThird.value)
    const sameRef = { second:sameSecond.value, third:sameThird.value, rawSubmits:p2.calls.length }
    // A person-confirmed retry becomes a new durable attempt: a new ref issues.
    const retry = outcome(second.sidePromptSubmit($2, 'side:R', 'R retry', { ref:S }))
    await until(() => p2.calls.length === 1)
    const generation = state.rows().find(row => row.status !== 'terminal')?.generation
    p1.calls[0].release('ok')
    await until(() => old.value)
    const afterStale = { old:old.value, registry:keysOf(state), terminals:terminalsOf(state),
      sameGeneration:state.rows().find(row => row.status !== 'terminal')?.generation === generation,
      retryPending:second.sidePromptPending('side:R', S) }
    p2.calls[0].release('ok')
    await until(() => retry.value)
    return { beforeHydrate, hydrated, noResubmit, released, sameRef, afterStale,
      final:{ retry:retry.value, registry:keysOf(state), terminals:terminalsOf(state) }, submits:p1.calls.length + p2.calls.length }
  },
  // A duplicate of the same delivery while its expiry is still writing the
  // terminal row, and after it: the terminal outcome, never a raw resubmit.
  // A reproduced released-wait notice with the same text is terminal too.
  async 'dup-terminal'() {
    const state = hostState(), timer = clock(), prompt = prompts()
    const $ = host(state, timer, prompt)
    const D = 'd'.repeat(16)
    const a = outcome(first.sidePromptSubmit($, 'side:D', 'D', { ref:D }))
    await until(() => prompt.calls.length === 1)
    let release
    state.holdSet = new Promise(done => { release = done })
    const firing = timer.fire()
    await sleep(10)
    const during = outcome(first.sidePromptSubmit($, 'side:D', 'D during clear', { ref:D }))
    release(); await firing
    await until(() => a.value && during.value)
    const after = outcome(first.sidePromptSubmit($, 'side:D', 'D after', { ref:D }))
    const notice1 = outcome(first.sidePromptSubmit($, 'notice:N1', 'N', { kind:'notice' }))
    await until(() => prompt.calls.length === 2)
    prompt.calls[1].release('ok')
    await until(() => after.value && notice1.value)
    const notice2 = outcome(first.sidePromptSubmit($, 'notice:N1', 'N reproduced', { kind:'notice' }))
    await until(() => notice2.value)
    await sleep(20)
    return { first:a.value, during:during.value, after:after.value, notice:[notice1.value, notice2.value],
      rawSubmits:prompt.calls.map(call => call.text), terminals:terminalsOf(state), registry:keysOf(state) }
  },
  // Terminal rows are capped separately: only the oldest terminal rows are
  // evicted, and never an unresolved pending row.
  async 'terminal-cap'() {
    const state = hostState(), timer = clock(), prompt = prompts()
    const pending = Array.from({ length:3 }, (_, i) => ({ key:'parallel:P' + i, generation:'p' + i, status:'pending' }))
    const terminal = Array.from({ length:200 }, (_, i) => ({ key:'notice:T' + i, generation:'t' + i, status:'terminal',
      outcome:'queued', settledAt:i + 1 }))
    state.map.set('ciel-work/promptPending', { value:[...pending, ...terminal], version:3 })
    const a = outcome(first.sidePromptSubmit(host(state, timer, prompt), 'notice:NEW', 'NEW', { kind:'notice' }))
    await until(() => prompt.calls.length === 1)
    prompt.calls[0].release('ok')
    await until(() => a.value)
    await sleep(20)
    const rows = state.rows()
    return { value:a.value, pending:rows.filter(row => row.status !== 'terminal').map(row => row.key),
      terminalCount:rows.filter(row => row.status === 'terminal').length,
      oldestKept:rows.some(row => row.key === 'notice:T0'), secondOldestKept:rows.some(row => row.key === 'notice:T1'),
      newKept:rows.some(row => row.key === 'notice:NEW' && row.status === 'terminal') }
  },
  // $.clock.after refused at an idle boundary: settle uncertain at once, no raw.
  async 'clock-failure'() {
    const state = hostState(), prompt = prompts()
    const $ = { state:state.api, prompt:{ submit:prompt.submit }, clock:{ after() { throw new Error('timer refused') } } }
    const a = outcome(first.sidePromptSubmit($, 'side:CF', 'CF', { ref:'c'.repeat(16) }))
    await until(() => a.value)
    await sleep(30)
    return { value:a.value, raw:prompt.calls.length, pending:first.sidePromptPending('side:CF'), terminals:terminalsOf(state) }
  },
  // A transient $.state failure during the re-claim must not orphan this
  // entry's pending row: its settlement still writes the exact terminal row.
  async 'reassert-transient'() {
    const state = hostState(), timer = clock(), prompt = prompts()
    const $ = host(state, timer, prompt)
    const a = outcome(first.sidePromptSubmit($, 'side:O', 'O', { ref:'o'.repeat(16) }))
    await until(() => prompt.calls.length === 1)
    state.options.throws = true
    await first.sidePromptReassert($)
    state.options.throws = false
    prompt.calls[0].release('ok')
    await until(() => a.value)
    await sleep(20)
    return { value:a.value, registry:keysOf(state), terminals:terminalsOf(state) }
  },
  // A released-wait notice has no durable fence. /clear resets $.state and
  // reassert handles unresolved entries only. After a hot reload, the same
  // notice text issues once more: documented at-least-once state-loss residual.
  async 'notice-clear'() {
    const state = hostState(), t1 = clock(), t2 = clock(), p1 = prompts(), p2 = prompts()
    const key = 'notice:clear'
    const a = outcome(first.sidePromptSubmit(host(state, t1, p1), key, 'released n3', { kind:'notice' }))
    await until(() => p1.calls.length === 1)
    p1.calls[0].release('ok')
    await until(() => a.value)
    const beforeClear = terminalsOf(state)
    state.map.clear()
    await first.sidePromptReassert(host(state, t1, p1))
    const afterReassert = terminalsOf(state)
    const $2 = host(state, t2, p2)
    await second.sidePromptHydrate($2)
    const b = outcome(second.sidePromptSubmit($2, key, 'released n3', { kind:'notice' }))
    await sleep(40)
    const reloadRaw = p2.calls.length
    p2.calls[0]?.release('ok')
    await until(() => b.value)
    // The original instance still holds its bounded local terminal record.
    const c = outcome(first.sidePromptSubmit(host(state, t1, p1), key, 'released n3', { kind:'notice' }))
    await until(() => c.value)
    return { first:a.value, beforeClear, afterReassert, reloadRaw, reloadValue:b.value,
      sameInstanceValue:c.value, firstInstanceRaw:p1.calls.length }
  },
  // drop -> failed, throw -> uncertain; the key and its row are cleared.
  async faults() {
    const out = {}
    for (const fault of ['drop', 'throw']) {
      const state = hostState(), timer = clock(), prompt = prompts()
      const value = outcome(first.sidePromptSubmit(host(state, timer, prompt), 'parallel:' + fault, fault))
      await until(() => prompt.calls.length === 1)
      prompt.calls[0].release(fault)
      await until(() => value.value)
      out[fault] = { value:value.value, registry:keysOf(state), pending:first.sidePromptPending('parallel:' + fault) }
    }
    return out
  },
  // The registry never evicts an unresolved key. Full, every compare-and-set
  // missing, an oversized key, $.state throwing, or no $.state at all: fail
  // closed. No raw submit in either instance; the public promise becomes
  // uncertain at the idle window and the key is released.
  async unclaimable() {
    const out = {}
    const full = hostState()
    full.map.set('ciel-work/promptPending', { value:Array.from({ length:200 }, (_, i) => ({ key:'parallel:F' + i, generation:'g' + i })), version:7 })
    for (const [name, state, key] of [['full', full, 'parallel:new'], ['contended', hostState({ foreign:true }), 'parallel:c'],
      ['oversized', hostState(), 'side:' + 'x'.repeat(400)], ['throws', hostState({ throws:true }), 'side:t'], ['absent', null, 'side:a']]) {
      const t1 = clock(), p1 = prompts(), t2 = clock(), p2 = prompts()
      const a = outcome(first.sidePromptSubmit(host(state, t1, p1), key, name))
      const dup = first.sidePromptSubmit(host(state, t1, p1), key, name + ' dup')
      const b = outcome(second.sidePromptSubmit(host(state, t2, p2), key, name + ' second'))
      await until(() => t1.live() === 1 && t2.live() === 1)
      await sleep(30)
      const before = { calls:p1.calls.length + p2.calls.length, pending:first.sidePromptPending(key), value:a.value,
        rows:state ? state.rows().length : null, version:name === 'full' ? state.version() : null,
        hasKey:state ? keysOf(state).includes(key) : false, live:t1.live() }
      await t1.fire(); await t2.fire()
      await until(() => a.value && b.value)
      out[name] = { before, value:a.value, secondValue:b.value, dupShared:await dup === a.value,
        callsAfter:p1.calls.length + p2.calls.length, pendingAfter:first.sidePromptPending(key),
        rowsAfter:state ? state.rows().length : null, versionAfter:name === 'full' ? state.version() : null }
    }
    return out
  },
  // The idle window is event-anchored. No timer exists during a main turn; a
  // stale callback (armed before turn.start) does nothing; turn.complete arms
  // exactly one 5 s window; a new turn inside it cancels it. Invoking every
  // armed callback three times while busy stands for a turn longer than 15 s:
  // nothing expires. A raw resolution after the release keeps uncertain.
  async windows() {
    const state = hostState(), timer = clock(), prompt = prompts()
    const $ = host(state, timer, prompt)
    const a = outcome(first.sidePromptSubmit($, 'side:W', 'W'))
    await until(() => prompt.calls.length === 1 && timer.live() === 1)
    const armedAtIdleIssue = timer.timers.filter(t => t.ms === 5000).length
    const staleBeforeTurn = timer.timers.find(t => t.ms === 5000)
    busyFirst = true; first.sidePromptTurnStart()
    const liveInTurn = timer.live()
    for (let i = 0; i < 3; i++) { await staleBeforeTurn.fn(); await timer.fire() }
    const afterLongTurn = { value:a.value, pending:first.sidePromptPending('side:W') }
    busyFirst = false; first.sidePromptTurnComplete($)
    const liveAfterComplete = timer.live()
    const insideWindow = timer.timers.find(t => t.ms === 5000 && !t.cancelled && !t.fired)
    // A new turn inside the window cancels it; that callback then does nothing,
    // even when invoked after main is idle again.
    busyFirst = true; first.sidePromptTurnStart(); busyFirst = false
    await insideWindow.fn()
    const afterCancelled = { value:a.value, pending:first.sidePromptPending('side:W'), live:timer.live() }
    first.sidePromptTurnComplete($)
    await timer.fire()
    await until(() => a.value)
    const released = { value:a.value, pending:first.sidePromptPending('side:W'), registry:keysOf(state) }
    prompt.calls[0].release('ok'); await sleep(20)
    return { armedAtIdleIssue, liveInTurn, afterLongTurn, liveAfterComplete, insideWindowMs:insideWindow.ms,
      afterCancelled, released, lateValue:a.value, lateRegistry:keysOf(state), submits:prompt.calls.length }
  },
  // /clear: $.state is reset while this module's raw prompt is unresolved.
  // sidePromptReassert re-claims the exact generation, so a reload hydrates it
  // and does not submit it again. skip: no re-claim; fails: $.state throws
  // during the re-claim. The reload's raw-submit count is what is compared.
  async 'clear-reload'() {
    const out = {}
    for (const variant of ['reassert', 'skip', 'fails']) {
      const state = hostState(), t1 = clock(), t2 = clock(), p1 = prompts(), p2 = prompts()
      const key = 'parallel:C' + variant
      const $1 = host(state, t1, p1)
      const a = outcome(first.sidePromptSubmit($1, key, 'C'))
      await until(() => p1.calls.length === 1)
      const generation = state.rows()[0]?.generation
      state.map.clear() // /clear resets $.state (measured, nonce-lifetime-probe-44e40778)
      if (variant === 'fails') state.options.throws = true
      if (variant !== 'skip') await first.sidePromptReassert($1)
      if (variant === 'fails') state.options.throws = false
      const afterReassert = { registry:keysOf(state), sameGeneration:state.rows()[0]?.generation === generation,
        pending:first.sidePromptPending(key), live:t1.live() }
      const $2 = host(state, t2, p2)
      await second.sidePromptHydrate($2)
      const hydrated = second.sidePromptPending(key)
      const b = outcome(second.sidePromptSubmit($2, key, 'C again'))
      await sleep(40)
      out[variant] = { afterReassert, hydrated, reloadRawSubmits:p2.calls.length, firstRawSubmits:p1.calls.length,
        registryAfterReload:keysOf(state) }
      p1.calls[0].release('ok'); p2.calls[0]?.release('ok')
      await t1.fire(); await t2.fire()
      await until(() => a.value && b.value)
      out[variant].values = [a.value, b.value]
    }
    return out
  },
}

// Notice path (released waits; non-durable). One completed side event whose
// committed result released a wait, with main idle, through the real
// startSide/pollSide/completeSide and noticeToMain. argv[3]: finite | anomaly |
// restart | reload.
async function releaseNotice(instance, $, logs) {
  await instance.startSide($)
  await instance.pollSide($, 'claude:parent')
  await instance.completeSide($, { agentId:'actor1', reason:'answer', answer:JSON.stringify({ decision:{ action:'update', kind:'request',
    item_id:'n3', summary:'approved', reply:'ok', note:'approved', release_waits:[{ id:'n3', waiting_on:'approval' }] }, expected_version:4 }) })
  await until(() => logs.some(line => line.startsWith('Side released')), 300)
}
function noticeHost(state, timer, prompt, logs) {
  let claimed = false
  return { ...host(state, timer, prompt),
    session:{ model:async () => 'fixture-main', id:async () => 'parent', append:async () => ({}), send:async () => ({ isDelivered:true }) },
    mcp:{ connect:async () => ({}), call:async (server, name, args) => {
      let value = {}
      if (name === 'work_side_claim') {
        const claim = claimed ? null : { receipt_id:'e'.repeat(64), generation:'g1', text:'approved', event_key:'walkie:1',
          lease_until:Date.now() / 1000 + 120, route:{}, workload:{ version:4, truncated:false, active_root:'n1', queue_order:[],
            nodes:[{ id:'n1', parent_id:null, title:'goal' }, { id:'n3', parent_id:'n1', title:'gate', waiting_on:'approval' }] } }
        claimed = true
        value = { claim, settings:{ model:'inherit', context_mode:'isolated' }, handoff_replies:[], main_wakes:[] }
      }
      if (name === 'work_parallel_status') value = { version:1, policy:{}, leases:{} }
      if (name === 'work_side_resolve') value = { receipt:{ receipt_id:args._host.receipt_id, stage:'processed' },
        result:{ action:'update', item_id:'n3', summary:'approved', duplicate:false, released_waits:[{ id:'n3', waiting_on:'approval' }],
          handoff:'not_required' } }
      if (name === 'work_side_reply') value = { receipt:{ receipt_id:args.receipt_id, stage:'processed' }, arguments:null }
      return { content:[{ type:'text', text:JSON.stringify(value) }] }
    } },
    tool:{ list:async () => [], call:async () => ({ text:'stopped' }) },
    agent:{ list:async () => [], register:async () => ({}), spawn:async () => ({ agentId:'actor1' }) },
    env:{ get:async key => key === 'CIEL_RUNTIME_DIRECT_GATEWAY_ACTIVE' ? '0' : 'x'.repeat(40) },
    ui:{ log:text => logs.push(String(text)), status:() => {}, invalidate:() => {} } }
}
cases.notice = async () => {
  const condition = process.argv[3] || 'finite'
  const state = hostState(), t1 = clock(), p1 = prompts(), logs1 = []
  const $1 = noticeHost(state, t1, p1, logs1)
  const done = releaseNotice(first, $1, logs1)
  await until(() => p1.calls.length === 1)
  const issued = { calls:p1.calls.length, registry:keysOf(state).map(key => key.slice(0, 7)), live:t1.live() }
  let result = {}
  if (condition === 'finite') {
    p1.calls[0].release('ok'); await done
    result = { logs:logs1 }
  } else if (condition === 'anomaly') {
    await t1.fire(); await done
    p1.calls[0].release('ok'); await sleep(30)
    result = { logs:logs1 }
  } else {
    // Second module instance: reload shares $.state, restart starts empty. The
    // same release notice is produced again there (same text, same key).
    const fresh = condition === 'reload' ? state : hostState()
    const t2 = clock(), p2 = prompts(), logs2 = []
    const $2 = noticeHost(fresh, t2, p2, logs2)
    const again = releaseNotice(second, $2, logs2)
    await sleep(60)
    const secondCalls = p2.calls.length
    // reload: nothing was issued here; the hydrated key's idle window ends it.
    // restart: no registry carried over, so this process issued it again.
    if (condition === 'reload') await t2.fire()
    else p2.calls[0]?.release('ok')
    await again
    result = { secondCalls, logs2, registryAfter:keysOf(fresh) }
  }
  return { condition, issued, ...result, registry:keysOf(state), submits:p1.calls.length }
}

console.log(JSON.stringify({ mode, ...(await cases[mode]()) }))
process.exit(0)

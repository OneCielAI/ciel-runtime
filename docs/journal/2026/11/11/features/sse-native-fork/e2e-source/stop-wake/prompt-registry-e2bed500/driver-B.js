
import { sidePromptSubmit, sidePromptPending, sidePromptHydrate, sidePromptTurnStart, sidePromptTurnComplete,
  sideMainBusy } from './side-intake.js'
const GEN = "B"
const M = {"A": "PROMPT-REGISTRY-A-e2bed500", "A2": "PROMPT-REGISTRY-A2-e2bed500", "B": "PROMPT-REGISTRY-B-e2bed500", "R": "PROMPT-REGISTRY-R-e2bed500", "R2": "PROMPT-REGISTRY-R2-e2bed500"}
const HOLD_MS = 8000
const RELOAD_HOLD_MS = 9000
const REG = { plugin:'ciel-work', key:'promptPending' }
const SCRATCH = { plugin:'ciel-work', key:'legacyWakes' }
let working = false
sideMainBusy(() => working)
let seq = 0
let chain = Promise.resolve()
function note($, row) {
  const entry = { gen:GEN, seq:++seq, at:Date.now(), ...row }
  chain = chain.then(async () => {
    let prior = ''
    try { prior = await $.fs.read('events-' + GEN + '.jsonl') } catch {}
    await $.fs.write('events-' + GEN + '.jsonl', prior + JSON.stringify(entry) + '\n')
  }).catch(() => {})
  return chain
}
async function registry($) {
  try {
    const held = await $.state.get(REG)
    return { version:held.version, keys:(Array.isArray(held.value) ? held.value : []).map(row => row.key) }
  } catch (error) { return { error:String(error).slice(0, 200) } }
}
const pendingView = () => ({ A:sidePromptPending('parallel:A'), B:sidePromptPending('side:B', 'b'.repeat(16)),
  R:sidePromptPending('parallel:R') })
function marker(text) { return Object.keys(M).find(name => String(text || '').includes(M[name])) || null }
let held = { A:false, R:false }
export function register(on) {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    const before = pendingView()
    await sidePromptHydrate($)
    await note($, { event:'session.start', pendingBeforeHydrate:before, pendingAfterHydrate:pendingView(),
      registry:await registry($) })
    if (GEN === 'A') {
      // Phase F: D1 long-lived read, D2 write in between.
      $.clock.after(1500, async () => {
        let first = null, second = null
        try { first = await $.state.get(SCRATCH) } catch (error) { first = { error:String(error).slice(0, 200) } }
        await note($, { event:'fresh.d1.first', read:first })
        try { await $.clock.sleep(3000) } catch (error) { await note($, { event:'fresh.d1.sleep-error', error:String(error).slice(0, 200) }) }
        try { second = await $.state.get(SCRATCH) } catch (error) { second = { error:String(error).slice(0, 200) } }
        await note($, { event:'fresh.d1.second', read:second })
      })
      $.clock.after(2500, async () => {
        let wrote = null
        try { wrote = await $.state.set(SCRATCH, ['fresh-' + GEN], { ifVersion:0 }) } catch (error) { wrote = { error:String(error).slice(0, 200) } }
        await note($, { event:'fresh.d2.write', wrote })
      })
      // Phase 1 inside this hook dispatch (run 35a040a8: the plugin's own
      // prompt.submit hook was not raised for prompts issued from a timer).
      // The three calls are back to back, with no await between them.
      const invokedAt = Date.now()
      const a = sidePromptSubmit($, 'parallel:A', M.A)
      const b = sidePromptSubmit($, 'side:B', M.B, { ref:'b'.repeat(16) })
      const a2 = sidePromptSubmit($, 'parallel:A', M.A2)
      const pendingNow = pendingView()
      await note($, { event:'invoked', invokedAt, sameAPromise:a === a2, distinctB:a !== b, pending:pendingNow })
      a.then(value => note($, { event:'a.resolve', value, resolvedAt:Date.now(), pending:pendingView() }))
      a2.then(value => note($, { event:'a2.resolve', value, resolvedAt:Date.now() }))
      b.then(value => note($, { event:'b.resolve', value, resolvedAt:Date.now(), pending:pendingView() }))
      $.clock.after(300, async () => note($, { event:'registry.after-issue', registry:await registry($), pending:pendingView() }))
      $.clock.after(5600, async () => note($, { event:'registry.after-window', registry:await registry($), pending:pendingView() }))
    } else {
      // Generation B: the same key again is never submitted while registered.
      const r2 = sidePromptSubmit($, 'parallel:R', M.R2)
      await note($, { event:'r2.invoke', pending:pendingView() })
      r2.then(async value => note($, { event:'r2.resolve', value, pending:pendingView(), registry:await registry($) }))
      $.clock.after(300, async () => note($, { event:'b.registry.after-start', registry:await registry($), pending:pendingView() }))
    }
    return result
  })
  on('prompt.submit', async ($, e, next) => {
    const name = marker(e.text)
    await note($, { event:'hook.prompt.submit', name, working, head:String(e.text || '').slice(0, 60),
      origin:e.origin ? JSON.stringify(e.origin).slice(0, 120) : null })
    const wait = name === 'A' && !held.A ? HOLD_MS : name === 'R' && !held.R ? RELOAD_HOLD_MS : 0
    if (wait) {
      held[name] = true
      await note($, { event:'hold.begin', name, ms:wait })
      try { await $.clock.sleep(wait, { signal:next.signal }) }
      catch (error) { await note($, { event:'hold.error', name, error:String(error).slice(0, 200) }) }
      await note($, { event:'hold.end', name })
      // If A's prompt never starts a turn, phase 2 still runs once main is idle.
      if (name === 'A' && GEN === 'A') $.clock.after(12000, () => { if (!working) startPhase2($, 'a-fallback') })
    }
    const result = await next(e)
    if (name) await note($, { event:'hook.prompt.result', name, drop:result?.drop ?? null })
    return result
  })
  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) {
      working = true; sidePromptTurnStart(); turnName = marker(e.text)
      await note($, { event:'turn.start', turnId:e.turnId, name:turnName })
    }
    return next(e)
  })
  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    if (e.agentId === undefined) {
      working = false; sidePromptTurnComplete($)
      await note($, { event:'turn.complete', turnId:e.turnId, reason:e.reason || '', name:turnName })
      if (GEN === 'A' && turnName === 'A') await startPhase2($, 'a-turn-complete')
    }
    return result
  })
}
let phase2 = false
let turnName = null
// Issued inside a hook dispatch so the plugin's own prompt.submit hook can hold it.
async function startPhase2($, why) {
  if (phase2) return
  phase2 = true
  const r = sidePromptSubmit($, 'parallel:R', M.R)
  await note($, { event:'r.invoke', why, pending:pendingView() })
  r.then(value => note($, { event:'r.resolve', value, resolvedAt:Date.now() }))
  $.clock.after(400, async () => note($, { event:'phase2.ready', registry:await registry($), pending:pendingView() }))
}

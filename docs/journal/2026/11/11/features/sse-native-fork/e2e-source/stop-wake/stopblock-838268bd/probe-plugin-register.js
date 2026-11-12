
let chain = Promise.resolve()
let seq = 0
function note($, row) {
  const entry = { ...row, seq: ++seq, at: Date.now() }
  chain = chain.then(async () => {
    let prior = ''
    try { prior = await $.fs.read('hook-events.jsonl') } catch {}
    await $.fs.write('hook-events.jsonl', prior + JSON.stringify(entry) + '\n')
  }).catch(() => {})
  return chain
}
let armed = false
let blocked = 0
export function register(on) {
  on('session.start', async ($, e, next) => { const r = await next(e); await note($, { event: 'session.start' }); return r })
  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) {
      if (String(e.text || '').includes('STOP-PROBE-ON')) armed = true
      await note($, { event: 'turn.start', turnId: e.turnId, text: String(e.text || '').slice(0, 60) })
    }
    return next(e)
  })
  on('turn.step', async function* ($, e, next) {
    if (e.agentId === undefined)
      await note($, { event: 'turn.step', turnId: e.turnId, index: e.index, messageCount: e.messageCount })
    return yield* next(e)
  })
  on('turn.complete', async ($, e, next) => {
    if (e.agentId === undefined)
      await note($, { event: 'turn.complete', turnId: e.turnId, reason: e.reason || '', answer: String(e.answer || '').slice(0, 40) })
    return next(e)
  })
  on('classic.Stop', async ($, e, next) => {
    const result = await next(e)
    if (e.agent_id) return result
    if (armed && blocked === 0 && !result?.block) {
      blocked++
      await note($, { event: 'classic.Stop', decision: 'block', stop_hook_active: e.stop_hook_active ?? null })
      return { ...result, block: 'STOP-BLOCK-PROBE-REASON: answer with the word STOPPED-OK.' }
    }
    await note($, { event: 'classic.Stop', decision: 'pass', stop_hook_active: e.stop_hook_active ?? null })
    return result
  })
}

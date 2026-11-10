
const SIGNAL = /^\[ciel-side-receipt-ready\] [0-9a-f]{64}$/
let chain = Promise.resolve()
function note($, row) {
  chain = chain.then(async () => {
    let prior = ''
    try { prior = await $.fs.read('hook-events.jsonl') } catch {}
    await $.fs.write('hook-events.jsonl', prior + JSON.stringify(row) + '\n')
  }).catch(() => {})
  return chain
}
export function register(on) {
  on('session.receive', async ($, e, next) => {
    const text = String(e.text || '').trim()
    if (SIGNAL.test(text)) {
      const at = Date.now()
      await note($, { hook: 'session.receive', origin: e.origin?.kind || '', agentId: e.agentId || null, text, at })
      return { consumed: 'probe consumed' }
    }
    return next(e)
  })
  on('prompt.submit', async ($, e, next) => {
    const text = String(e.text || '').trim()
    if (SIGNAL.test(text)) {
      const at = Date.now()
      await note($, { hook: 'prompt.submit', origin: e.origin?.kind || '', text, at })
      return { drop: 'probe dropped' }
    }
    return next(e)
  })
}

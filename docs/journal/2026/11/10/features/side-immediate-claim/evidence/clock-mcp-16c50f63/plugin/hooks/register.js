
let chain = Promise.resolve()
function note($, row) {
  chain = chain.then(async () => {
    let prior = ''
    try { prior = await $.fs.read('hook-events.jsonl') } catch {}
    await $.fs.write('hook-events.jsonl', prior + JSON.stringify(row) + '\n')
  }).catch(() => {})
  return chain
}
let started = false
export function register(on) {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    if (started) return result
    started = true
    try { await $.mcp.connect('probe'); await note($, { event: 'connected', at: Date.now() }) }
    catch (error) { await note($, { event: 'connect-failed', at: Date.now(), error: String(error).slice(0, 300) }) }
    const tick = () => $.clock.after(2000, async () => {
      const fired = Date.now()
      let row = { event: 'tick', fired }
      try {
        const answer = await $.mcp.call('probe', 'now', {})
        const text = (answer?.content || []).map(c => c.text || '').join('')
        row = { ...row, returned: Date.now(), server_at: JSON.parse(text).server_at }
      } catch (error) { row = { ...row, returned: Date.now(), error: String(error).slice(0, 200) } }
      await note($, row)
      tick()
    })
    tick()
    return result
  })
}

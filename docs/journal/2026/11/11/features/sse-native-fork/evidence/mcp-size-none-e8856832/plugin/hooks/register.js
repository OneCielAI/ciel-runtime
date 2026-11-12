
let chain = Promise.resolve()
function note($, row) {
  chain = chain.then(async () => {
    let prior = ''
    try { prior = await $.fs.read('hook-events.jsonl') } catch {}
    await $.fs.write('hook-events.jsonl', prior + JSON.stringify(row) + '\n')
  }).catch(() => {})
  return chain
}
async function sha(text) {
  const hash = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)))
  return [...hash].map(v => v.toString(16).padStart(2, '0')).join('')
}

let started = false
export function register(on) {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    if (started) return result
    started = true
    try { await $.mcp.connect('walkie'); await note($, { event: 'connected' }) }
    catch (error) { await note($, { event: 'connect-failed', error: String(error).slice(0, 300) }); return result }
    for (const tool of ['walkie_read_messages', 'walkie_call_tool']) {
      for (const size of [60000, 200000, 600000]) {
        let row = { event: 'call', tool, size }
        try {
          const answer = await $.mcp.call('walkie', tool, { size })
          const content = Array.isArray(answer?.content) ? answer.content : []
          const text = content.filter(c => c?.type === 'text').map(c => c.text || '').join('')
          let parsed = false
          try { JSON.parse(text); parsed = true } catch {}
          row = { ...row, keys: Object.keys(answer || {}).sort(), isError: answer?.isError ?? null,
            content_types: content.map(c => c?.type), chars: text.length, sha256: await sha(text), parsed,
            head: text.slice(0, 400), tail: text.slice(-200),
            other_content: content.filter(c => c?.type !== 'text').map(c => JSON.stringify(c).slice(0, 400)),
            meta: answer?._meta ? JSON.stringify(answer._meta).slice(0, 400) : null }
        } catch (error) { row = { ...row, threw: String(error).slice(0, 600) } }
        await note($, row)
      }
    }
    await note($, { event: 'done' })
    return result
  })
}

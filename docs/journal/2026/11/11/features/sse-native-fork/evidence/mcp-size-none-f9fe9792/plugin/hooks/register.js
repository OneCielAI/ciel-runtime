
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
    try {
      await $.mcp.connect('walkie')
      let configDir = null
      try { configDir = await $.env.get('CLAUDE_CONFIG_DIR') } catch (error) { configDir = 'threw: ' + String(error).slice(0, 200) }
      await note($, { event: 'connected', session_id: await $.session.id(), claude_config_dir: configDir })
    }
    catch (error) { await note($, { event: 'connect-failed', error: String(error).slice(0, 300) }); return result }
    // Run 1 showed the first call can precede the server's tool listing.
    for (let attempt = 0; attempt < 40; attempt++) {
      try { await $.mcp.call('walkie', 'walkie_read_messages', { size: 100 }); break }
      catch { await new Promise(resolve => setTimeout(resolve, 250)) }
    }
    for (const tool of ['walkie_read_messages', 'walkie_call_tool']) {
      for (const size of [60000, 200000, 600000]) {
        let row = { event: 'call', tool, size }
        try {
          const answer = await $.mcp.call('walkie', tool, { size })
          const content = Array.isArray(answer?.content) ? answer.content : []
          const text = content.filter(c => c?.type === 'text').map(c => c.text || '').join('')
          let parsed = false
          try { JSON.parse(text); parsed = true } catch {}
          // Can the plugin read the host-persisted full result named in a stub?
          const saved = /Output has been saved to (.+?\.txt)/.exec(text)?.[1] || null
          let savedRead = null
          if (saved) {
            try { const full = await $.fs.read(saved); savedRead = { chars: full.length, sha256: await sha(full) } }
            catch (error) { savedRead = { threw: String(error).slice(0, 300) } }
          }
          row = { ...row, saved_path: saved, saved_read: savedRead }
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

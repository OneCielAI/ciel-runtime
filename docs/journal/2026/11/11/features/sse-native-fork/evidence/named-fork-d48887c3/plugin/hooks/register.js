
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

const NAMED = 'side-intake-0123456789abcdef'
const pending = new Set()
const row = a => ({ id: a.id, type: a.type, name: a.name ?? null, status: a.status, spawnedBy: a.spawnedBy ?? null,
  description: a.description ?? null, keys: Object.keys(a).sort() })
export function register(on) {
  on('agent.spawn', async ($, e, next) => {
    await note($, { event: 'agent.spawn', name: e.name ?? null, description: e.description ?? null,
      subagentType: e.subagentType ?? null, fork: e.fork ?? null, parentAgentId: e.parentAgentId ?? null,
      origin: next.origin?.plugin ?? null, keys: Object.keys(e).sort() })
    return next(e)
  })
  on('session.receive', async ($, e, next) => {
    if (String(e.text || '').trim() !== '[fork-probe] go' || e.agentId !== undefined) return next(e)
    void (async () => {
      for (const [label, spec] of [['named', { name: NAMED, description: 'Side SSE 0123456789ab' }],
                                   ['unnamed', { description: 'Side SSE abcdefabcdef' }]]) {
        try {
          const spawned = await $.agent.spawn({ subagentType: 'fork', ...spec,
            prompt: 'FORK-PROBE-' + label.toUpperCase() + ' reply with one word' })
          pending.add(spawned?.agentId)
          await note($, { event: 'spawned', label, agentId: spawned?.agentId ?? null, deny: spawned?.deny ?? null,
            keys: Object.keys(spawned || {}).sort() })
        } catch (error) { await note($, { event: 'spawn-threw', label, error: String(error).slice(0, 400) }) }
      }
    })()
    return { consumed: 'fork probe' }
  })
  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    if (!e.agentId || !pending.has(e.agentId)) return result
    pending.delete(e.agentId)
    void (async () => {
      const before = (await $.agent.list()).filter(a => a.id === e.agentId).map(row)
      await note($, { event: 'complete', agentId: e.agentId, reason: e.reason ?? null,
        answer: String(e.answer || '').slice(0, 200), before })
      let stop = null
      try { stop = await $.tool.call({ tool: 'TaskStop', task_id: e.agentId }) }
      catch (error) { stop = { threw: String(error).slice(0, 300) } }
      await new Promise(resolve => setTimeout(resolve, 3000))
      const after = (await $.agent.list()).filter(a => a.id === e.agentId).map(row)
      const all = (await $.agent.list()).map(row)
      await note($, { event: 'stopped', agentId: e.agentId, stop: JSON.stringify(stop).slice(0, 400), after, all })
    })()
    return result
  })
}

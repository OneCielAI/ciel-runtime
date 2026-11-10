
let chain = Promise.resolve()
function note($, row) {
  chain = chain.then(async () => {
    let prior = ''
    try { prior = await $.fs.read('hook-events.jsonl') } catch {}
    await $.fs.write('hook-events.jsonl', prior + JSON.stringify(row) + '\n')
  }).catch(() => {})
  return chain
}
const SPAWN = /^\[spawn-probe\] (idle|busy)$/
export function register(on) {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await $.agent.register({ name: 'probe-actor', description: 'Spawn latency probe actor',
      prompt: 'Answer with one word.', model: 'inherit', effort: 'low', tools: [],
      background: true, maxTurns: 1, omitClaudeMd: true })
    await note($, { event: 'registered', at: Date.now() })
    return result
  })
  on('session.receive', async ($, e, next) => {
    const match = SPAWN.exec(String(e.text || '').trim())
    if (!match || e.agentId !== undefined) return next(e)
    const phase = match[1]
    const call = Date.now()
    void (async () => {
      try {
        const spawned = await $.agent.spawn({ subagentType: 'spawn-probe:probe-actor',
          description: 'Probe actor ' + phase, prompt: 'SPAWN-PROBE-' + phase.toUpperCase() })
        await note($, { event: 'spawn-resolved', phase, call, resolved: Date.now(),
          agentId: spawned?.agentId || null, deny: spawned?.deny || null })
      } catch (error) {
        await note($, { event: 'spawn-threw', phase, call, at: Date.now(), error: String(error).slice(0, 300) })
      }
    })()
    return { consumed: 'spawn probe' }
  })
  on('turn.complete', async ($, e, next) => {
    if (e.agentId) await note($, { event: 'turn.complete', agentId: e.agentId, reason: e.reason || '', at: Date.now() })
    return next(e)
  })
}

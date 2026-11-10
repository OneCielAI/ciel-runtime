
let chain = Promise.resolve()
function note($, row) {
  chain = chain.then(async () => {
    let prior = ''
    try { prior = await $.fs.read('hook-events.jsonl') } catch {}
    await $.fs.write('hook-events.jsonl', prior + JSON.stringify({ ...row, at: Date.now() }) + '\n')
  }).catch(() => {})
  return chain
}
let fork = null
export function register(on) {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await $.agent.register({ name: 'probe-worker', description: 'Delivery probe worker',
      prompt: 'Run the command you are given, then answer with one word.', model: 'inherit', effort: 'low',
      tools: ['Bash'], background: true, maxTurns: 4, omitClaudeMd: true })
    await note($, { event: 'registered' })
    return result
  })
  on('agent.spawn', async ($, e, next) => {
    await note($, { event: 'agent.spawn', subagentType: e.subagentType || '', description: e.description || '' })
    return next(e)
  })
  on('session.receive', async ($, e, next) => {
    const text = String(e.text || '').trim()
    if (e.agentId !== undefined || !text.startsWith('[delivery-probe] ')) return next(e)
    const command = text.slice('[delivery-probe] '.length)
    void (async () => {
      try {
        if (command === 'append-busy' || command === 'append-idle') {
          const result = await $.session.append({ message: { type: 'user', content: [{ type: 'text', text: 'APPEND-NOTE-' + command }] } })
          await note($, { event: command, result: result ?? null })
        } else if (command === 'spawn-fork') {
          const spawned = await $.agent.spawn({ subagentType: 'delivery-probe:probe-worker', description: 'Probe fork', prompt: 'FORK-START run the busy command' })
          fork = spawned?.agentId || null
          await note($, { event: 'spawn-fork', agentId: fork, deny: spawned?.deny || null })
        } else if (command === 'send-fork') {
          const result = await $.session.send({ to: { agentId: fork }, text: 'SEND-NOTE-to-fork' })
          await note($, { event: 'send-fork', agentId: fork, result: result ?? null })
        }
      } catch (error) { await note($, { event: command + '-threw', error: String(error).slice(0, 300) }) }
    })()
    return { consumed: 'delivery probe' }
  })
  on('turn.complete', async ($, e, next) => {
    await note($, { event: 'turn.complete', agentId: e.agentId || null, reason: e.reason || '' })
    return next(e)
  })
}

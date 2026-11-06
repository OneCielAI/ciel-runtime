export function register(on) {
  on('turn.step', async function* ($, e, next) {
    const enabled = await $.env.get('CIEL_RUNTIME_DIRECT_GATEWAY_ACTIVE')
    const configured = Number(await $.env.get('CIEL_RUNTIME_DIRECT_EMPTY_STREAM_RETRIES'))
    const limit = Number.isInteger(configured) ? Math.max(0, Math.min(10, configured)) : 0
    if (enabled !== '1' || !limit) return yield* next(e)
    for (let attempt = 0; ; attempt++) {
      const before = await $.session.messages(e.agentId ? { agentId: e.agentId } : {})
      if (!Array.isArray(before)) return yield* next(e)
      const stream = next(e)
      let modelOutput = false
      let result
      while (true) {
        const item = await stream.next()
        if (item.done) { result = item.value; break }
        // Engine envelopes are opaque. Every typed model chunk closes replay,
        // including thinking, tool input, and a stop with no visible text.
        if (item.value.kind !== 'engine') modelOutput = true
        yield item.value
      }
      if (modelOutput || result.stopReason !== null || result.answer || result.toolUses.length
          || result.usage !== null || attempt >= limit || next.signal.aborted) return result
      const messages = await $.session.messages(e.agentId ? { agentId: e.agentId } : {})
      if (!Array.isArray(messages) || messages.length <= before.length
          || messages.slice(before.length).some(message => message.role === 'user')) return result
      const last = messages[messages.length - 1]
      if (last?.role !== 'assistant' || last.text !== 'API Error: Stream ended without receiving any events'
          || last.toolUses.length) return result
      $.ui.log('Ciel network recovery: gateway stream ended without model events; retry '
        + String(attempt + 1) + '/' + String(limit) + '. No user message is injected.')
    }
  })
}

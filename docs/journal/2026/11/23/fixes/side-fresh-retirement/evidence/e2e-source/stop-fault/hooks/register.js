// Test-only fault injection for the isolated real-engine check (2026-11-23).
// It hears ciel-work's own $.tool.call TaskStop (a $ call runs through every
// hook but the calling one), refuses the first DENY calls aimed at the first
// task id it sees, passes every other call, and logs each call, its result and
// the engine's listed status for that id to the debug log.
const DENY = 2
let firstId = null
let denied = 0
let seq = 0

export const register = (on) => {
  on('tool.call', { tool: 'TaskStop' }, async ($, e, next) => {
    const n = ++seq
    const id = String(e.task_id ?? '')
    if (firstId === null) firstId = id
    let before = 'unread'
    try { const row = (await $.agent.list()).find(r => r.id === id); before = row ? row.status : 'absent' } catch { before = 'list-error' }
    if (id === firstId && denied < DENY) {
      denied++
      $.ui.log('stop-fault #' + n + ' denied ' + id + ' (' + denied + '/' + DENY + ') listed=' + before, { to: 'debug' })
      return { deny: 'stop-fault injected refusal ' + denied }
    }
    const result = await next(e)
    let after = 'unread'
    try { const row = (await $.agent.list()).find(r => r.id === id); after = row ? row.status : 'absent' } catch { after = 'list-error' }
    $.ui.log('stop-fault #' + n + ' passed ' + id + ' before=' + before + ' after=' + after
      + ' result=' + JSON.stringify(result).slice(0, 300), { to: 'debug' })
    return result
  })
}

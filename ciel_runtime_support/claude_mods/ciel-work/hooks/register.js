import { atom, read, update } from 'claude-code'

// Router is authoritative; this cache is display-only and never persisted.
const detailView = atom({ plugin: 'ciel-work', key: 'detail' }, null)
const PANE = 'work-details'
const PREFIX = 'mcp__ciel-runtime-router__'
const WORK = new Set(['work_status', 'work_plan_set', 'work_node_add', 'work_node_update',
  'work_node_remove', 'work_select', 'work_execute', 'work_replan', 'work_reorder', 'work_urgent'])
const WRITES = new Set(['Edit', 'Write', 'NotebookEdit', 'apply_patch'])
const SHELLS = new Set(['Bash', 'PowerShell'])
const UNAVAILABLE = 'Work status unavailable: router connection or snapshot could not be verified.'
const PLANNING = 'Work mode is planning: edits and state-changing commands wait until the plan is approved. '
  + 'Build it with work_plan_set, then call work_execute. (/work-mode help)'
const NOTICE = 'Ciel Work mode is on: the model plans first (work_plan_set) and edits after work_execute. '
  + 'Items show above the prompt · /work-mode help · turn off: ciel-runtime menu → LLM options → Work mode'
const HELP = [
  'Ciel Work mode — plan first, then execute',
  '• Planning: the model builds a plan with work_plan_set: goal → phases → tasks → verifiable steps. Reading is allowed; edits and state-changing commands wait.',
  '• New requests: an independent request becomes its own numbered item ("2. …") and waits in the queue behind the active item; a change to an existing item is added under that item.',
  '• Executing: after work_execute (automatic, or your approval when set to ask) the model works step by step and marks each step done with evidence.',
  '• Above the prompt: Ctrl+X Tab focuses the items · Tab/↑↓ select · Enter inspects · Esc returns.',
  '• /work-mode view: keyboard inspector · /work-mode tree: plan tree · /work-mode status: raw snapshot (JSON) · /work-mode help: this help',
  '• Priorities: All items → Move up / Move down orders root goals. Moving the active goal requires your request; a button press is that request.',
  '• Linked IDs appear as [ id ] description; ◷ names an external wait; green ✓ means verified done. An urgent request holds other goals until it finishes.',
  '• Auto continue: normal turn endings resume actionable Work, bounded by no-progress checks; waits, blocks, approval and background tasks do not trigger it, and a dim line says why it stopped. Codex receives instructions only.',
  '• Settings: ciel-runtime menu → LLM options → Work mode (on/off), Work plan approval (auto/ask), Work auto continue (on/off).',
].join('\n')
let cached = null
let enabled = true
let generation = 0
let cachedSession = null
let hinted = ''
let starts = 0
let noticed = 0
let continuationVersion = null
let unchangedContinuations = 0
let stopNoticeKey = ''
const RETRY_MS = [250, 500, 1000, 2000, 4000, 8000]
const IDLE_REFRESH_MS = [1000, 2000, 4000, 8000, 16000, 30000]
const nativePlanPaths = new Map()
let idleRefresh = null
let idleReadPending = false
let interactive = false
let working = false
function planKey(identity, agentId) {
  return identity + ':' + (typeof agentId === 'string' ? agentId : '')
}
function clearNativePlanPaths(identity = null) {
  if (identity === null) nativePlanPaths.clear()
  else for (const key of nativePlanPaths.keys()) if (key.startsWith(identity + ':')) nativePlanPaths.delete(key)
}
function stopIdleRefresh() {
  if (idleRefresh) idleRefresh.active = false
  if (idleRefresh?.timer) {
    try { idleRefresh.timer.cancel() } catch {}
  }
  idleRefresh = null
}
function scheduleIdleRefresh($, poll, attempt = poll.attempt) {
  if (idleRefresh !== poll || !poll.active || poll.running || poll.timer || idleReadPending) return
  const delay = IDLE_REFRESH_MS[Math.min(attempt, IDLE_REFRESH_MS.length - 1)]
  try {
    poll.timer = $.clock.after(delay, async () => {
      poll.timer = null
      if (idleRefresh !== poll || !poll.active || poll.running) return
      poll.running = true; idleReadPending = true
      const before = cached?.version
      try {
        if (await sessionId($) !== poll.identity) { stopIdleRefresh(); return }
        const s = await refresh($)
        if (idleRefresh !== poll || !poll.active) return
        if (s?.enabled === false) { stopIdleRefresh(); return }
        if (cachedSession !== poll.identity || await sessionId($) !== poll.identity) { stopIdleRefresh(); return }
        poll.attempt = s && s.version !== before ? 0 : Math.min(poll.attempt + 1, IDLE_REFRESH_MS.length - 1)
      } catch {
        poll.attempt = Math.min(poll.attempt + 1, IDLE_REFRESH_MS.length - 1)
      } finally {
        poll.running = false; idleReadPending = false
        if (idleRefresh?.active) scheduleIdleRefresh($, idleRefresh, idleRefresh.attempt)
      }
    })
  } catch {}
}
function startIdleRefresh($, identity) {
  if (!interactive || working || !identity || !enabled || (idleRefresh?.identity === identity && idleRefresh.active)) return
  stopIdleRefresh()
  const poll = { identity, active: true, running: false, timer: null, attempt: 0 }
  idleRefresh = poll
  scheduleIdleRefresh($, poll)
}
const INSTRUCTIONS = 'Ciel Work mode is on. Before edits or state-changing commands, use '
  + 'work_plan_set: goal -> phases -> tasks -> concrete verifiable steps. Triage every new user '
  + 'request first: an independent request becomes its own goal (root-level item), its title starting '
  + 'with its item number where possible ("1. ...", "2. ..."); a request that changes or extends an '
  + 'existing item goes under that item as a child (work_node_add with parent_id). A new goal queues '
  + 'behind the unfinished active goal and becomes active when it is done; keep the active goal going. '
  + 'Read-only exploration '
  + 'is allowed while planning. Call work_execute when ready; auto approval is default and '
  + 'configured ask approval uses the Work dialog. Never supply user_confirmed yourself. '
  + 'During execution mark the current leaf in_progress with work_node_update, then done only '
  + 'with verification evidence. Add/split nodes using work_node_add; use work_replan when the '
  + 'approach changes. Use work_status for the tree and expected_version where supported. '
  + 'These rules include subagents: no delegated state changes during planning or unavailable status. '
  + 'Set ref for a linked id, and waiting_on for what an item waits on (empty text clears). '
  + 'Use work_reorder to change root order: queue-only by default; changing the active slot needs an explicit '
  + 'user request and user_requested=true. On an explicit urgent/ad-hoc request use work_urgent with '
  + 'user_requested=true to hold other goals, break down the request and execute; held goals resume afterwards.'
function textOf(r) {
  if (!r || r.isError) throw new Error('Router error')
  const text = (r.content || []).filter(b => b.type === 'text').map(b => b.text).join('\n')
  if (!text) throw new Error('Missing router text')
  return text
}
async function sessionId($) {
  const id = await $.session.id()
  if (typeof id !== 'string' || !/^[A-Za-z0-9_-]{1,200}$/.test(id)) throw new Error('Missing session identity')
  return 'claude:' + id
}
async function refresh($) {
  const ticket = ++generation
  try {
    const identity = await sessionId($)
    if (ticket !== generation) return null
    if (cachedSession !== identity) {
      if (cachedSession) clearNativePlanPaths(cachedSession)
      cached = null; cachedSession = identity; continuationVersion = null; unchangedContinuations = 0
      await update($, detailView, () => null)
      if (ticket !== generation) return null
    }
    const s = JSON.parse(textOf(await $.mcp.call('ciel-runtime-router', 'work_status', { format: 'json', session_id: identity })))
    const currentIdentity = await sessionId($)
    if (ticket !== generation || currentIdentity !== identity) return null
    if (s?.enabled !== false && (s?.enabled !== true
      || !['planning', 'executing'].includes(s.phase) || !['auto', 'ask'].includes(s.approval)
      || !Number.isInteger(s.version) || s.version < 0 || !Number.isInteger(s.done)
      || !Number.isInteger(s.total) || s.done < 0 || s.total < s.done
      || !(s.active_root === null || (typeof s.active_root?.id === 'string'
        && typeof s.active_root.title === 'string')))) throw new Error('Invalid snapshot')
    if (ticket === generation) { cached = s; enabled = s.enabled; $.ui.invalidate('ui.render') }
    return s
  } catch {
    if (ticket === generation) { cached = null; $.ui.invalidate('ui.render') }
    return null
  }
}
// Only the host-pinned plan attachment identifies the file; never infer it from
// tool arguments, a tool result, reminder prose or a path under a plans folder.
function planPathKey(path) {
  if (typeof path !== 'string') return null
  // Native Windows facts use backslashes; model tool paths may use slashes.
  // Do not broaden this to a directory exception or resolve relative paths.
  if (/^[A-Za-z]:/.test(path) && (path[2] === '/' || path.charCodeAt(2) === 92)) {
    const normalized = path.replaceAll(String.fromCharCode(92), '/')
    if (normalized.split('/').some(part => part === '.' || part === '..')) return null
    return normalized.toLowerCase()
  }
  return path.startsWith('/') ? path : null
}
async function nativePlanWrite($, tool, input, agentId) {
  if (tool !== 'Edit' && tool !== 'Write') return false
  const plan = planPathKey(nativePlanPaths.get(planKey(await sessionId($), agentId)))
  return plan !== null && planPathKey(input?.file_path) === plan
}
function blocked(s) {
  if (s?.enabled === false || (!s && !enabled)) return ''
  if (!s) return UNAVAILABLE
  return s.phase === 'planning' ? PLANNING : ''
}
// One toast per conversation and plan version: the deny text itself goes to the model.
function hintPlanning($, s, reason) {
  if (reason !== PLANNING || !cachedSession) return
  const key = cachedSession + ':' + s.version
  if (hinted === key) return
  hinted = key
  try { $.ui.toast('Work mode is planning: edits wait for the approved plan · /work-mode help', { timeoutMs: 6000 }) } catch {}
}
function summary(s) {
  const lines = ['Ciel Work mode: ' + s.phase + ' · ' + s.done + '/' + s.total + ' steps · approval ' + s.approval]
  if (!s.active_root) lines.push('No plan yet — the model plans before editing.')
  else lines.push('Goal: ' + itemTitle(s.active_root, 240))
  for (const b of s.breakdown || []) lines.push('  ' + (b.status === 'done' ? '✓' : mark(b.status)) + ' ' + itemTitle(b, 240) + ' ' + b.done + '/' + b.total)
  if (s.next?.title) lines.push('Next: ' + clipped(s.next.title, 200))
  if (s.queue?.length) {
    lines.push('Queue (next first):')
    for (const q of s.queue) lines.push('  ' + (q.status === 'done' ? '✓' : mark(q.status)) + ' ' + itemTitle(q, 240) + ' ' + q.done + '/' + q.total)
  }
  lines.push('/work-mode help · view · tree · status')
  return lines.join('\n')
}
async function planText($) {
  return textOf(await $.mcp.call('ciel-runtime-router', 'work_status', { format: 'text', session_id: await sessionId($) }))
}
function clipped(value, limit) {
  const text = String(value || '').replace(/[\x00-\x1f\x7f-\x9f]/g, ' ').replace(/\s+/g, ' ').trim()
  return text.length <= limit ? text : text.slice(0, limit - 1) + '…'
}
function itemTitle(node, width) {
  return clipped((node.ref ? '[ ' + node.ref + ' ] ' : '') + node.title
    + (node.urgent ? ' [urgent]' : '') + (node.waiting_on ? ' ◷ ' + node.waiting_on : ''), width)
}
function statusButton(Box, Button, Text, node, props) {
  const button = Button(props)
  return node.status === 'done' ? Box({ flexDirection: 'row', children: [
    Text({ color: 'success', children: '✓ ' }), button] }) : button
}
async function moveRoot($, id, offset, origin) {
  if (await sessionId($) !== origin) return
  const s = await refresh($)
  if (!s?.enabled || await sessionId($) !== origin) return
  const roots = [s.active_root, ...(s.queue || [])].filter(Boolean)
  const index = roots.findIndex(n => n.id === id)
  if (index < 0 || index + offset < 0 || index + offset >= roots.length) return
  try {
    textOf(await $.mcp.call('ciel-runtime-router', 'work_reorder', {
      root_id: id, position: index + offset + 1, user_requested: true,
      expected_version: s.version, session_id: origin }))
    await openDetails($, null, origin)
  } catch { try { $.ui.toast('Work order changed or is unavailable; refresh and try again.') } catch {} }
}
function breakdownLabel(s) {
  return 'work: ' + (s.phase === 'planning' ? 'plan' : 'run') + ' ' + s.done + '/' + s.total
}
// Goals waiting behind the active one, oldest first; plain rows, the active goal keeps the focus ring.
function queued(s, width, Text) {
  const queue = s.queue || []
  const rows = queue.slice(0, 3).map(q => Text({ key: 'queue-' + q.id, dimColor: true,
    children: (q.held ? 'Held: ' : 'Queued: ') + mark(q.status) + ' ' + itemTitle(q, width) + ' ' + q.done + '/' + q.total }))
  if (queue.length > 3) rows.push(Text({ key: 'queue-more', dimColor: true, children: '+' + (queue.length - 3) + ' more queued · /work-mode' }))
  return rows
}
function mark(status) {
  return ({ pending: '[ ]', in_progress: '[~]', done: '', blocked: '[!]' })[status] ?? '[?]'
}
async function openDetails($, id = null, origin = null) {
  const identity = await sessionId($)
  if (origin && identity !== origin) return
  const s = await refresh($)
  if (!s?.enabled || await sessionId($) !== identity) return
  try {
    const detail = JSON.parse(textOf(await $.mcp.call('ciel-runtime-router', 'work_status', {
      format: 'json', detail: true, session_id: identity })))
    if (await sessionId($) !== identity || cachedSession !== identity) return
    if (!Array.isArray(detail.nodes) || detail.version !== s.version || cached?.version !== s.version)
      throw new Error('Plan changed; refresh to inspect the latest version.')
    const selected = id || detail.active_root?.id
    if (selected && !detail.nodes.some(n => n.id === selected)) throw new Error('This item was removed.')
    await update($, detailView, () => ({ session: identity, id: selected || null, version: detail.version,
      nodes: detail.nodes, error: '' }))
  } catch (error) {
    if (await sessionId($) !== identity || cachedSession !== identity) return
    await update($, detailView, () => ({ session: identity, id, version: s.version, nodes: [],
      error: clipped(error.message || 'Work details unavailable.', 200) }))
  }
  if (await sessionId($) === identity) await $.ui.open({ id: PANE, title: 'Work details',
    focus: true, closeOnEscape: true, rows: 18, columns: 64 })
}
// A transcript notice for the person, once per session start; the model never reads it.
function notice($, ticket) {
  if (noticed === ticket) return
  noticed = ticket
  try { $.ui.log(NOTICE) } catch {}
}
const STOP_UNAVAILABLE = 'Ciel Work auto continue could not read the Work status, so unfinished items were not checked. '
  + '/work-mode status'
const STOP_APPROVAL = 'Ciel Work stopped: the plan is ready and waits for your approval before execution. '
  + 'Ask the model to execute it (you approve in the Work dialog) or request changes. /work-mode tree'
// Unfinished Work the person sees above the prompt: the active goal or a queued goal.
function openWork(s) {
  return Boolean((s.active_root && s.active_root.status !== 'done') || s.queue?.length)
}
// Why a normal stop did not continue, as one person-facing line the model never reads. The same line is
// not repeated for the same conversation until the person writes; automated (meta) prompts do not reset it.
function stopNotice($, identity, text) {
  const key = identity + '|' + text
  if (stopNoticeKey === key) return
  stopNoticeKey = key
  try { $.ui.log(text) } catch {}
}
function notRunnable(s, c) {
  const parts = []
  const waits = (c.waiting || []).map(n => clipped(n.id, 64) + ' ◷ ' + clipped(n.waiting_on, 120))
  if (waits.length) parts.push('waiting: ' + waits.join('; '))
  if (c.blocked > 0) parts.push(c.blocked + ' blocked step(s)')
  if (c.held > 0) parts.push(c.held + ' goal(s) held behind the urgent goal')
  const finished = [s.active_root && s.active_root.status !== 'done' && s.total > 0 && s.done === s.total ? s.active_root : null,
    ...(s.queue || []).filter(q => q.total > 0 && q.done === q.total)].filter(Boolean).map(n => clipped(n.id, 64))
  if (finished.length) parts.push('every step done but goal still open: ' + finished.join(', '))
  return 'Ciel Work stopped: unfinished items remain but none can run now'
    + (parts.length ? ' (' + parts.join(' · ') + ')' : '')
    + '. Clear a wait, unblock or close an item, or send a message to continue. /work-mode tree lists every item.'
}
// On a mod's first load the engine can still be connecting the router when the session starts
// ("no connected MCP tool"): read again shortly instead of staying unavailable until the next prompt.
function retryStart($, ticket, attempt = 0) {
  if (attempt >= RETRY_MS.length) return
  try {
    $.clock.after(RETRY_MS[attempt], async () => {
      if (ticket !== starts) return
      const s = cached || await refresh($)
      if (ticket !== starts) return
      if (!s) retryStart($, ticket, attempt + 1)
      else if (s.enabled) { notice($, ticket); startIdleRefresh($, cachedSession) }
    })
  } catch {}
}
export function register(on) {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'work-mode', description: 'Work mode: summary, help, keyboard inspector, tree or raw status',
      argumentHint: '[help|view|tree|status]', immediate: true })
    const ticket = ++starts
    stopIdleRefresh(); clearNativePlanPaths(); interactive = e.isInteractive
    const s = await refresh($)
    if (e.isInteractive && s?.enabled) { notice($, ticket); startIdleRefresh($, cachedSession) }
    else if (e.isInteractive && !s) retryStart($, ticket)
    return next(e)
  })
  on('prompt.attachment', { type: ['plan_mode', 'plan_mode_exit'] }, async ($, e, next) => {
    if (next.origin.plugin === 'engine' && e.origin.kind === 'engine') {
      const identity = await sessionId($)
      const key = planKey(identity, e.agentId)
      const path = e.detail?.planFilePath
      if (e.type === 'plan_mode' && typeof path === 'string' && path.length > 0) nativePlanPaths.set(key, path)
      else nativePlanPaths.delete(key)
    }
    return next(e)
  })
  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) { working = true; stopIdleRefresh(); generation++ }
    return next(e)
  })
  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    if (e.agentId === undefined) { working = false; if (interactive) startIdleRefresh($, await sessionId($)) }
    return result
  })
  on('classic.Stop', async ($, e, next) => {
    const result = await next(e)
    if (result?.block || result?.preventContinuation || e.agent_id) return result
    const identity = await sessionId($)
    if (e.session_id && identity !== 'claude:' + e.session_id) return result
    const s = await refresh($)
    if (await sessionId($) !== identity) return result
    // A read superseded by a newer one also returns null; only a failed latest read is reported.
    if (!s) { if (enabled && !cached) stopNotice($, identity, STOP_UNAVAILABLE); return result }
    if (!s.enabled || s.auto_continue !== true) return result
    // Every stop that leaves unfinished Work either continues with a runnable item or says why not.
    if (e.background_tasks?.length) {
      if (openWork(s)) stopNotice($, identity, 'Ciel Work auto continue waits: ' + e.background_tasks.length
        + ' background task(s) still running will wake this session, and the next stop checks unfinished items again.')
      return result
    }
    if (s.phase === 'planning' && s.approval === 'ask' && s.ready) {
      if (openWork(s)) stopNotice($, identity, STOP_APPROVAL)
      return result
    }
    if (!(s.continuation?.actionable > 0)) {
      if (openWork(s)) stopNotice($, identity, notRunnable(s, s.continuation || {}))
      return result
    }
    if (continuationVersion !== s.version) { continuationVersion = s.version; unchangedContinuations = 0 }
    else unchangedContinuations++
    if (unchangedContinuations > 1) {
      try { $.ui.toast('Work auto continue paused: no plan progress. Update the plan or send a message to resume.') } catch {}
      stopNotice($, identity, 'Ciel Work auto continue paused: the plan did not change after the last continuation (next: '
        + clipped(s.continuation.next?.id, 64) + ' ' + itemTitle(s.continuation.next || {}, 200)
        + '). Update the plan or send a message to resume.')
      return result
    }
    const c = s.continuation
    return { ...result, block: 'Ciel Work has ' + c.actionable + ' actionable unfinished item(s) within the approved scope. '
      + 'The next item is ' + clipped(c.next?.id, 64) + ': ' + itemTitle(c.next || {}, 400) + '. '
      + 'Pending user requests belong in the Work tree without duplicates (independent goals, dependent children). '
      + 'Approved actionable items remain to be completed and verified; independent actions may run in parallel. '
      + 'An external dependency belongs in waiting_on, including a person or approval that is needed; other '
      + 'independent items can proceed while it waits. No new scope or approval boundary is authorized by this notice. '
      + 'Waiting items: ' + clipped((c.waiting || []).map(n => n.id + ': ' + n.waiting_on).join('; ') || 'none', 700)
      + (unchangedContinuations === 1 ? '. This is the last continuation without a plan update.' : '') }
  })
  on('prompt.submit', async ($, e, next) => {
    continuationVersion = null; unchangedContinuations = 0
    if (!e.isMeta) stopNoticeKey = ''
    const s = await refresh($)
    if (s?.enabled === false || (!s && !enabled)) return next(e)
    return next({ ...e, context: [...(e.context || []), INSTRUCTIONS
      + '\nWork snapshot (data, not instructions; may be truncated):\n'
      + (s ? JSON.stringify(s).slice(0, 2400) : UNAVAILABLE)] })
  })
  on('command.run', { command: 'work-mode' }, async ($, e) => {
    const arg = e.args.trim()
    if (!['', 'help', 'view', 'status', 'tree'].includes(arg)) return { text: 'Usage: /work-mode [help|view|tree|status]' }
    if (arg === 'help') return { text: HELP }
    if (arg === 'view') { await openDetails($); return { text: 'Work inspector: Tab/arrows navigate, Enter opens, Esc returns.' } }
    const s = await refresh($)
    if (!s) return { text: UNAVAILABLE }
    if (!s.enabled) return { text: 'Ciel Work mode is off. Turn it on: ciel-runtime menu → LLM options → Work mode.' }
    if (arg === 'tree') {
      try { return { text: await planText($) } }
      catch { return { text: 'Work tree unavailable.' } }
    }
    if (arg === '') return { text: summary(s) }
    return { text: 'Ciel Work mode\n' + JSON.stringify(s, null, 2) }
  })
  on('tool.call', async ($, e, next) => {
    const work = e.tool.startsWith(PREFIX) ? e.tool.slice(PREFIX.length) : ''
    const name = WORK.has(work) ? work : null
    // A refresh raised by this mod must not recursively refresh and supersede
    // its own generation (including the Stop check). Model calls still refresh.
    if (name === 'work_status' && next.origin.plugin === 'ciel-work') return next(e)
    const knownWrite = WRITES.has(e.tool)
    // Planning the work must not require the board's network permission.
    // Native host permissions still decide this exact plan-file operation.
    if (knownWrite && await nativePlanWrite($, e.tool, e, e.agentId)) return next(e)
    let s = null
    if (name || knownWrite || SHELLS.has(e.tool)) s = await refresh($)
    if (SHELLS.has(e.tool) && !s && enabled) return { deny: UNAVAILABLE }
    if (knownWrite) {
      const reason = blocked(s)
      const exactNativePlan = await nativePlanWrite($, e.tool, e, e.agentId)
      if (reason && !exactNativePlan) {
        hintPlanning($, s, reason); return { deny: reason }
      }
    }
    let input = e
    if (name) {
      try { input = { ...e, session_id: await sessionId($) } }
      catch { return { deny: UNAVAILABLE } }
    }
    if (name === 'work_execute') {
      const { user_confirmed, ...clean } = input
      input = clean
      if (!s && enabled) return { deny: UNAVAILABLE }
      if (s?.enabled && s.approval === 'ask') {
        let answer
        const approvalSession = await sessionId($)
        try {
          const plan = await planText($)
          if (plan.length > 24000) return { deny: 'Plan too long for approval; keep planning.' }
          answer = await $.ui.ask('Approve this Work plan?\n\n' + plan, ['Approve', 'Keep planning'])
        } catch { return { deny: 'Work approval was not obtained; keep planning.' } }
        if (answer !== 'Approve') return { deny: 'Work plan not approved; keep planning.' }
        const current = await refresh($)
        if (await sessionId($) !== approvalSession || !current || !current.enabled || current.version !== s.version
            || current.phase !== s.phase || current.approval !== s.approval
            || current.active_root?.id !== s.active_root?.id) {
          return { deny: 'Work plan changed or became unavailable during approval; review it again.' }
        }
        input = { ...clean, user_confirmed: true, expected_version: current.version }
      }
    }
    try { return await next(input) } finally { if (name) await refresh($) }
  })
  // Classification comes from the engine. Permission bypass may skip tool.check.
  on('tool.check', async ($, e, next) => {
    if (!SHELLS.has(e.tool) && !WRITES.has(e.tool)) return next(e)
    if (await nativePlanWrite($, e.tool, e.input, e.agentId)) return next(e)
    const s = await refresh($)
    const reason = blocked(s)
    const planWrite = await nativePlanWrite($, e.tool, e.input, e.agentId)
    if (reason && !planWrite && (WRITES.has(e.tool) || e.isReadOnly !== true)) { hintPlanning($, s, reason); return { decision: 'deny', reason } }
    return next(e)
  })
  on('session.end', async ($, e, next) => {
    stopIdleRefresh(); interactive = false; working = false; clearNativePlanPaths(); starts++
    generation++; cached = null; cachedSession = null; hinted = ''; continuationVersion = null; unchangedContinuations = 0
    stopNoticeKey = ''
    await update($, detailView, () => null)
    return next(e)
  })
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (!enabled || e.props.hasSurvey) return next(e)
    if (cachedSession && await sessionId($) !== cachedSession) return next(e)
    const origin = cachedSession
    const { Box, Button, Text } = $.ui.resolve(e)
    if (!cached) return Text({ dimColor: true, children: 'Work unavailable — /work-mode status' })
    if (!cached.active_root) return Text({ dimColor: true, children: 'Work mode: no plan yet — the model plans before editing · /work-mode help' })
    const width = Math.max(16, e.props.bodyColumns - 8)
    const rows = []
    for (const [i, b] of (cached.breakdown || []).entries()) {
      rows.push(statusButton(Box, Button, Text, b, { key: 'branch-' + b.id,
        plain: true, ...(i < 9 ? { hotkey: String(i + 1) } : {}), ...(i === 0 ? { autoFocus: true } : {}),
        label: mark(b.status) + ' ' + itemTitle(b, width),
        onPress: () => openDetails($, b.id, origin) }))
      for (const child of b.children || []) rows.push(statusButton(Box, Button, Text, child, {
        key: 'child-' + child.id, plain: true,
        label: '  ' + mark(child.status) + ' ' + itemTitle(child, width - 2),
        onPress: () => openDetails($, child.id, origin) }))
      if (b.more) rows.push(Text({ key: 'more-' + b.id, dimColor: true, children: '  +' + b.more + ' more · Enter to inspect' }))
    }
    return Box({ flexDirection: 'column', children: [
      Text({ bold: true, children: 'Work ' + (cached.phase === 'planning' ? 'planning' : 'running') + ' ' + cached.done + '/'
        + cached.total + ' — ' + itemTitle(cached.active_root, width) }),
      ...rows,
      ...queued(cached, width, Text),
      Button({ key: 'work-all', plain: true, label: 'All items / refresh', hotkey: 'w', onPress: () => openDetails($, null, origin) }),
      Text({ dimColor: true, children: 'Ctrl+X Tab: focus · Tab/↑↓: select · Enter: inspect · Esc: return · /work-mode help' }),
    ] })
  })
  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e, next) => {
    const { Box, Button, Text } = $.ui.resolve(e)
    const view = await read($, detailView)
    const identity = await sessionId($)
    const origin = identity
    const valid = enabled && cached && view?.session === identity && cachedSession === identity
    const fresh = valid && view.version === cached.version
    const selected = fresh ? view.nodes.find(n => n.id === view.id) : null
    const controls = [
      Button({ key: 'detail-refresh', plain: true, hotkey: 'r', label: 'Refresh', onPress: () => openDetails($, valid ? view.id : null, origin) }),
      Button({ key: 'detail-all', plain: true, hotkey: 'w', label: 'All items', onPress: () => openDetails($, null, origin) }),
      ...(selected?.parent_id ? [Button({ key: 'detail-parent', plain: true, hotkey: 'b', label: 'Parent',
        onPress: () => openDetails($, selected.parent_id, origin) })] : []),
      Button({ key: 'detail-close', plain: true, hotkey: 'q', label: 'Close', onPress: () => $.ui.close({ id: PANE }) }),
    ]
    if (!fresh || view.error || !selected) return Box({ flexDirection: 'column', children: [
      Text({ children: !valid ? 'Work details unavailable for this conversation.' : !fresh ? 'Plan changed — refresh to inspect current items.'
        : view.error || 'No active goal.' }), ...controls] })
    const rows = []
    const visit = (node, depth) => {
      rows.push(statusButton(Box, Button, Text, node, { key: 'node-' + node.id, plain: true,
        label: '  '.repeat(depth) + mark(node.status) + ' ' + itemTitle(node, 480),
        onPress: () => openDetails($, node.id, origin) }))
      if (node.note) rows.push(Text({ children: '  '.repeat(depth + 1) + 'Note: ' + clipped(node.note, 2000) }))
      if (node.evidence) rows.push(Text({ children: '  '.repeat(depth + 1) + 'Evidence: ' + clipped(node.evidence, 2000) }))
      for (const child of view.nodes.filter(n => n.parent_id === node.id)) visit(child, depth + 1)
    }
    visit(selected, 0)
    if (!selected.parent_id) {
      const roots = [cached.active_root, ...(cached.queue || [])].filter(n => n && n.status !== 'done')
      for (const [i, root] of roots.entries()) {
        rows.push(Text({ key: 'order-' + root.id, children: 'Priority ' + (i + 1) + ': ' + itemTitle(root, 360)
          + (root.held ? ' [held]' : '') }))
        if (i > 0) rows.push(Button({ key: 'up-' + root.id, plain: true, label: 'Move up',
          onPress: () => moveRoot($, root.id, -1, origin) }))
        if (i + 1 < roots.length) rows.push(Button({ key: 'down-' + root.id, plain: true, label: 'Move down',
          onPress: () => moveRoot($, root.id, 1, origin) }))
      }
    }
    return Box({ flexDirection: 'column', children: [Text({ bold: true, children: 'Work details — version ' + view.version }),
      ...controls, ...rows, Text({ dimColor: true, children: 'Tab/↑↓: navigate/scroll · Enter: inspect node · Esc: close' })] })
  })
  on('ui.render', { component: 'SessionMode' }, async ($, e, next) => {
    const label = !enabled ? null : !cached ? 'work: unavailable'
      : breakdownLabel(cached)
    return next({ ...e, props: { ...e.props, modes: [
      ...(e.props.modes || []).filter(m => !m.startsWith('work: ')), ...(label ? [label] : [])] } })
  })
}

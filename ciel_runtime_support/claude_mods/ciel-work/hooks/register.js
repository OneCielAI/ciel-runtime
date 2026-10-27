import { atom, read, update } from 'claude-code'

// Router is authoritative; this cache is display-only and never persisted.
const detailView = atom({ plugin: 'ciel-work', key: 'detail' }, null)
const panelView = atom({ plugin: 'ciel-work', key: 'panel' }, null)
const PANE = 'work-details'
const PREFIX = 'mcp__ciel-runtime-router__'
const WORK = new Set(['work_status', 'work_plan_set', 'work_node_add', 'work_node_update',
  'work_node_remove', 'work_select', 'work_execute', 'work_replan', 'work_reorder', 'work_urgent'])
const WRITES = new Set(['Edit', 'Write', 'NotebookEdit', 'apply_patch'])
const UNAVAILABLE = 'Work status unavailable: router connection or snapshot could not be verified.'
const PLANNING = 'Work mode is planning: direct file edits wait until the plan is approved. '
  + 'Build it with work_plan_set, then call work_execute. (/work-mode help)'
const NOTICE = 'Ciel Work mode is on: plan and execute by item; new independent requests queue without pausing approved Work. '
  + 'Items show above the prompt · /work-mode help · turn off: ciel-runtime menu → LLM options → Work mode'
const HELP = [
  'Ciel Work mode — hybrid planning and execution',
  '• Work is not native Plan Mode. Queued planning does not pause active execution. within_scope=true is only for refinements inside approved scope; new scope needs review.',
  '• Context reminders omit queued details. Inspect one goal on demand; raw user messages remain in native history, not isolated by Work.',
  '• Planning: the model builds a plan with work_plan_set: goal → phases → tasks → verifiable steps. Reading is allowed; direct file edits wait. Bash/PowerShell use native permissions and sandboxing, not Work gates.',
  '• New requests: an independent request becomes its own numbered item ("2. …") and waits in the queue behind the active item; a change to an existing item is added under that item.',
  '• Executing: after work_execute (automatic, or your approval when set to ask) the model works step by step and marks each step done with evidence.',
  '• Above the prompt: Progress / Queue / Completed are read-only button tabs; Prev / Next (p / n) page the list; w opens the Inspector. Ctrl+X Tab focuses · Tab/↑↓ select · Enter inspects · Esc returns. Tiny screens use Inspect Work. Rows of other plugins stay below the Work rows; the band scrolls to them.',
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
const INSTRUCTIONS = 'Ciel Work mode is hybrid per-item planning and execution, not native Plan Mode. Before edits or state-changing commands for an unapproved goal, use '
  + 'work_plan_set: goal -> phases -> tasks -> concrete verifiable steps. Triage every new user '
  + 'request first: an independent request becomes its own goal (root-level item), its title starting '
  + 'with its item number where possible ("1. ...", "2. ..."); a request that changes or extends an '
  + 'existing item goes under that item as a child (work_node_add with parent_id). A new goal queues '
  + 'behind the unfinished active goal and becomes active when it is done; keep the active goal going. '
  + 'Read-only exploration '
  + 'is allowed while planning. Call work_execute when ready; auto approval is default and '
  + 'configured ask approval uses the Work dialog. Never supply user_confirmed yourself. '
  + 'During execution mark the current leaf in_progress with work_node_update, then done only '
  + 'with verification evidence. Add/split nodes using work_node_add with within_scope=true only for refinements wholly within already approved scope; otherwise leave it false and review/approve the changed scope. Planning queued goals must not pause approved active work. Use work_replan when the '
  + 'approach changes. Use work_status(root_id=...) for exact goal details only when needed, and expected_version for mutations. Automatic context excludes queued details, but raw user messages remain in native history; this is not history isolation. '
  + 'Bash and PowerShell permission decisions belong to the native host, not the Work board. Work does not inspect shell side effects or override native permissions; keep commands within the approved scope. '
  + 'These workflow rules include subagents: do not use a shell or delegate changes to evade required planning or approval. '
  + 'Set ref for a linked id, and waiting_on for what an item waits on (empty text clears). '
  + 'Use work_reorder to change root order: queue-only by default; changing the active slot needs an explicit '
  + 'user request and user_requested=true, except that when every active step waits or is blocked, work_select may move to '
  + 'the queued goal holding the next runnable step. On an explicit urgent/ad-hoc request use work_urgent with '
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
      await update($, panelView, () => null)
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
// Deliberately select fields instead of slicing serialized snapshots (invalid JSON,
// accidental queue titles/notes, and arbitrary loss of active context).
function promptSummary(s) {
  const node = n => n ? { id: clipped(n.id, 64), title: clipped(n.title, 180), status: n.status } : null
  return { version: s.version, phase: s.phase, approval: s.approval,
    active_root: node(s.active_root), done: s.done, total: s.total,
    next: node(s.next), ready: s.ready,
    queue_count: s.queue?.length || 0,
    queued_ids: (s.queue || []).slice(0, 5).map(n => clipped(n.id, 64)),
    detail: 'work_status(root_id=...) on demand; raw user messages remain in native history' }
}
async function planText($, rootId = null) {
  return textOf(await $.mcp.call('ciel-runtime-router', 'work_status', { format: 'text', ...(rootId ? { root_id: rootId } : {}), session_id: await sessionId($) }))
}
function clipped(value, limit) {
  const text = String(value || '').replace(/[\x00-\x1f\x7f-\x9f]/g, ' ').replace(/\s+/g, ' ').trim()
  return text.length <= limit ? text : text.slice(0, limit - 1) + '…'
}
function itemTitle(node, width) {
  return clipped((node.ref ? '[ ' + node.ref + ' ] ' : '') + node.title
    + (node.urgent ? ' [urgent]' : '') + (node.waiting_on ? ' ◷ ' + node.waiting_on : ''), width)
}
// Indentation and row prefixes are kept outside the clipped (whitespace-collapsing) text.
function rowLabel(prefix, node, width, suffix = '') {
  return prefix + clipped(mark(node.status) + ' ' + itemTitle(node, width) + suffix, Math.max(1, width - prefix.length))
}
function statusButton(Box, Button, Text, node, props) {
  const button = Button({ ...props, children: Text({ wrap: 'truncate-end', children: props.label }) })
  return Box({ height: 1, flexShrink: 0, overflow: 'hidden', flexDirection: 'row', children: [
    ...(node.status === 'done' ? [Text({ color: 'success', children: '✓ ' })] : []), button] })
}
async function moveRoot($, id, offset, origin) {
  if (await sessionId($) !== origin) return
  const s = await refresh($)
  if (!s?.enabled || await sessionId($) !== origin) return
  const roots = [s.active_root, ...queueOf(s)].filter(Boolean)
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
// Display-only summaries. A tab or page change never fetches a queued subtree.
function rootsOf(s) {
  return Array.isArray(s.root_index) ? s.root_index : [s.active_root, ...(s.queue || [])].filter(Boolean)
}
function queueOf(s) { return rootsOf(s).filter(n => n.id !== s.active_root?.id && n.status !== 'done') }
function panelRows(s, tab) {
  if (tab === 'queue') {
    // Step counts exist for the snapshot's queue preview; later roots show their status only.
    const counted = new Map((s.queue || []).map(q => [q.id, q]))
    return queueOf(s).map(node => ({ key: 'queue-' + node.id, node, prefix: node.held ? 'Held: ' : 'Queued: ',
      suffix: Number.isInteger(counted.get(node.id)?.total) ? ' ' + counted.get(node.id).done + '/' + counted.get(node.id).total : '' }))
  }
  if (tab === 'completed') {
    const rows = rootsOf(s).filter(n => n.status === 'done').map(node => ({ key: 'finished-' + node.id, node, prefix: 'Goal: ' }))
    // New snapshots enumerate done rows throughout the active tree. Older routers
    // still expose done branch/child summaries; the inspector reads the exact tree.
    const active = Array.isArray(s.completed_active) ? s.completed_active
      : (s.breakdown || []).flatMap(n => [n, ...(n.children || [])]).filter(n => n.status === 'done')
    return [...rows, ...active.filter(node => node.status === 'done').map(node => ({ key: 'completed-' + node.id, node, prefix: '  ' }))]
  }
  const rows = []
  if (s.active_root?.status === 'done') return rows
  for (const branch of s.breakdown || []) {
    if (branch.status === 'done') continue
    rows.push({ key: 'branch-' + branch.id, node: branch, prefix: '' })
    for (const child of branch.children || []) if (child.status !== 'done') {
      rows.push({ key: 'child-' + child.id, node: child, prefix: '  ' })
    }
    if (branch.more) rows.push({ key: 'more-' + branch.id, node: branch,
      prefix: '  +' + branch.more + ' more · inspect ' })
  }
  return rows
}
// Conservatively count every non-ASCII code point as two terminal cells. This
// may leave space, but never drops the right half of a wide-character detail.
function detailLines(value, width) {
  const rows = []
  for (const line of String(value).replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f]/g, ' ').split('\n')) {
    let chunk = ''; let cells = 0
    for (const char of line) {
      const size = char.charCodeAt(0) < 128 ? 1 : 2
      if (chunk && cells + size > width) { rows.push(chunk); chunk = ''; cells = 0 }
      chunk += char; cells += size
    }
    rows.push(chunk)
  }
  return rows
}
function pageOf(rows, page, size) {
  const pages = Math.max(1, Math.ceil(rows.length / size))
  const current = Math.min(Math.max(0, page || 0), pages - 1)
  return { rows: rows.slice(current * size, (current + 1) * size), current, pages }
}
async function displayState($, origin, change) {
  if (!origin || cachedSession !== origin || await sessionId($) !== origin) return
  await update($, panelView, old => change(old?.session === origin ? old
    : { session: origin, tab: 'progress', page: 0, detailPage: 0 }))
}
function bandBudget(e) {
  const available = Math.max(0, Math.floor(e.props.maxRows))
  const viewport = Math.max(0, Math.floor(e.viewport?.rows ?? available * 2))
  // Whole band: a quarter of the viewport, at most ten rows. Leave room for
  // the prompt/native areas even when maxRows itself is only a few rows.
  const total = Math.max(0, Math.min(10, Math.floor(viewport / 4), available - 2))
  const native = Math.min(2, Math.max(0, total - 1))
  return { total, native, work: total - native }
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
    const previous = await read($, detailView)
    const queuedRoot = rootsOf(s).find(n => n.id === id)
    const priorRoot = previous?.session === identity && previous.nodes?.some(n => n.id === id)
      ? previous.nodes.find(n => n.parent_id === null) : null
    const rootId = queuedRoot?.id || priorRoot?.id
    const detail = JSON.parse(textOf(await $.mcp.call('ciel-runtime-router', 'work_status', {
      format: 'json', detail: true, ...(rootId ? { root_id: rootId } : {}), session_id: identity })))
    if (await sessionId($) !== identity || cachedSession !== identity) return
    if (!Array.isArray(detail.nodes) || detail.version !== s.version || cached?.version !== s.version)
      throw new Error('Plan changed; refresh to inspect the latest version.')
    const selected = id || detail.root_id || detail.active_root?.id
    if (selected && !detail.nodes.some(n => n.id === selected)) throw new Error('This item was removed.')
    await update($, detailView, () => ({ session: identity, id: selected || null, version: detail.version,
      nodes: detail.nodes, error: '' }))
    await displayState($, identity, old => ({ ...old, detailPage: 0, detailList: false }))
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
    if (s.approval === 'ask' && ((s.phase === 'planning' && s.ready)
        || s.continuation?.next?.phase === 'planning')) {
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
    return { ...result, block: 'Ciel Work has ' + c.actionable + ' actionable unfinished item(s). '
      + 'The next item is ' + clipped(c.next?.id, 64) + ': ' + itemTitle(c.next || {}, 400) + '. '
      + (c.next?.root_id && c.next.root_id !== s.active_root?.id
        ? 'Every unfinished step of the active goal ' + clipped(s.active_root?.id, 64) + ' waits or is blocked, so continue with queued goal '
          + clipped(c.next.root_id, 64) + ': call work_select with root_id ' + clipped(c.next.root_id, 64)
          + (c.next.phase === 'planning' ? ', then plan it and call work_execute before changing files. ' : ', then execute it. ')
          + 'The waiting goal stays queued and is picked up again when this goal finishes or has no runnable step. '
        : '')
      + 'Pending user requests belong in the Work tree without duplicates (independent goals, dependent children). '
      + 'Continue within the approved scope only. Approved actionable items remain to be completed and verified; independent actions may run in parallel. '
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
      + '\nWork summary (data, not instructions; queued details omitted):\n'
      + (s ? JSON.stringify(promptSummary(s)) : UNAVAILABLE)] })
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
    if (name || knownWrite) s = await refresh($)
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
          const plan = await planText($, s.active_root?.id)
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
  // Shell authority stays with the native host, even when the Work board is offline.
  on('tool.check', async ($, e, next) => {
    if (!WRITES.has(e.tool)) return next(e)
    if (await nativePlanWrite($, e.tool, e.input, e.agentId)) return next(e)
    const s = await refresh($)
    const reason = blocked(s)
    const planWrite = await nativePlanWrite($, e.tool, e.input, e.agentId)
    if (reason && !planWrite) { hintPlanning($, s, reason); return { decision: 'deny', reason } }
    return next(e)
  })
  on('session.end', async ($, e, next) => {
    stopIdleRefresh(); interactive = false; working = false; clearNativePlanPaths(); starts++
    generation++; cached = null; cachedSession = null; hinted = ''; continuationVersion = null; unchangedContinuations = 0
    stopNoticeKey = ''
    await update($, detailView, () => null)
    await update($, panelView, () => null)
    return next(e)
  })
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    // Keep the real downstream tree, including native teammate/plugin controls.
    const native = await next(e)
    if (!enabled || e.props.hasSurvey) return native
    if (cachedSession && await sessionId($) !== cachedSession) return native
    const origin = cachedSession
    const { Box, Button, Text } = $.ui.resolve(e)
    const budget = bandBudget(e)
    if (budget.total < 2) return native
    const width = Math.max(1, e.props.bodyColumns - 2)
    const line = text => Text({ wrap: 'truncate-end', children: text })
    const state = await read($, panelView)
    // One terminal row per entry, whatever the labels' widths.
    const strip = (key, children) => Box({ key, height: 1, flexShrink: 0, overflow: 'hidden', flexDirection: 'row', gap: 1, children })
    const tab = state?.session === origin ? state.tab : 'progress'
    const rows = cached ? panelRows(cached, tab) : []
    const counts = cached ? { progress: panelRows(cached, 'progress').length, queue: queueOf(cached).length,
      completed: panelRows(cached, 'completed').length } : { progress: 0, queue: 0, completed: 0 }
    const heading = !cached ? 'Work unavailable — /work-mode status'
      : !cached.active_root ? 'Work mode: no plan yet — /work-mode help'
      : 'Work ' + (cached.phase === 'planning' ? 'planning' : 'running') + ' ' + cached.done + '/' + cached.total
        + ' — ' + itemTitle(cached.active_root, width)
    const work = []
    // Tiny/narrow terminals get an actionable summary; the inspector carries
    // all three paged lists instead of leaving clipped-off controls unreachable.
    if (budget.work < 5 || width < 58) {
      work.push(strip('work-summary', [
        Button({ key: 'work-all', plain: true, autoFocus: true,
          label: clipped('Inspect Work' + (cached ? ' ' + cached.done + '/' + cached.total + ' · Q ' + counts.queue + ' · Done ' + counts.completed : ' unavailable'), width),
          onPress: () => openDetails($, null, origin) }),
      ]))
    } else {
      work.push(line(heading))
      work.push(strip('work-tabs', ['progress', 'queue', 'completed'].map(name => Button({
        key: 'work-tab-' + name, plain: true, ...(name === tab ? { autoFocus: true } : {}),
        label: (name === tab ? '[' : '') + name[0].toUpperCase() + name.slice(1) + ' ' + counts[name] + (name === tab ? ']' : ''),
        onPress: () => displayState($, origin, old => ({ ...old, tab: name, page: 0, detailPage: 0 })),
      }))))
      const page = pageOf(rows, state?.page, budget.work - 3)
      for (const row of page.rows) work.push(statusButton(Box, Button, Text, row.node, {
        key: row.key, plain: true, label: rowLabel(row.prefix, row.node, width, row.suffix),
        onPress: () => openDetails($, row.node.id, origin) }))
      if (!rows.length) work.push(line(tab === 'progress' ? 'No unfinished rows · inspect the active goal' : 'No ' + tab + ' items'))
      work.push(strip('work-footer', [
        ...(page.current > 0 ? [Button({ key: 'work-prev', plain: true, hotkey: 'p', label: 'Prev',
          onPress: () => displayState($, origin, old => ({ ...old, page: page.current - 1 })) })] : []),
        line((page.current + 1) + '/' + page.pages),
        ...(page.current + 1 < page.pages ? [Button({ key: 'work-next', plain: true, hotkey: 'n', label: 'Next',
          onPress: () => displayState($, origin, old => ({ ...old, page: page.current + 1 })) })] : []),
        Button({ key: 'work-all', plain: true, label: 'Inspector', hotkey: 'w', onPress: () => openDetails($, null, origin) }),
      ]))
    }
    // Only the Work panel is bounded. The host refuses an engine-drawn element under any Box that
    // sizes, clips or positions it (display, overflow, position, width/height and their minimums,
    // top/left/right/bottom), so the tree next(e) returned follows the panel unwrapped and the
    // engine's band scroll owns its height. The budget's reserve keeps its first rows on screen.
    return Box({ key: 'work-band', flexDirection: 'column', children: [
      Box({ key: 'work-panel', height: Math.min(work.length, budget.work), flexShrink: 0, overflow: 'hidden',
        flexDirection: 'column', children: work }),
      native,
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
    const state = await read($, panelView)
    const tab = state?.session === identity ? state.tab : 'progress'
    const tabs = Box({ flexDirection: 'row', gap: 1, children: ['progress', 'queue', 'completed'].map(name => Button({
      key: 'detail-tab-' + name, plain: true, label: (name === tab ? '[' : '') + name[0].toUpperCase() + name.slice(1) + (name === tab ? ']' : ''),
      onPress: () => displayState($, origin, old => ({ ...old, tab: name, page: 0, detailPage: 0, detailList: true })),
    })) })
    if (!valid) return Box({ flexDirection: 'column', children: [Text({ children: 'Work details unavailable for this conversation.' }), ...controls] })
    const rows = []
    const width = Math.max(1, e.props.bodyColumns - 2)
    const text = value => Text({ wrap: 'truncate-end', children: value })
    const listing = state?.session === identity && state.detailList === true
    if (listing) {
      for (const row of panelRows(cached, tab)) rows.push(statusButton(Box, Button, Text, row.node, {
        key: 'detail-' + row.key, plain: true, label: rowLabel(row.prefix, row.node, width, row.suffix),
        onPress: () => openDetails($, row.node.id, origin) }))
      if (!rows.length) rows.push(text('No ' + tab + ' items'))
    } else if (!fresh || view.error || !selected) rows.push(text(!fresh ? 'Plan changed — refresh to inspect current items.' : view.error || 'No active goal.'))
    else {
      const visit = (node, depth) => {
        rows.push(statusButton(Box, Button, Text, node, { key: 'node-' + node.id, plain: true,
          label: rowLabel('  '.repeat(Math.min(depth, 8)), node, width),
          onPress: () => openDetails($, node.id, origin) }))
        // Exact fields are shown only for the inspected node. Full content,
        // including a title shortened in the band, remains reachable by paging.
        if (node.id === selected.id) for (const [label, value] of [['Title', node.title], ['Ref', node.ref],
          ['Waiting on', node.waiting_on], ['Note', node.note], ['Evidence', node.evidence]]) {
          if (value) for (const line of detailLines(label + ': ' + value, width)) rows.push(text(line))
        }
        for (const child of view.nodes.filter(n => n.parent_id === node.id)) visit(child, depth + 1)
      }
      visit(selected, 0)
    }
    if (!listing && fresh && selected && !selected.parent_id) {
      const roots = [cached.active_root, ...queueOf(cached)].filter(n => n && n.status !== 'done')
      for (const [i, root] of roots.entries()) {
        rows.push(text('Priority ' + (i + 1) + ': ' + itemTitle(root, width) + (root.held ? ' [held]' : '')))
        if (i > 0) rows.push(Button({ key: 'up-' + root.id, plain: true, label: 'Move up',
          onPress: () => moveRoot($, root.id, -1, origin) }))
        if (i + 1 < roots.length) rows.push(Button({ key: 'down-' + root.id, plain: true, label: 'Move down',
          onPress: () => moveRoot($, root.id, 1, origin) }))
      }
    }
    const page = pageOf(rows, state?.detailPage, Math.max(1, Math.min(10, e.props.scroll.bodyRows - 9)))
    return Box({ flexDirection: 'column', children: [text('Work details — version ' + cached.version), tabs,
      ...controls, ...page.rows,
      Box({ flexDirection: 'row', gap: 1, children: [
        ...(page.current > 0 ? [Button({ key: 'detail-prev', plain: true, hotkey: 'p', label: 'Prev',
          onPress: () => displayState($, origin, old => ({ ...old, detailPage: page.current - 1 })) })] : []),
        text((page.current + 1) + '/' + page.pages),
        ...(page.current + 1 < page.pages ? [Button({ key: 'detail-next', plain: true, hotkey: 'n', label: 'Next',
          onPress: () => displayState($, origin, old => ({ ...old, detailPage: page.current + 1 })) })] : []),
      ] }), text('Tab/↑↓: navigate · Enter: inspect · Esc: close')] })
  })
  on('ui.render', { component: 'SessionMode' }, async ($, e, next) => {
    const label = !enabled ? null : !cached ? 'work: unavailable'
      : breakdownLabel(cached)
    return next({ ...e, props: { ...e.props, modes: [
      ...(e.props.modes || []).filter(m => !m.startsWith('work: ')), ...(label ? [label] : [])] } })
  })
}

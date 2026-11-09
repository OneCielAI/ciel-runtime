import { atom, read, update } from 'claude-code'
import { startSide, pollSide, sideSpawnInput, sideTool, completeSide, suppressSide, sideReceipts, sideActors, sideMcpParity, hostJson, eventView } from './side-intake.js'

// Router is authoritative; this cache is display-only and never persisted.
const detailView = atom({ plugin: 'ciel-work', key: 'detail' }, null)
const panelView = atom({ plugin: 'ciel-work', key: 'panel' }, null)
const PANE = 'work-details'
const PREFIX = 'mcp__ciel-runtime-router__'
const WORK = new Set(['work_discovery_pickup', 'work_parallel_status', 'work_parallel_configure', 'work_parallel_run', 'work_parallel_event', 'work_status', 'work_plan_set', 'work_node_add', 'work_node_update',
  'work_node_remove', 'work_select', 'work_execute', 'work_replan', 'work_reorder', 'work_urgent', 'work_focus',
  'work_mode_set'])
const WRITES = new Set(['Edit', 'Write', 'NotebookEdit', 'apply_patch'])
const UNAVAILABLE = 'Work status unavailable: router connection or snapshot could not be verified.'
const PLANNING = 'Work mode is planning: direct file edits wait until the plan is approved. '
  + 'Build it with work_plan_set, then call work_execute. (/work-mode help)'
const NOTICE = 'Ciel Work mode is on: plan and execute by item; new independent requests queue without pausing approved Work. '
  + 'Items show above the prompt · /work-mode help · turn off: ciel-runtime menu → LLM options → Work mode, or the agent (work_mode_set)'
const HELP = [
  'Ciel Work mode — hybrid planning and execution',
  '• Work is not native Plan Mode. Queued planning does not pause active execution. within_scope=true is only for refinements inside approved scope; new scope needs review.',
  '• Context reminders omit queued details. Inspect one goal on demand; raw user messages remain in native history, not isolated by Work.',
  '• Planning: the model builds a plan with work_plan_set: goal → phases → tasks → verifiable steps. Reading is allowed; direct file edits wait. Bash/PowerShell use native permissions and sandboxing, not Work gates.',
  '• New requests: an independent request becomes its own numbered item ("2. …") and waits in the queue behind the active item; a change to an existing item is added under that item.',
  '• Executing: after work_execute (automatic, or your approval when set to ask) the model works step by step and marks each step done with evidence.',
  '• Above the prompt: compact by default (done/total + current step); [+] / w expands, [-] collapses. Progress / Queue / Completed are read-only button tabs; Prev / Next (p / n) page the list; w opens the Inspector. Ctrl+X Tab focuses · Tab/↑↓ select · Enter inspects · Esc returns. Tiny screens use Inspect Work. Rows of other plugins stay below the Work rows; the band scrolls to them.',
  '• /work-mode view: Work & SSE pane — Work tree (w), SSE receipts with P1 filter (s), every Work setting incl. the live side model (g); mobile and narrow screens get a single-column touch layout.',
  '• /work-mode expand / compact: toggle the inline list · /work-mode tree: plan tree · /work-mode status: raw snapshot (JSON) · /work-mode parallel: fork slots, leases and the next Agent name · /work-mode help: this help',
  '• Focus: inspect an item → Focus first. Focused items run before other Work; a focused item that waits or is blocked never holds unrelated approved Work or discovery. Clear focus releases it now; it also clears automatically once every focused item is done.',
  '• Priorities: All items → Move up / Move down orders root goals. Moving the active goal requires your request; a button press is that request.',
  '• Linked IDs appear as [ id ] description; ◷ names an external wait; green ✓ means verified done. An urgent request holds other goals until it finishes.',
  '• Auto continue: normal turn endings resume actionable Work, bounded by no-progress checks; waits, blocks and approval do not trigger it; background tasks do not block independent Work, and a dim line says why it stopped. Codex receives instructions only.',
  '• Completed items expire after 24 hours by default; progress counts are preserved. Toggle: LLM options → Work completed auto-delete (24h).',
  '• Settings: ciel-runtime menu → LLM options → Work mode (on/off), Work plan approval (auto/ask), Work auto continue (on/off).',
  '• The agent can switch the same workspace Work mode setting itself with work_mode_set (enabled true/false); plans are kept.',
].join('\n')
let cached = null
let enabled = true
let generation = 0
let mainTurnEpoch = 0
let cachedSession = null
let hinted = ''
let starts = 0
let noticed = 0
let continuationVersion = null
let unchangedContinuations = 0
let stopNoticeKey = ''
let compactEpoch = 0
const compacts = new Set()
let continuationSession = null
const seenSideReceipts = new Set()
let sidePanelReceipts = []
let sidePanelSession = null
// Display-only selection in the side pane; never persisted.
let sidePaneView = { receipt: null, page: 0 }
let observatorySettings = null
let observatoryParallel = null
// A launch with side model inherit pins every child to the main model
// (CLAUDE_CODE_SUBAGENT_MODEL_FORCE, d15822f); a selected model then applies
// only after a relaunch. Launches with a selected side model switch live.
let observatoryForced = false
const noticedSideHandoffs = new Set()
function showSideReceipts($, identity, snapshot) {
  const previous = sidePanelSession === identity ? sidePanelReceipts : []
  sidePanelSession = identity
  sidePanelReceipts = [...(snapshot.side_intake_receipts || []), ...(snapshot.side_inbox_receipts || [])].reverse()
    .filter((r, i, rows) => rows.findIndex(x => (x.receipt_id || x.item_id) === (r.receipt_id || r.item_id)) === i)
    // Main model snapshots deliberately omit side originals/results. They must
    // not erase the host-only projection already shown in this session's pane.
    .map(r => ({ ...previous.find(p => (p.receipt_id || p.item_id) === (r.receipt_id || r.item_id)), ...r }))
  $.ui.invalidate('ui.render')
  for (const receipt of sidePanelReceipts) {
    const key = identity + ':' + (receipt.receipt_id || receipt.item_id) + ':' + (receipt.stage || '')
    if (seenSideReceipts.has(key)) continue
    seenSideReceipts.add(key)
    void $.ui.open({ id: 'ciel-side-intake', title: 'btw_with_mcp' }).catch(() => {})
    if (seenSideReceipts.size > 200) seenSideReceipts.delete(seenSideReceipts.values().next().value)
  }
}
let sidePoll = null
async function sideMailboxCall($, name, args = {}) {
  let timer
  try {
    return await Promise.race([
      parallelCall($, name, args),
      new Promise((resolve, reject) => { timer = setTimeout(() => reject(new Error('mailbox operation timed out; durable inbox retained')), 2000) }),
    ])
  } finally { clearTimeout(timer) }
}
function pollBusySideReceipts($, identity) {
  if (sidePoll?.identity === identity && sidePoll.lifecycle === starts) return
  const poll = { identity, lifecycle: starts }
  sidePoll = poll
  const schedule = () => $.clock.after(2000, async () => {
    if (sidePoll !== poll || poll.lifecycle !== starts) return
    try {
      if (await sessionId($) !== identity) return
      // A full status projection can page through many private receipts. Claim
      // first so display hydration can never starve the side worker.
      await pollSide($, identity)
      if (sidePoll !== poll || poll.lifecycle !== starts || await sessionId($) !== identity) return
      const snapshot = await sideMailboxCall($, 'work_side_status')
      if (sidePoll !== poll || poll.lifecycle !== starts || await sessionId($) !== identity) return
      showSideReceipts($, identity, snapshot)
    } catch { /* Durable inbox and pane state remain authoritative. */
    } finally {
      if (sidePoll === poll && poll.lifecycle === starts) schedule()
    }
  })
  schedule()
}
let discoveryLast = 0
let discoveryIdentity = null
const STOP_READ_MS = 2000
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
  + 'When the user asks to prioritize specific existing items, resolve the exact IDs from Work status and call work_focus with ordered node_ids and user_requested=true. Focus orders the work first but does not hold unrelated approved items: when no focused step can run, keep working other approved items and discovery. Focus clears automatically once every focused step is done; node_ids=[] clears it now. Do not duplicate goals or mark unrelated items blocked. '
  + 'Use work_reorder to change root order: queue-only by default; changing the active slot needs an explicit '
  + 'user request and user_requested=true, except that when every active step waits or is blocked, work_select may move to '
  + 'the queued goal holding the next runnable step. On an explicit urgent/ad-hoc request use work_urgent with '
  + 'user_requested=true to hold other goals, break down the request and execute; held goals resume afterwards.'
// A wait is preserved: discovery only asks for independent, scoped candidates.
function discoveryNotice($, identity, s, reason) {
  if (!s.discovery?.enabled) return null
  const instruction = clipped(s.discovery.instruction, 5000)
  if (!instruction) return null
  stopNotice($, identity, reason + ' · ' + instruction)
  const now = Date.now()
  if (discoveryIdentity === identity && now - discoveryLast < 300000) return null
  discoveryIdentity = identity; discoveryLast = now
  return { block: reason + '\nWork 미결 아이템 탐색: ' + instruction
    + '\n검색은 한 번, 픽업은 최대 1개. 미결 없음/접근 불가면 그 결과를 알리고 멈춘다. 사용자 정지·기존 승인을 우회하지 않는다.' }
}
// Router text as the person should see it: MCP error results carry the real
// message in their text blocks. Display callers surface it verbatim; gate
// decisions never log or return it (they stay on fixed stage codes).
function routerText(r) {
  if (!r) return ''
  return (r.content || []).filter(b => b.type === 'text').map(b => b.text).join('\n').trim()
}
function textOf(r) {
  if (!r || r.isError) throw new Error('Router error')
  const text = routerText(r)
  if (!text) throw new Error('Missing router text')
  return text
}
// Parse a JSON payload, or fail with a message that names the step and shows
// what the router actually said. A router error reply that arrives without
// isError (or any non-JSON text) must never surface as a raw JSON.parse
// complaint about its first word.
function parseJson(r, step) {
  if (!r || r.isError) throw new Error(step + ' failed: ' + (clipped(routerText(r), 160) || 'router error'))
  const text = routerText(r)
  if (!text) throw new Error(step + ' failed: router returned no text')
  try { return JSON.parse(text) }
  catch {
    throw new Error(step + ' failed: router did not return JSON: ' + clipped(text, 160))
  }
}
async function sessionId($) {
  const id = await $.session.id()
  if (typeof id !== 'string' || !/^[A-Za-z0-9_-]{1,200}$/.test(id)) throw new Error('Missing session identity')
  return 'claude:' + id
}
let gateSequence = 0
let gateIdentity = null
let gateVersion = -1
function readFailure(stage) {
  return Object.assign(new Error(stage), { stage })
}
// One verified snapshot of this conversation's board; failures name the step that failed.
async function readSnapshot($, identity) {
  let r
  // _host lets the router page a board snapshot above the native result limit.
  const token = await $.env.get('CIEL_RUNTIME_WORK_PARALLEL_HOST_TOKEN')
  try { r = await $.mcp.call('ciel-runtime-router', 'work_status', { format: 'json', session_id: identity, _host: { token } }) }
  catch (error) {
    const message = String(error?.message || '')
    // Report fixed categories only; native errors can contain credentials.
    const stage = /timed?\s*out|timeout/i.test(message) ? 'router-timeout'
      : /not connected|disconnected|connection closed|no connected MCP/i.test(message) ? 'router-disconnected'
        : /unauthori[sz]ed|authentication|sign.in|401|403/i.test(message) ? 'router-auth'
          : 'router-call'
    if (stage === 'router-disconnected') {
      // A verified reconnect permits one pure read, never a cached approval or
      // replay of an Edit/Write. Refused/unlisted/policy/auth remain closed.
      try {
        const connection = await $.mcp.connect('ciel-runtime-router')
        if (connection?.isConnected && await sessionId($) === identity)
          r = await $.mcp.call(connection.server, 'work_status', {format:'json',session_id:identity,_host:{token}})
        else throw readFailure(stage)
      } catch { throw readFailure(stage) }
    } else throw readFailure(stage)
  }
  if (!r || r.isError) throw readFailure('router-error')
  let s
  try { s = JSON.parse(textOf(r)) } catch { throw readFailure('snapshot-parse') }
  if (s?.paged) {
    try { s = await hostJson($, 'work_status', r, identity, token) } catch { throw readFailure('snapshot-paged') }
  }
  if (s?.enabled !== false && (s?.enabled !== true
    || !['planning', 'executing'].includes(s.phase) || !['auto', 'ask'].includes(s.approval)
    || !Number.isInteger(s.version) || s.version < 0 || !Number.isInteger(s.done)
    || !Number.isInteger(s.total) || s.done < 0 || s.total < s.done
    || !(s.active_root === null || (typeof s.active_root?.id === 'string'
      && typeof s.active_root.title === 'string')))) throw readFailure('snapshot-schema')
  return s
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
    const s = await readSnapshot($, identity)
    const currentIdentity = await sessionId($)
    if (ticket !== generation || currentIdentity !== identity) return null
    if (ticket === generation) {
      cached = s; enabled = s.enabled; $.ui.invalidate('ui.render')
      showSideReceipts($, identity, s)
    }
    return s
  } catch {
    if (ticket === generation) { cached = null; $.ui.invalidate('ui.render') }
    return null
  }
}
// Edit/Write and approval gates decide on their own verified read. Agents gate
// concurrently, so a read started elsewhere (another agent's gate, a new turn)
// must not turn this decision into "unavailable"; it does not bump the display
// generation either. Only a failed read or a changed conversation denies.
async function gateRead($) {
  const correlation = 'wg-' + Date.now().toString(36) + '-' + (++gateSequence).toString(36)
  const fail = stage => {
    // Only generated identifiers and fixed stage codes: never log router text or tool input.
    try { $.ui.log('Ciel Work gate ' + correlation + ': ' + stage) } catch {}
    return { s: null, stage, correlation }
  }
  const lifecycle = starts
  let identity
  try { identity = await sessionId($) } catch { return fail('identity') }
  if (gateIdentity !== identity) { gateIdentity = identity; gateVersion = -1 }
  const ticket = generation
  try {
    const s = await readSnapshot($, identity)
    let current = null
    try { current = await sessionId($) } catch {}
    if (lifecycle !== starts || current !== identity) return fail('identity-changed')
    if (s.enabled !== false) {
      if (s.version < gateVersion) return fail('snapshot-version')
      gateVersion = s.version
    }
    if (ticket === generation && cachedSession === identity
        && (!cached || s.enabled === false || cached.enabled === false || s.version >= cached.version)) {
      cached = s; enabled = s.enabled; $.ui.invalidate('ui.render')
    }
    return { s, stage: '', correlation }
  } catch (error) {
    return fail(error?.stage || 'unknown')
  }
}
function unavailable(read) {
  return UNAVAILABLE + ' [' + read.stage + (read.correlation ? '; ' + read.correlation : '') + ']'
}
// Stop alone has a wall-clock deadline: HookBudget pauses during MCP calls.
// The losing read is pure, so a late response cannot update the cache, version
// floor or continuation counters, or emit a stale gate failure notice.
async function stopSnapshot($, identity) {
  let timer
  try {
    return await Promise.race([
      readSnapshot($, identity).catch(() => null),
      new Promise(resolve => { timer = setTimeout(() => resolve(null), STOP_READ_MS) }),
    ])
  } finally { clearTimeout(timer) }
}
function compacting(identity) {
  return [...compacts].some(c => c.lifecycle === starts && (c.identity === null || c.identity === identity))
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
function blocked(read) {
  const s = read.s
  if (s?.enabled === false || (!s && !enabled)) return ''
  if (!s) return unavailable(read)
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
  if (s.focus?.node_ids?.length) lines.push('Focus first: ' + s.focus.node_ids.join(', '))
  if (s.next?.title) lines.push('Next: ' + clipped(s.next.title, 200))
  if (s.queue?.length) {
    lines.push('Queue (next first):')
    for (const q of s.queue) lines.push('  ' + (q.status === 'done' ? '✓' : mark(q.status)) + ' ' + itemTitle(q, 240) + ' ' + q.done + '/' + q.total)
  }
  lines.push('/work-mode help · view · tree · status · parallel')
  return lines.join('\n')
}
// Deliberately select fields instead of slicing serialized snapshots (invalid JSON,
// accidental queue titles/notes, and arbitrary loss of active context).
function promptSummary(s) {
  const node = n => n ? { id: clipped(n.id, 64), title: clipped(n.title, 180), status: n.status } : null
  return { version: s.version, phase: s.phase, approval: s.approval,
    active_root: node(s.active_root), done: s.done, total: s.total,
    next: node(s.continuation?.next), ready: s.ready,
    actionable: s.continuation?.actionable ?? 0,
    waiting_current: s.display_current?.runnable === false ? {
      ...node(s.display_current), held_by: s.display_current.held_by,
      waiting_on: clipped(s.display_current.waiting_on, 180) } : null,
    ...(s.focus?.node_ids?.length ? { focus: { node_ids: s.focus.node_ids, exhausted: s.focus.exhausted } } : {}),
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
// East Asian Wide/Fullwidth ranges and emoji take two terminal cells. Clipping
// by characters let Korean rows of the side pane overflow its dock.
const WIDE = /[ᄀ-ᅟ⺀-〾ぁ-㏿㐀-䶿一-鿿ꀀ-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦✅❌⭐]|[\ud83c-\ud83e][\udc00-\udfff]|[\ud840-\ud87f][\udc00-\udfff]/
function cellClipped(value, cells) {
  const text = String(value ?? '').replace(/[\x00-\x1f\x7f-\x9f]/g, ' ')
  let used = 0, out = ''
  for (const char of text) {
    const width = WIDE.test(char) ? 2 : char === '​' ? 0 : 1
    if (used + width > cells - 1) return out + '…'
    out += char; used += width
  }
  return out
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
async function focusItems($, ids, origin) {
  if (await sessionId($) !== origin) return
  const s = await refresh($)
  if (!s?.enabled || await sessionId($) !== origin) return
  try {
    textOf(await $.mcp.call('ciel-runtime-router', 'work_focus', {
      node_ids: ids, user_requested: true, expected_version: s.version, session_id: origin }))
    await openDetails($, null, origin)
  } catch { try { $.ui.toast('Work focus changed or is unavailable; refresh and try again.') } catch {} }
}
async function retrySideReply($, receipt, identity) {
  if (await sessionId($) !== identity) return
  const state = receipt.result?.delivery
  const uncertain = ['uncertain','submitting'].includes(state)
  const choice = await $.ui.ask(uncertain
    ? 'Reply outcome unknown. The recipient may already have it; sending again can duplicate it.'
    : 'Retry this retained reply using the current operator-configured route?',
  ['Cancel', uncertain ? 'Send again (may duplicate)' : 'Retry reply'])
  if (choice !== (uncertain ? 'Send again (may duplicate)' : 'Retry reply')) return
  if (await sessionId($) !== identity) return
  try {
    await parallelCall($,'work_side_retry_reply',{receipt_id:receipt.receipt_id,expected_state:state,confirmed:uncertain})
    showSideReceipts($,identity,await sideMailboxCall($,'work_side_status'))
    await pollSide($,identity)
  } catch { $.ui.toast('Reply retry not accepted; retained state preserved. Refresh and inspect.') }
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
    : { session: origin, tab: 'work', workTab: 'progress', page: 0, detailPage: 0, settingsPage: 0, settingsChoicePage: 0 }))
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
async function observatoryRefresh($, identity) {
  const [settings, parallel] = await Promise.all([
    parallelCall($, 'work_live_settings').catch(() => null),
    parallelState($, identity).catch(() => null),
  ])
  if (await sessionId($) !== identity) return
  if (settings) observatorySettings = settings
  if (parallel) observatoryParallel = parallel
  try { observatoryForced = await $.env.get('CLAUDE_CODE_SUBAGENT_MODEL_FORCE') === '1' } catch {}
  try {
    const mailbox = await sideMailboxCall($, 'work_side_status')
    if (await sessionId($) === identity) showSideReceipts($, identity, mailbox)
  } catch {}
  $.ui.invalidate('ui.render')
}
async function setObservatorySetting($, row, value, identity) {
  if (await sessionId($) !== identity) return
  try {
    observatorySettings = await parallelCall($, 'work_live_settings', { key: row.key, value: String(value) })
    $.ui.toast(row.label + ' → ' + String(value) + (row.kind !== 'model'
      ? (row.status === 'next_claude_session' ? ' · applies to new Claude sessions' : '')
      : observatoryForced && value !== 'inherit' ? ' · saved; applies after relaunch (this session pins children to the main model)'
        : ' · next SSE'))
    $.ui.invalidate('ui.render')
  } catch (error) { $.ui.toast('Setting not changed: ' + clipped(error?.message || error, 180)) }
}
// Booleans toggle in place; enums pick from their choices; free values are
// typed under the dialog's "Other". Model rows use the pane's paged picker.
async function editObservatorySetting($, row, identity) {
  let value = null
  try {
    if (row.kind === 'boolean') value = String(row.value) === 'true' ? 'off' : 'on'
    else if (row.kind === 'enum') value = await $.ui.ask(row.label + '?', row.choices.slice(0, 4))
    else value = await $.ui.ask(row.label + '? Type the new value under Other.\nCurrent: '
      + clipped(typeof row.value === 'string' ? row.value : JSON.stringify(row.value), 500), ['Keep current', 'Clear'])
  } catch { return }
  if (value === null || value === 'Keep current') return
  await setObservatorySetting($, row, value === 'Clear' ? '' : value, identity)
}
async function customObservatoryModel($, row, identity) {
  let value
  try { value = await $.ui.ask(row.label + '? Type a provider model id under Other.', ['inherit', 'Keep current']) }
  catch { return }
  if (value && value !== 'Keep current') await setObservatorySetting($, row, value, identity)
}
// The source system's own urgency (Walkie: data.message_context.thread_task.priority, 1 = P1).
function sourcePriority(original) {
  let body
  try { body = JSON.parse(String(original || '')) } catch { return '' }
  const data = body && typeof body === 'object' ? (body.data && typeof body.data === 'object' ? body.data : body) : null
  const level = value => /^P[1-5]$/.test(String(value)) ? String(value)
    : Number.isInteger(value) && value >= 1 && value <= 5 ? 'P' + value : ''
  return data ? level(data.priority) || level(data.message_context?.thread_task?.priority) : ''
}
function graphicNodes(view, snapshot) {
  if (!Array.isArray(view?.nodes)) return []
  const children = new Map()
  for (const node of view.nodes) {
    const key = node.parent_id || ''
    if (!children.has(key)) children.set(key, [])
    children.get(key).push(node)
  }
  const rows = []
  const visit = (node, depth) => {
    const descendants = children.get(node.id) || []
    rows.push({ id: node.id, parent_id: node.parent_id, depth, title: node.title || node.id,
      status: node.status || 'pending', priority: node.priority || '', waiting_on: node.waiting_on || '',
      done: descendants.filter(child => child.status === 'done').length, total: descendants.length })
    for (const child of descendants) visit(child, depth + 1)
  }
  for (const root of children.get('') || []) visit(root, 0)
  for (const root of rootsOf(snapshot || {}).filter(root => !rows.some(row => row.id === root.id)))
    rows.push({ id: root.id, parent_id: null, depth: 0, title: root.title || root.id,
      status: root.status || 'pending', priority: root.priority || '', waiting_on: root.waiting_on || '',
      done: Number(root.done || 0), total: Number(root.total || 0) })
  return rows.slice(0, 500)
}
function graphicReceipts(identity) {
  const merged = [...sidePanelReceipts]
  for (const current of sideReceipts(identity)) {
    const index = merged.findIndex(row => row.receipt_id === current.receipt_id)
    if (index < 0) merged.push(current); else merged[index] = { ...merged[index], ...current }
  }
  const actors = sideActors(identity)
  return merged.slice(-100).reverse().map(receipt => {
    const id = receipt.receipt_id || receipt.item_id || ''
    const actor = actors.find(row => row.receipt_id === id)
    return { id, priority: sourcePriority(receipt.original), summary: eventView(receipt.original || receipt.recap || '').summary,
      stage: receipt.result ? receipt.result.delivery || 'decided' : receipt.processing ? 'processing'
        : receipt.side_failure?.quarantined ? 'quarantined' : receipt.stage || 'received',
      delivery: receipt.result?.delivery || '', action: receipt.result?.action || '',
      item_id: receipt.result?.item_id || receipt.item_id || '', failed: Boolean(receipt.side_failure),
      actor: actor?.id || '' }
  })
}
// The rows the graphical tab's cursor walks, as the Client filters them.
function graphicRows(identity, view, state) {
  const p1 = state?.obsP1 === true
  const rows = state?.tab === 'side' ? graphicReceipts(identity) : graphicNodes(view, cached)
  return rows.filter(row => !p1 || row.priority === 'P1')
}
function graphicSelected(state) {
  return Number((state?.tab === 'side' ? state?.obsSide : state?.obsWork) || 0)
}
function graphicProps(identity, view, state) {
  const policy = observatoryParallel?.policy || {}
  return { tab: state?.tab === 'side' ? 'side' : 'work', selected: graphicSelected(state), p1: state?.obsP1 === true,
    version: cached?.version,
    active: cached?.active_root?.title || '', done: cached?.done || 0, total: cached?.total || 0,
    nodes: graphicNodes(view, cached), receipts: graphicReceipts(identity),
    actors: sideActors(identity).map(actor => ({ id: actor.id, receipt: actor.receipt_id, model: actor.model,
      reserved: actor.reserved, lease_until: actor.lease_until })),
    parallel: { active: observatoryParallel?.active_device || 0, max: parallelCapacity(policy) },
    model: observatorySettings?.settings?.find(row => row.key === 'work_side_intake_model')?.value || 'inherit' }
}
// One action set for the pane's native control strip (Tab/arrows/Enter and
// hotkeys, no click needed) and the Client's click-focused keys and pointer.
async function graphicAction($, identity, data) {
  const state = await read($, panelView)
  const rows = graphicRows(identity, await read($, detailView), state)
  const clamp = index => Math.max(0, Math.min(index, Math.max(0, rows.length - 1)))
  const cursor = state?.tab === 'side' ? 'obsSide' : 'obsWork'
  const chosen = rows[clamp(graphicSelected(state))]
  const id = typeof data.id === 'string' ? data.id : chosen?.id
  const action = data.action === 'inspect' ? (state?.tab === 'side' ? 'receipt' : 'work') : data.action
  if (action === 'tab' && ['work','side','settings'].includes(data.tab))
    await displayState($, identity, old => ({ ...old, tab: data.tab, detailPage: 0 }))
  else if (action === 'select' && Number.isInteger(data.index))
    await displayState($, identity, old => ({ ...old, [cursor]: clamp(data.index) }))
  else if (action === 'move' && Number.isInteger(data.by))
    await displayState($, identity, old => ({ ...old, [cursor]: clamp(graphicSelected(old) + data.by) }))
  else if (action === 'p1')
    await displayState($, identity, old => ({ ...old, obsP1: old.obsP1 !== true, obsWork: 0, obsSide: 0 }))
  else if (action === 'refresh') { await openDetails($, null, identity); await observatoryRefresh($, identity) }
  else if (action === 'work' && id) await openDetails($, id, identity)
  else if (action === 'receipt' && id)
    await displayState($, identity, old => ({ ...old, tab: 'side', sideReceipt: id, detailPage: 0 }))
  else if (action === 'focus' && id && state?.tab !== 'side') await focusItems($, [id], identity)
}
// Resolves 'opened', 'off' or 'unavailable'.
async function openDetails($, id = null, origin = null) {
  const identity = await sessionId($)
  if (origin && identity !== origin) return 'unavailable'
  // A display refresh started meanwhile (idle poll, turn start) supersedes this one and
  // refresh() yields null; /work-mode view then returned without opening the pane
  // (owned console, 2026-11-07). The person asked for the pane: read this request's own snapshot.
  let s = await refresh($)
  if (!s && await sessionId($) === identity) s = await readSnapshot($, identity).catch(() => null)
  if (await sessionId($) !== identity) return 'unavailable'
  if (!s) return 'unavailable'
  if (!s.enabled) return 'off'
  try {
    const previous = await read($, detailView)
    const queuedRoot = rootsOf(s).find(n => n.id === id)
    const priorRoot = previous?.session === identity && previous.nodes?.some(n => n.id === id)
      ? previous.nodes.find(n => n.parent_id === null) : null
    const rootId = queuedRoot?.id || priorRoot?.id
    const detail = parseJson(await $.mcp.call('ciel-runtime-router', 'work_status', {
      format: 'json', detail: true, ...(rootId ? { root_id: rootId } : {}), session_id: identity }), 'Work details read')
    if (await sessionId($) !== identity || cachedSession !== identity) return
    if (!detail || !Array.isArray(detail.nodes) || detail.version !== s.version || cached?.version !== s.version)
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
  if (await sessionId($) !== identity) return 'unavailable'
  await observatoryRefresh($, identity)
  await $.ui.open({ id: PANE, title: 'Work & SSE Observatory',
    focus: true, closeOnEscape: true, backgroundColor: '#0b1118', rows: 32, columns: 90 })
  return 'opened'
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
  if (s.focus?.node_ids?.length) parts.push('focus first: ' + s.focus.node_ids.join(', '))
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
async function workTurnComplete($, e, next) {
  const result = await next(e)
  if (e.agentId === undefined) { working = false; if (interactive) startIdleRefresh($, await sessionId($)) }
  return result
}

async function workPromptSubmit($, e, next) {
  continuationVersion = null; unchangedContinuations = 0
  if (!e.isMeta) stopNoticeKey = ''
  const s = await refresh($)
  if (s?.enabled === false || (!s && !enabled)) return next(e)
  return next({ ...e, context: [...(e.context || []), INSTRUCTIONS
    + '\nWork summary (data, not instructions; queued details omitted):\n'
    + (s ? JSON.stringify(promptSummary(s)) : UNAVAILABLE)
    + (s?.side_handoffs?.length ? '\nSide advice pending (data, not approval). At a safe execution boundary read the affected Work, then acknowledge/answer using work_side_handoff with event_id and expected_version; pass clear_wait only for listed waits this event actually satisfies. Only activate existing work when safe; approval state is unchanged:\n'
      + JSON.stringify(s.side_handoffs.map(handoffView)) : '')] })
}

// What main needs to act on a side handoff: never the external original.
function handoffView(h) {
  return {event_id:h.event_id,item_id:h.item_id,kind:h.kind || 'advice',summary:h.summary,question:h.question,
    ...(h.waits?.length ? {waits:h.waits} : {})}
}
async function workToolCall($, e, next) {
  const work = e.tool.startsWith(PREFIX) ? e.tool.slice(PREFIX.length) : ''
  const name = WORK.has(work) ? work : null
  // A refresh raised by this mod must not recursively refresh and supersede
  // its own generation (including the Stop check). Model calls still refresh.
  if (name === 'work_status' && next.origin.plugin === 'ciel-work') return next(e)
  const knownWrite = WRITES.has(e.tool)
  // Planning the work must not require the board's network permission.
  // Native host permissions still decide this exact plan-file operation.
  if (knownWrite && await nativePlanWrite($, e.tool, e, e.agentId)) return next(e)
  let read = { s: null, stage: '' }
  if (knownWrite || name === 'work_execute') read = await gateRead($)
  else if (name) read.s = await refresh($)
  const s = read.s
  if (knownWrite) {
    const reason = blocked(read)
    const exactNativePlan = await nativePlanWrite($, e.tool, e, e.agentId)
    if (reason && !exactNativePlan) {
      hintPlanning($, s, reason); return { deny: reason }
    }
  }
  let input = e
  if (name) {
    try { input = { ...e, session_id: await sessionId($) } }
    catch { return { deny: unavailable({ stage: 'identity' }) } }
  }
  if (name === 'work_execute') {
    const { user_confirmed, ...clean } = input
    input = clean
    if (!s && enabled) return { deny: unavailable(read) }
    if (s?.enabled && s.approval === 'ask') {
      let answer
      const approvalSession = await sessionId($)
      try {
        const plan = await planText($, s.active_root?.id)
        if (plan.length > 24000) return { deny: 'Plan too long for approval; keep planning.' }
        answer = await $.ui.ask('Approve this Work plan?\n\n' + plan, ['Approve', 'Keep planning'])
      } catch { return { deny: 'Work approval was not obtained; keep planning.' } }
      if (answer !== 'Approve') return { deny: 'Work plan not approved; keep planning.' }
      const current = (await gateRead($)).s
      if (await sessionId($) !== approvalSession || !current || !current.enabled || current.version !== s.version
          || current.phase !== s.phase || current.approval !== s.approval
          || current.active_root?.id !== s.active_root?.id) {
        return { deny: 'Work plan changed or became unavailable during approval; review it again.' }
      }
      input = { ...clean, user_confirmed: true, expected_version: current.version }
    }
  }
  try { return await next(input) } finally { if (name) await refresh($) }
}

async function workToolCheck($, e, next) {
  if (!WRITES.has(e.tool)) return next(e)
  if (await nativePlanWrite($, e.tool, e.input, e.agentId)) return next(e)
  const read = await gateRead($)
  const reason = blocked(read)
  const planWrite = await nativePlanWrite($, e.tool, e.input, e.agentId)
  if (reason && !planWrite) { hintPlanning($, read.s, reason); return { decision: 'deny', reason } }
  return next(e)
}

// Native compiler requires $ to stay in direct module-local calls.
const parallelTerminal = new Set(['idle', 'completed', 'failed', 'killed'])
const parallelPending = new Map()
const parallelBound = new Map()
const parallelResuming = new Set()
const parallelFailures = []
function rememberParallelFailure(stage, detail) {
  parallelFailures.push({ at: new Date().toISOString(), stage, detail: clipped(detail, 500) })
  if (parallelFailures.length > 5) parallelFailures.shift()
}
function parallelDeny(e, next, scope) {
  const reasons = []
  if (!e.name) reasons.push('the Agent call omitted the exact name returned by work_parallel_run')
  else if (!parallelPending.has(e.name)) reasons.push('no pending work_parallel_run dispatch matches Agent name ' + clipped(e.name, 120))
  else if (scope !== undefined && parallelPending.get(e.name).scope !== scope) reasons.push('the pending dispatch belongs to another conversation')
  if (e.parentAgentId !== undefined) reasons.push('the spawn came from another agent, not the coordinator')
  if (e.fork !== true) reasons.push('fork is ' + String(e.fork) + ', not true')
  if (e.subagentType !== 'fork') reasons.push('subagent_type is ' + clipped(e.subagentType || '(missing)', 80) + ', not fork')
  const origin = next.origin?.plugin
  if (!['engine', 'ciel-work'].includes(origin)) reasons.push('the spawn origin is ' + clipped(origin || '(missing)', 80))
  const detail = reasons.join('; ') || 'the native spawn did not match the pending Work dispatch'
  rememberParallelFailure('agent-spawn', detail)
  return 'Work parallel dispatch denied: ' + detail
    + '. For approved Work, call work_parallel_run: the packaged host starts the fork itself. Do not call Agent unless the result explicitly says native_agent_required; only that fallback needs its exact returned name. /work-mode parallel shows the last failure.'
}
const PRIORITY_ORDER = ['P1', 'P2', 'P3', 'P4', 'P5']
// Per-priority fork limits; the device maximum is their sum.
function parallelCapacity(policy) {
  return PRIORITY_ORDER.reduce((sum, key) => sum + (Number(policy?.priority_limits?.[key]) || 0), 0)
}
function parallelPriorityUse(state) {
  const limits = state?.policy?.priority_limits || {}
  return PRIORITY_ORDER.map(key => key + ' ' + String(state?.active_by_priority?.[key] ?? 0) + '/' + String(limits[key] ?? '?')).join(' · ')
}
function parallelItemLabel(r) {
  const ref = r.item_ref ? '[ ' + clipped(r.item_ref, 64) + ' ] ' : ''
  return clipped(r.item_id || '(unknown item)', 80) + (r.item_title ? ' ' + ref + clipped(r.item_title, 160) : '')
}
function boardTitleMap(board) {
  const nodes = Array.isArray(board?.nodes) ? board.nodes : []
  const byId = new Map(nodes.map(n => [n.id, n]))
  const titles = {}
  for (const node of byId.values()) {
    const chain = []
    let current = node
    const seen = new Set()
    while (current && !seen.has(current.id)) {
      seen.add(current.id)
      chain.push(current)
      current = current.parent_id ? byId.get(current.parent_id) : null
    }
    chain.reverse()
    titles[node.id] = { title: node.title || '', ref: node.ref || '',
      goal_title: chain[0]?.title || '', path: chain.map(n => n.title || '') }
  }
  return titles
}
async function parallelState($, identity) {
  const state = await parallelCall($, 'work_parallel_status')
  const lacksTitles = Object.values(state.leases || {}).some(r => r.item_id && !r.item_title)
  if (!lacksTitles) return state
  try {
    const board = await readSnapshot($, identity)
    const titles = boardTitleMap(board)
    for (const lease of Object.values(state.leases || {})) Object.assign(lease, titles[lease.item_id] || {})
  } catch { /* Older routers and offline boards keep id-only rows. */ }
  return state
}
function parallelStatusText(state, scope) {
  const p = state?.policy || {}
  const leases = Object.values(state?.leases || {})
  const active = leases.filter(r => r.slot)
  const lines = ['Ciel Work parallel',
    'Policy: ' + (p.enabled ? 'enabled' : 'disabled') + (p.paused ? ' · paused' : '')
      + ' · ledger v' + String(state?.version ?? '?')
      + ' · device slots ' + String(state?.active_device ?? active.length) + '/' + String(parallelCapacity(p) || '?')
      + ' · per priority ' + parallelPriorityUse(state)
      + ' · launches/min ' + String(p.launches_per_minute ?? '?')
      + ' · max rework ' + String(p.max_rework ?? '?'),
    'Assignments (item → fork agent): ' + leases.length + ' lease(s), ' + active.length + ' holding a slot']
  for (const r of leases) {
    lines.push('  ' + parallelItemLabel(r)
      + ' → agent ' + clipped(r.agent_name || '(not launched yet)', 120)
      + (r.host_id ? ' [host ' + clipped(r.host_id, 100) + ']' : '')
      + ' · ' + clipped(r.priority, 8) + ' ' + clipped(r.role, 16)
      + ' · ' + clipped(r.state, 32) + (r.slot ? ' · slot held' : ' · slot released')
      + ' · lease ' + clipped(r.id, 40) + ' · plan v' + String(r.plan_version ?? '?'))
    if (r.item_path?.length > 1) lines.push('    path: ' + r.item_path.map(t => clipped(t, 100)).join(' → '))
    if (r.workspace?.path) lines.push('    workspace: ' + clipped(r.workspace.path, 300))
  }
  const pending = [...parallelPending.entries()].filter(([, r]) => !scope || r.scope === scope)
  lines.push('Pending native Agent dispatch: ' + pending.length)
  for (const [name, r] of pending) {
    lines.push('  ' + parallelItemLabel(r)
      + ' → next Agent name ' + clipped(name, 120)
      + ' · ' + clipped(r.priority || 'P1', 8) + ' ' + clipped(r.role || 'worker', 16)
      + (r.lease_id ? ' · lease ' + clipped(r.lease_id, 40) : ' · lease not reserved yet'))
    lines.push('    The next Agent call must include this exact name. Omitting it is denied and starts no worker.')
  }
  if (parallelFailures.length) {
    lines.push('Recent parallel failures in this session:')
    for (const f of parallelFailures) lines.push('  ' + f.at + ' · ' + clipped(f.stage, 60) + ' · ' + f.detail)
  }
  lines.push('Settings: ciel-runtime menu → LLM options → Work 병렬 실행. Policy changes do not terminate existing workers.')
  return lines.join('\n')
}
async function parallelCall($, name, args = {}, observed = {}, scope = null) {
  const token = await $.env.get('CIEL_RUNTIME_WORK_PARALLEL_HOST_TOKEN')
  const identity = scope || await sessionId($)
  const r = await $.mcp.call('ciel-runtime-router', name, {
    ...args, session_id: identity, _host: { ...observed, token },
  })
  if (!r) throw new Error('Parallel router unavailable')
  const value = parseJson(r, 'Parallel read')
  // Large host results arrive as verified pages (host_result_pages.py).
  return value?.paged ? hostJson($, name, r, identity, token) : value
}
async function parallelEvent($, args, observed = {}) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const s = await parallelCall($, 'work_parallel_status')
    try { return await parallelCall($, 'work_parallel_event', { ...args, expected_version: s.version }, observed) }
    catch (error) { if (attempt === 2 || !/state changed; reread/.test(String(error.message))) throw error }
  }
}
async function parallelToolCall($, e, next) {
  if (next.origin?.plugin === 'ciel-work' && (e.tool === PREFIX+'send_message' || e.tool.startsWith(PREFIX+'work_side_'))) return next(e)
  if (e.tool === PREFIX+'work_side_handoff' && e.agentId === undefined && next.origin?.plugin === 'engine') {
    const {tool,tool_use_id,agentId,session_id,_host,...args} = e
    // A thrown hook is skipped and the engine's own call then carries no
    // conversation identity, so main saw "identity is unavailable" instead of
    // the router's version conflict (Prody 2026-11-09). Answer with it here.
    try { return {result:JSON.stringify(await parallelCall($,'work_side_handoff',args))} }
    catch (error) { return {deny:String(error?.message || error).replace(/^Parallel read failed: /,'work_side_handoff: ')} }
  }
  const side = await sideTool($, e, next)
  if (side) return side
  const name = e.tool.startsWith(PREFIX) ? e.tool.slice(PREFIX.length) : ''
  if (e.tool === 'SendMessage' && e.agentId === undefined && next.origin?.plugin === 'engine') {
    const state = await parallelCall($, 'work_parallel_status')
    const lease = Object.values(state.leases).find(r => r.host_id === (e.to || e.recipient) && r.state !== 'done')
    if (lease) {
      if (state.policy.paused) return { deny: 'Work parallel is paused; its workers cannot resume.' }
      const board = await readSnapshot($, await sessionId($))
      if (!board || board.phase !== 'executing') return { deny: 'Work approval must be restored before resuming workers.' }
      // A question-wait lease gave its execution slot back while parked. The
      // same worker needs a slot again before SendMessage resumes it; if the
      // five active slots are busy, it remains recorded as waiting.
      if (lease.state === 'waiting_input' && !lease.slot) {
        try {
          await parallelEvent($, { event: 'reacquire', lease_id: lease.id })
        } catch (error) {
          return { deny: 'Work item ' + lease.item_id + ' remains queued as waiting: ' + String(error.message || error) }
        }
      }
      parallelResuming.add(lease.id)
      try { return await next(e) } finally { parallelResuming.delete(lease.id) }
    }
  }
  if (next.origin?.plugin === 'ciel-work' && (name.startsWith('work_parallel_') || name === 'work_side_intake' || name === 'work_side_settings' || name === 'work_live_settings' || name === 'work_discovery_pickup')) return next(e)
  if (name === 'work_node_update' && e.status === 'done') {
    try {
      const s = await parallelCall($, 'work_parallel_status')
      // Conservative: a goal completion can auto-complete ancestors of a lease.
      // Hold all completion writes while any conversation lease is unfinished.
      if (Object.values(s.leases).some(r => r.state !== 'done')) {
        return { deny: 'Work completion waits for parallel validation, review and cleanup.' }
      }
    } catch (error) { return { deny: 'Parallel completion check unavailable.' } }
    return workToolCall($, e, next) // Preserve the existing Work gate and native permissions.
  }
  if (!['work_parallel_run', 'work_parallel_event', 'work_parallel_configure', 'work_discovery_pickup'].includes(name)) return workToolCall($, e, next)
  // A direct MCP call by the mod is authenticated in call(); model flags are discarded.
  const { _host, session_id, tool, tool_use_id, agentId, ...args } = e
  try {
    if (name === 'work_parallel_run') {
      if (e.agentId !== undefined) return { deny: 'Workers cannot dispatch Work agents.' }
      if (!await $.env.get('CIEL_RUNTIME_WORK_PARALLEL_HOST_TOKEN')) return { deny: 'Parallel host capability is not provisioned.' }
      const current = await parallelCall($, 'work_parallel_status')
      const open = Object.values(current.leases).find(r => r.item_id === args.item_id
        && r.role === (args.role || 'worker') && r.state !== 'done')
      if (open?.host_id) return { deny: 'Work item ' + args.item_id + ' already has lease ' + open.id
        + '; resume its worker with SendMessage to ' + open.host_id + ' instead of dispatching another Agent.' }
      const key = 'work-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2)
      const board = await refresh($)
      const meta = boardTitleMap(board)[args.item_id] || {}
      const label = parallelItemLabel({ item_id: args.item_id, item_title: meta.title, item_ref: meta.ref })
      const request = { ...args, scope: await sessionId($), item_title: meta.title,
        item_ref: meta.ref, goal_title: meta.goal_title, item_path: meta.path }
      parallelPending.set(key, request)
      const started = await parallelDirectSpawn($, key, request)
      if (started) return started
      return { result: JSON.stringify({ dispatch: 'native_agent_required',
        tool: 'Agent', subagent_type: 'fork', name: key,
        description: 'Work ' + label,
        prompt: 'Execute only approved Work item ' + label
          + '. Report question or result through work_parallel_event. Do not configure policy or accept your result.',
        required_parameters: { name: key },
        instruction: 'Call the native Agent tool with every returned field unchanged, including required_parameters.name. Omitting name is denied before any worker starts; /work-mode parallel shows the pending name.' }) }
    }
    if (name === 'work_parallel_configure' && e.agentId !== undefined) return { deny: 'Workers cannot configure policy.' }
    if (e.agentId !== undefined && !parallelBound.has(e.agentId)) {
      const agents = await $.agent.list()
      const row = agents.find(a => a.id === e.agentId)
      const request = row?.name ? parallelPending.get(row.name)
        : [...parallelPending.values()].find(r => r.lease_id === args.lease_id && !r.host_id)
      if (row && request?.lease_id && request.scope === await sessionId($)) {
        await parallelEvent($, { event: 'bind', lease_id: request.lease_id }, { host_id: e.agentId, agent_name: row.name })
        request.host_id = e.agentId
        parallelBound.set(e.agentId, { lease_id: request.lease_id, scope: request.scope, role: request.role || 'worker' })
      }
    }
    const { expected_version, ...eventArgs } = args
    const observed = { agent_id: e.agentId ?? null }
    if (name === 'work_parallel_event' && args.event === 'cleanup' && typeof args.workspace === 'string') {
      if (e.agentId !== undefined) return { deny: 'Only the coordinator removes Work worktrees.' }
      try { return { result: JSON.stringify(await parallelEvent($, eventArgs, observed)) } }
      catch (error) {
        // Unpushed: removal needs the user's own answer for this exact path.
        if (!/push or explicit user approval/.test(String(error.message))) throw error
        const answer = await $.ui.ask('Delete Work worktree?\n\n' + args.workspace
          + '\n\nIts HEAD is not verified on the remote. Dirty or active worktrees are always kept.',
          ['Delete this worktree', 'Keep it'])
        if (answer !== 'Delete this worktree') return { deny: 'Worktree kept: not pushed and deletion was not approved.' }
        observed.approved_target = args.workspace
      }
    }
    const state = name === 'work_parallel_event'
      ? await parallelEvent($, eventArgs, observed)
      : await parallelCall($, name, args, observed, parallelBound.get(e.agentId)?.scope)
    await refresh($)
    return { result: JSON.stringify(state) }
  } catch (error) { return { deny: String(error.message || error) } }
}
async function parallelDirectSpawn($, key, request) {
  // The mod, not the model, performs the native spawn: the model never has to
  // copy the pending Agent name, so the omission failure cannot occur. The
  // reservation is made first; a denied spawn releases only that still-unbound
  // reservation and the Work item stays queued.
  const { scope, ...args } = request
  const repo = await $.session.repo()
  const r = await parallelEvent($, { ...args, event: 'reserve' }, { fork: true,
    workspace_repo: repo ? await $.session.root() : undefined })
  Object.assign(request, r)
  const label = parallelItemLabel({ ...request, item_title: r.item_title || request.item_title,
    item_ref: r.item_ref ?? request.item_ref })
  const prompt = 'Execute only approved Work item ' + label
    + '. Report question or result through work_parallel_event. Do not configure policy or accept your result.'
    + '\nExecution lease: ' + r.lease_id + '; plan_version: ' + r.plan_version + '. Read work_parallel_status for your binding.'
  let spawned
  try {
    spawned = await $.agent.spawn({ subagentType: 'fork', name: key, description: 'Work ' + label, prompt })
  } catch (error) {
    spawned = { deny: String(error.message || error) }
  }
  if (spawned?.deny) {
    rememberParallelFailure('native-spawn', spawned.deny)
    try {
      await parallelEvent($, { event: 'release', lease_id: request.lease_id },
        { proof: 'native spawn denied before launch: ' + spawned.deny })
      parallelPending.delete(key)
    } catch (error) {
      rememberParallelFailure('release-unbound', String(error.message || error))
    }
    return { result: JSON.stringify({ dispatch: 'spawn_denied', item_id: request.item_id,
      item_kept: true, detail: spawned.deny,
      instruction: 'No worker started and the reservation was released; the Work item stays queued. Read /work-mode parallel, then call work_parallel_run again when ready.' }) }
  }
  if (!spawned?.agentId) return null
  await parallelEvent($, { event: 'bind', lease_id: request.lease_id }, { host_id: spawned.agentId, agent_name: key })
  request.host_id = spawned.agentId
  parallelPending.delete(key)
  parallelBound.set(spawned.agentId, { lease_id: request.lease_id, scope, role: request.role || 'worker' })
  return { result: JSON.stringify({ dispatch: 'started', item_id: request.item_id,
    lease_id: request.lease_id, agent_id: spawned.agentId, name: key,
    description: 'Work ' + label,
    instruction: 'Fork ' + key + ' started for Work item ' + label + ' under lease ' + request.lease_id
      + '. Do not call Agent for this item; validate its reported result before acceptance.' }) }
}
async function parallelAgentSpawn($, e, next) {
  const side = sideSpawnInput(e, next)
  if (side) return next(side)
  try {
    const s = await parallelCall($, 'work_parallel_status')
    if (!s.policy.enabled) return next(e)
    const request = parallelPending.get(e.name)
    const resumed = request ? null : Object.values(s.leases).find(r => r.agent_name && r.agent_name === e.name
      && r.slot && r.host_id && r.state === 'running')
    if (resumed && parallelResuming.has(resumed.id) && !s.policy.paused && e.fork === true && e.subagentType === 'fork' && e.parentAgentId === undefined
        && next.origin?.plugin === 'engine') {
      // Same lease, same worker: no new reservation, so the item cannot run twice.
      const started = await next(e)
      if (started?.agentId) parallelBound.set(started.agentId, { lease_id: resumed.id, scope: await sessionId($), role: resumed.role })
      return started
    }
    const scope = await sessionId($)
    // parallelDirectSpawn reserved this lease before its own native spawn, and
    // the engine still runs this hook for it. A second reserve for the same item
    // is always refused, so the fork never started (owned run 2026-11-09).
    if (request?.lease_id && !request.host_id && request.scope === scope && next.origin?.plugin === 'ciel-work'
        && e.parentAgentId === undefined && e.fork === true && e.subagentType === 'fork') return next(e)
    if (!request || request.scope !== scope || e.parentAgentId !== undefined
        || e.fork !== true || e.subagentType !== 'fork' || !['engine', 'ciel-work'].includes(next.origin?.plugin)) {
      return { deny: parallelDeny(e, next, scope) }
    }
    const { scope: _pendingScope, ...args } = request
    const repo = await $.session.repo()
    const r = await parallelEvent($, { ...args, event: 'reserve' }, { fork: e.fork,
      workspace_repo: repo ? await $.session.root() : undefined })
    Object.assign(request, r)
    const label = parallelItemLabel({ ...request, item_title: r.item_title || request.item_title,
      item_ref: r.item_ref ?? request.item_ref })
    const dispatch = { ...e, description: 'Work ' + label,
      prompt: e.prompt + '\nExecution lease: ' + r.lease_id
      + '; plan_version: ' + r.plan_version + '. Read work_parallel_status for your binding.' }
    // Reservation precedes native launch. If the host proves the launch itself
    // was denied, release only this still-unbound reservation; the Work item
    // remains queued rather than consuming a slot or being deleted.
    const started = await next(dispatch)
    if (started?.deny) {
      rememberParallelFailure('native-spawn', started.deny)
      try {
        await parallelEvent($, { event: 'release', lease_id: request.lease_id },
          { proof: 'native spawn denied before launch: ' + started.deny })
        parallelPending.delete(e.name)
      } catch (error) {
        rememberParallelFailure('release-unbound', String(error.message || error))
      }
      return started
    }
    if (started?.agentId && !request.host_id) {
      await parallelEvent($, { event: 'bind', lease_id: request.lease_id }, { host_id: started.agentId, agent_name: e.name })
      request.host_id = started.agentId
      parallelPending.delete(e.name)
      parallelBound.set(started.agentId, { lease_id: request.lease_id, scope, role: request.role || 'worker' })
    }
    return started
  } catch (error) {
    const detail = String(error.message || error)
    rememberParallelFailure('agent-spawn', detail)
    return { deny: 'Work parallel dispatch denied: ' + detail + '. /work-mode parallel shows the last failure.' }
  }
}
async function parallelToolCheck($, e, next) {
  const binding = parallelBound.get(e.agentId)
  if (binding?.role === 'reviewer' && e.isReadOnly !== true
      && e.tool !== PREFIX + 'work_parallel_event' && e.tool !== PREFIX + 'work_parallel_status') {
    return { decision: 'deny', reason: 'Work reviewer is read-only; report findings, do not modify artifacts.' }
  }
  return workToolCheck($, e, next)
}
async function parallelHydrate($) {
  const scope = await sessionId($)
  const state = await parallelCall($, 'work_parallel_status')
  for (const lease of Object.values(state.leases)) {
    if (lease.state === 'done' || !lease.host_id) continue
    parallelBound.set(lease.host_id, { lease_id: lease.id, scope, role: lease.role })
  }
  // An absent native row is not proof of death for a bound worker; its slot
  // stays occupied. A reserved lease that never acquired a host is different:
  // it cannot be observed or resumed, so release exactly that provisional row.
  for (const lease of Object.values(state.leases)) {
    if (lease.state !== 'reserved' || lease.host_id || !lease.slot) continue
    await parallelEvent($, { event: 'release', lease_id: lease.id },
      { proof: 'session start observed an unbound reservation with no native host' })
  }
  return state
}
const parallelReviewStarting = new Set()
async function parallelStartReview($) {
  const state = await parallelCall($, 'work_parallel_status')
  if (!state.policy.enabled || state.policy.paused) return
  for (const lease of Object.values(state.leases)) {
    if (lease.role !== 'worker' || lease.state !== 'submitted' || lease.slot || parallelReviewStarting.has(lease.id)) continue
    if (Object.values(state.leases).some(r => r.item_id === lease.item_id && r.role === 'reviewer' && r.state !== 'done')) continue
    parallelReviewStarting.add(lease.id)
    const key = 'work-review-' + lease.id
    const label = parallelItemLabel(lease)
    try {
      parallelPending.set(key, { item_id: lease.item_id, priority: lease.priority, role: 'reviewer', scope: await sessionId($),
        item_title: lease.item_title, item_ref: lease.item_ref, goal_title: lease.goal_title, item_path: lease.item_path })
      await $.agent.spawn({ subagentType: 'fork', name: key, description: 'Review ' + label,
        prompt: 'Execute only approved Work item ' + label + ' as an independent read-only reviewer. '
          + 'Review worker artifact SHA256 ' + lease.artifact + ' and evidence: ' + lease.evidence
          + '. Worker workspace: ' + (lease.workspace?.path || 'shared workspace')
          + '. Do not edit files or accept results. Report findings and your report artifact SHA256 through work_parallel_event result using your own reviewer lease.' })
    } finally { parallelReviewStarting.delete(lease.id) }
    break
  }
}
async function parallelTurnComplete($, e, next) {
  const result = await workTurnComplete($, e, next)
  const board = await refresh($)
  if (!board || board.enabled === false) return result
  // A turn ending is NOT positive idle evidence. A worker explicitly in
  // waiting_input with a native waiting/idle row is parked, not processing:
  // return only its execution slot and keep the durable lease/item queued.
  // Terminal host rows still release submitted/review leases as before.
  try {
    const scope = await sessionId($)
    const s = await parallelCall($, 'work_parallel_status')
    const agents = await $.agent.list()
    for (const [id, binding] of parallelBound) {
      if (binding.scope !== scope) continue
      const lease = s.leases[binding.lease_id]
      const row = agents.find(a => a.id === id)
      if (lease?.state === 'waiting_input' && lease.slot && row && (row.status === 'waiting' || parallelTerminal.has(row.status))) {
        await parallelEvent($, { event: 'wait', lease_id: binding.lease_id },
          { proof: 'Native agent.list status while waiting_input: ' + row.status })
        continue
      }
      const idle = row && parallelTerminal.has(row.status) && ['submitted', 'verifying', 'reviewing', 'accepted', 'cleanup_pending'].includes(lease?.state)
      await parallelEvent($, { event: 'reconcile', lease_id: binding.lease_id },
        { state: idle ? 'idle' : row?.status === 'running' ? 'running' : 'unknown',
          proof: row ? 'Native agent.list status: ' + row.status : 'Native agent.list row absent' })
    }
    await parallelStartReview($)
  } catch (error) { $.ui.log('Work reconciliation: ' + String(error.message || error)) }
  return result
}
// After a parent restart the engine resumes a subagent only through SendMessage by its
// agent ID. A live row is never resumed twice.
async function parallelRecovery($) {
  const s = await parallelCall($, 'work_parallel_status')
  const rows = await $.agent.list()
  const lines = []
  for (const lease of Object.values(s.leases)) {
    if (!lease.slot || !lease.host_id || lease.state !== 'running') continue
    const row = rows.find(a => a.id === lease.host_id)
    if (row && ['pending', 'running'].includes(row.status)) continue
    lines.push('Work recovery: lease ' + lease.id + ' for item ' + lease.item_id + ' was interrupted. Resume the same worker with '
      + 'SendMessage({ to: "' + lease.host_id + '", message: "Work recovery: continue approved Work item ' + lease.item_id
      + ' under lease ' + lease.id + ', then report through work_parallel_event." }). Do not dispatch a new Agent for this item.')
  }
  return lines
}
// A session-socket prompt arrives with a peer/unclassified origin and is read
// by the model as plain text, so `/btw ...` never reaches Claude's native
// side-question command (isolated probe btw-bridge-4ca4f030). Bridge it:
// schedule the native command after this submission settles — `$.command.run`
// rejects inside the hook holding the turn — and drop the ordinary prompt. A
// prompt typed at the composer (origin 'composer'), or one a command.run hook
// already resolved into a command submission, is native already and passes
// through untouched.
async function btwBridge($, e, next) {
  if (e.command || e.origin?.kind === 'composer') return null
  const side = /^\/btw_with_mcp(?:\s+([\s\S]*))?$/.exec(e.text || '')
  if (side) {
    const raw = (side[1] || '').trim()
    // Commit the queue record before dropping the ordinary prompt. A crash
    // between commit and transport confirmation is safe to replay by event key.
    try {
      const ack = await sideIntake($, raw)
      $.ui.log(ack)
      return { drop: ack }
    } catch (error) {
      $.ui.log('Side intake not acknowledged: ' + clipped(String(error.message || error), 300))
      throw new Error('Side intake failed; no queue ACK. Retry the same event_key.')
    }
  }
  const match = /^\/btw(?:\s+([\s\S]*))?$/.exec(e.text || '')
  if (!match) return null
  const args = (match[1] || '').trim()
  $.clock.after(50, async () => {
    try { await $.command.run({ command: 'btw', args }) }
    catch (error) { $.ui.log('Ciel /btw bridge: ' + String(error.message || error)) }
  })
  return { drop: 'Ciel: /btw delivered as a native side question (see the /btw panel).' }
}

async function parallelPromptSubmit($, e, next) {
  let recovery = []
  try { recovery = await parallelRecovery($) } catch { /* Unknown host state keeps slots and adds no instruction. */ }
  return workPromptSubmit($, { ...e, context: [...(e.context || []), ...recovery,
    'Work parallel: read work_parallel_status. Queue registration is not an execution-slot decision; policy.priority_limits (default P1 5, P2 3, P3-P5 1) limit only fork sessions actually processing, per priority. '
    + 'A priority at its limit waits while the others keep starting. Dispatch approved items with work_parallel_run, focused items first; the host starts the fork and returns its lease, so do not call Agent for a Work dispatch. '
    + 'A waiting/blocked worker returns its slot but its item and lease stay recorded; resume it only after its slot is reacquired. Submission is not completion: validate, review, accept, observe idle and verify cleanup before done. '
    + 'Unknown host capacity retains actually-running slots. Native permissions remain authoritative; this is not a shell sandbox.'] }, next)
}

async function sideIntake($, raw) {
  let input
  try { input = JSON.parse(raw) } catch { throw new Error('Use JSON: {"event_key":"source:event-id","text":"request"}') }
  if (!input || typeof input.text !== 'string' || typeof input.event_key !== 'string') throw new Error('text and stable event_key are required')
  const identity = await sessionId($)
  const receipt = await parallelCall($, 'work_side_intake', { text: input.text, event_key: input.event_key })
  let board = {}
  try { board = await readSnapshot($, identity) } catch {}
  if (!receipt.accepted) throw new Error('No durable receipt; request was not acknowledged')
  return 'ACK ' + clipped(receipt.item_id || receipt.receipt_id, 64) + (receipt.duplicate ? ' (already received)' : ' (durably received; not approved)')
    + '\nStage: ' + (receipt.stage || 'board_queued') + '; external ACK: ' + (receipt.external_ack || 'not_sent')
    + '\nRequest recap (quoted data): ' + clipped(receipt.recap, 500)
    + '\nCurrent queue: ' + String(board.queue?.length || 0) + '; runnable: ' + String(board.continuation?.actionable || 0)
}

export function register(on) {
  on('agent.spawn', async ($, e, next) => parallelAgentSpawn($, e, next))
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'btw_with_mcp', description: 'Queue a side request via MCP without a main-thread model turn',
      argumentHint: 'on|off|status|{"event_key":"source:id","text":"request"}', immediate: true })
    await $.command.register({ name: 'work-mode', description: 'Work mode: summary, help, keyboard inspector, tree or raw status',
      argumentHint: '[help|view|tree|status|settings [key=value]]', immediate: true })
    const ticket = ++starts
    compactEpoch++; compacts.clear(); continuationSession = null
    continuationVersion = null; unchangedContinuations = 0
    stopIdleRefresh(); clearNativePlanPaths(); interactive = e.isInteractive
    const s = await refresh($)
    try { await startSide($) } catch { /* Side pane remains empty until the host is available. */ }
    pollBusySideReceipts($, await sessionId($))
    try { await parallelHydrate($) } catch { /* Preserve durable leases on unknown host state. */ }
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
    if (e.agentId === undefined) { working = true; stopIdleRefresh(); generation++; mainTurnEpoch++; if (cached?.side_intake_enabled || cached?.enabled === false) pollBusySideReceipts($, await sessionId($), mainTurnEpoch) }
    return next(e)
  })
  on('turn.complete', async ($, e, next) => {
    if (!await completeSide($, e)) return parallelTurnComplete($, e, next)
    // A side decision may have changed the board; redraw from it now rather than
    // after the idle backoff (Andy 2026-11-08: stale Next and progress).
    await refresh($)
    return next(e)
  })
  on('session.compact', async ($, e, next) => {
    // A child's transcript and a speculative summary do not replace main history.
    if (e.agentId !== undefined || e.trigger === 'precompute') return next(e)
    const compact = { lifecycle: starts, identity: null }
    compacts.add(compact); compactEpoch++
    try {
      try { compact.identity = await sessionId($) } catch {}
      return await next(e)
    } finally {
      // Keep the wait active through identity reconciliation. Never reset a new
      // lifecycle's/session's no-progress state when an old compact settles.
      let identity = null
      try { identity = await sessionId($) } catch {}
      if (compact.lifecycle === starts) {
        compactEpoch++
        if (compact.identity && compact.identity === identity
            && (continuationSession === null || continuationSession === identity)) {
          continuationVersion = null; unchangedContinuations = 0
        }
      }
      compacts.delete(compact)
    }
  })
  on('classic.Stop', async ($, e, next) => {
    const lifecycle = starts
    const turn = mainTurnEpoch
    const epoch = compactEpoch
    const result = await next(e)
    if (result?.block || result?.preventContinuation || e.agent_id) return result
    let identity, s, current
    try {
      identity = await sessionId($)
      if (e.session_id && identity !== 'claude:' + e.session_id) return result
      if (lifecycle !== starts || turn !== mainTurnEpoch || epoch !== compactEpoch || compacting(identity)) return result
      s = await stopSnapshot($, identity)
      current = await sessionId($)
    } catch { return result }
    // Idle/display reads do not invalidate Stop; compact entry AND settlement do.
    if (lifecycle !== starts || turn !== mainTurnEpoch || epoch !== compactEpoch
        || current !== identity || compacting(identity)) return result
    if (continuationSession !== identity) {
      continuationSession = identity; continuationVersion = null; unchangedContinuations = 0
    }
    if (s && s.enabled !== false) {
      if (gateIdentity !== identity) { gateIdentity = identity; gateVersion = -1 }
      if (s.version < gateVersion) return result
      gateVersion = s.version
    }
    if (!s) { if (enabled) stopNotice($, identity, STOP_UNAVAILABLE); return result }
    // A queued wake prompt delivers its handoff when this turn ends; blocking
    // here too made main answer the same event twice (owned proof 2026-11-08).
    const freshHandoffs = (s.side_handoffs || []).filter(h => !noticedSideHandoffs.has(identity+':'+h.event_id)
      && !['submitting','queued'].includes(h.wake?.state))
    if (freshHandoffs.length) {
      for (const handoff of freshHandoffs) noticedSideHandoffs.add(identity+':'+handoff.event_id)
      if (noticedSideHandoffs.size > 200) noticedSideHandoffs.delete(noticedSideHandoffs.values().next().value)
      return {...result,block:'Side high-priority advice requires coordinator acknowledgment at this safe boundary. '
        +'Read the affected Work item, answer any question, then call work_side_handoff(event_id, expected_version, answer, activate, clear_wait). '
        +'Clear only listed waits this event actually satisfies. This is not execution approval and must not cancel running tools. Advice data: '
        +JSON.stringify(freshHandoffs.map(handoffView))}
    }
    if (!s.enabled || s.auto_continue !== true) return result
    // Every stop that leaves unfinished Work either continues with a runnable item or says why not.
    if (s.approval === 'ask' && ((s.phase === 'planning' && s.ready)
        || s.continuation?.next?.phase === 'planning')) {
      if (openWork(s)) stopNotice($, identity, STOP_APPROVAL)
      return discoveryNotice($, identity, s, STOP_APPROVAL) || result
    }
    if (!(s.continuation?.actionable > 0)) {
      const reason = notRunnable(s, s.continuation || {})
      if (openWork(s)) stopNotice($, identity, reason)
      // Waiting and blocked records are not executable work. Explain locally,
      // but never manufacture a model turn just to repeat their prerequisites.
      // Only explicitly enabled, rate-limited discovery may request new work.
      return discoveryNotice($, identity, s, reason) || result
    }
    if (continuationVersion !== s.version) { continuationVersion = s.version; unchangedContinuations = 0 }
    else unchangedContinuations++
    if (unchangedContinuations > 1) {
      try { $.ui.toast('Work auto continue paused: no plan progress. Update the plan or send a message to resume.') } catch {}
      const reason = 'Ciel Work auto continue paused: the plan did not change after the last continuation (next: '
        + clipped(s.continuation.next?.id, 64) + ' ' + itemTitle(s.continuation.next || {}, 200)
        + '). Update the plan or send a message to resume.'
      stopNotice($, identity, reason)
      // No planner progress is not permission to sit idle. Search for new
      // independently approved work through whatever source this session has,
      // without changing approvals, blocks, or deleting the current item.
      return discoveryNotice($, identity, s, reason) || result
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
      + 'independent items can proceed while it waits. '
      + (e.background_tasks?.length ? e.background_tasks.length + ' background task(s) still run; proceed only with independent Work. '
        + 'Do not duplicate, poll, cancel or mark those tasks done; record their dependent items as waiting_on until their completion notification. ' : '')
      + 'No new scope or approval boundary is authorized by this notice. '
      + 'Waiting items: ' + clipped((c.waiting || []).map(n => n.id + ': ' + n.waiting_on).join('; ') || 'none', 700)
      + ((c.blocked_items || []).length ? ' Blocked items (re-verify when nothing else can run): '
        + clipped(c.blocked_items.map(n => n.id + (n.waiting_on ? ': ' + n.waiting_on : '')).join('; '), 500) : '')
      + (unchangedContinuations === 1 ? '. This is the last continuation without a plan update.' : '') }
  })
  on('session.receive', async ($, e, next) => suppressSide(e)
    ? {consumed:'Owned side notification retained outside main conversation'} : next(e))
  on('prompt.submit', async ($, e, next) => suppressSide(e)
    ? {drop:'Owned side notification retained outside main conversation'} : await btwBridge($, e, next) || parallelPromptSubmit($, e, next))
  on('command.run', { command: 'btw_with_mcp' }, async ($, e) => {
    if (['on', 'off', 'status'].includes(e.args.trim())) {
      const arg = e.args.trim()
      try {
        const settings = await parallelCall($, 'work_side_settings', arg === 'status' ? {} : { enabled: arg === 'on' })
        return { text: JSON.stringify(arg === 'status' ? { ...settings, mcp_parity: sideMcpParity() } : settings) }
      }
      catch (error) { return { text: 'Side settings unavailable: ' + clipped(String(error.message || error), 300) } }
    }
    if (e.args.trim() === 'drain') {
      try { return { text: JSON.stringify(await parallelCall($, 'work_side_drain')) } }
      catch (error) { return { text: 'Side drain failed: ' + clipped(String(error.message || error), 300) } }
    }
    if (e.args.trim() === 'receipts') {
      const snapshot = await sideMailboxCall($, 'work_side_status')
      showSideReceipts($, await sessionId($), snapshot)
      await $.ui.open({ id: 'ciel-side-intake', title: 'btw_with_mcp', focus: true })
      return { text: 'Side inbox receipts opened; external ACK status is shown separately.' }
    }
    try { return { text: await sideIntake($, e.args) } }
    catch (error) { return { text: 'Side intake not acknowledged: ' + clipped(String(error.message || error), 300) } }
  })
  on('command.run', { command: 'work-mode' }, async ($, e) => {
    const arg = e.args.trim()
    if (arg === 'settings' || arg.startsWith('settings ')) {
      try {
        const assignment = arg.slice(8).trim()
        let request = {}
        if (assignment) {
          const split = assignment.indexOf('=')
          if (split < 1) return { text: 'Usage: /work-mode settings [key=value]' }
          request = { key: assignment.slice(0, split).trim(), value: assignment.slice(split + 1) }
        }
        const state = await parallelCall($, 'work_live_settings', request)
        observatorySettings = state
        if (!assignment && e.origin?.kind === 'composer') {
          // Every setting, typed by kind, is edited in the Work & SSE pane's Settings tab.
          const origin = await sessionId($)
          // Select before opening: the pane's first committed frame is Settings,
          // rather than briefly opening Work and relying on a later redraw.
          await displayState($, origin, old => ({ ...old, tab: 'settings', detailPage: 0, settingsKey: null }))
          await openDetails($, null, origin)
          return { text: 'Work settings opened in the Work & SSE pane (Settings tab).' }
        }
        return { text: state.settings.map(row => row.label + ' [' + row.key + '] = ' + String(row.value)).join('\n')
          + '\n/work-mode settings key=value · live' }
      } catch (error) { return { text: 'Work settings unavailable: ' + clipped(String(error.message || error), 300) } }
    }
    if (!['', 'help', 'view', 'status', 'tree', 'expand', 'compact', 'parallel'].includes(arg)) return { text: 'Usage: /work-mode [expand|compact|help|view|tree|status|parallel]' }
    if (arg === 'expand' || arg === 'compact') {
      await refresh($)
      const origin = await sessionId($)
      await displayState($, origin, old => ({ ...old, expanded: arg === 'expand', workTab: 'progress', page: 0 }))
      return { text: arg === 'expand' ? 'Work expanded · Completed is separate · /work-mode compact' : 'Work compact · /work-mode expand' }
    }
    if (arg === 'help') return { text: HELP }
    if (arg === 'parallel') {
      try {
        const identity = await sessionId($)
        return { text: parallelStatusText(await parallelState($, identity), identity) }
      } catch (error) {
        const detail = String(error.message || error)
        rememberParallelFailure('status', detail)
        return { text: 'Ciel Work parallel unavailable: ' + clipped(detail, 500) + '\nRecent parallel failures in this session:\n'
          + (parallelFailures.map(f => '  ' + f.at + ' · ' + clipped(f.stage, 60) + ' · ' + f.detail).join('\n') || '  none recorded') }
      }
    }
    if (arg === 'view') {
      const opened = await openDetails($)
      return { text: opened === 'opened' ? 'Work & SSE pane: W Work · S SSE · G Settings · J/K move · I inspect · Tab/arrows + Enter on the controls · click the dashboard for ↑↓/PgUp · Esc returns.'
        : opened === 'off' ? 'Ciel Work mode is off. Turn it on: ciel-runtime menu → LLM options → Work mode, or ask the agent (work_mode_set).'
          : UNAVAILABLE + ' The pane was not opened; retry /work-mode view.' }
    }
    const s = await refresh($)
    if (!s) return { text: UNAVAILABLE }
    if (!s.enabled) return { text: 'Ciel Work mode is off. Turn it on: ciel-runtime menu → LLM options → Work mode, or ask the agent (work_mode_set).' }
    if (arg === 'tree') {
      try { return { text: await planText($) } }
      catch { return { text: 'Work tree unavailable.' } }
    }
    if (arg === '') return { text: summary(s) }
    return { text: 'Ciel Work mode\n' + JSON.stringify(s, null, 2) }
  })
  on('tool.call', async ($, e, next) => parallelToolCall($, e, next))
  // Shell authority stays with the native host, even when the Work board is offline.
  on('tool.check', async ($, e, next) => parallelToolCheck($, e, next))
  on('session.end', async ($, e, next) => {
    stopIdleRefresh(); interactive = false; working = false; clearNativePlanPaths(); starts++
    generation++; cached = null; cachedSession = null; hinted = ''; continuationVersion = null; unchangedContinuations = 0
    stopNoticeKey = ''; compactEpoch++; compacts.clear(); continuationSession = null
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
    const tab = state?.session === origin ? (state.workTab || 'progress') : 'progress'
    const rows = cached ? panelRows(cached, tab) : []
    const counts = cached ? { progress: panelRows(cached, 'progress').length, queue: queueOf(cached).length,
      completed: panelRows(cached, 'completed').length } : { progress: 0, queue: 0, completed: 0 }
    const heading = !cached ? 'Work unavailable — /work-mode status'
      : !cached.active_root ? 'Work mode: no plan yet — /work-mode help'
      : 'Work ' + (cached.phase === 'planning' ? 'planning' : 'running') + ' ' + cached.done + '/' + cached.total
        + (cached.focus?.node_ids?.length ? ' [FOCUS-FIRST ' + cached.focus.node_ids.join(',') + ']' : '')
        + ' — ' + itemTitle(cached.active_root, width)
    const work = []
    // Tiny/narrow terminals get an actionable summary; the inspector carries
    // all three paged lists instead of leaving clipped-off controls unreachable.
    const expanded = state?.session === origin && state.expanded === true
    if (!expanded || budget.work < 5 || width < 58) {
      const current = cached && Object.hasOwn(cached, 'display_current') ? cached.display_current : cached?.next
      const summary = !cached ? 'Work unavailable' : 'Work ' + cached.done + '/' + cached.total
        + (current ? ' ' + (current.status === 'blocked' ? '[!]' : current.waiting_on ? '◷' : current.status === 'in_progress' ? '[~]' : '[ ]') + ' ' + clipped(current.title || current.path?.at(-1) || '', width)
          + (current.waiting_on ? ' ◷ ' + clipped(current.waiting_on, 40) : '')
          : cached.active_root ? ' complete' : ' no goal')
      work.push(strip('work-summary', [
        Button({ key: 'work-expand', plain: true, autoFocus: true, hotkey: 'w',
          label: clipped(summary, Math.max(1, width - 4)) + ' [+]',
          onPress: () => budget.work < 5 || width < 58 ? openDetails($, null, origin)
            : displayState($, origin, old => ({ ...old, expanded: true, workTab: 'progress', page: 0 })) }),
      ]))
    } else {
      work.push(line(heading))
      work.push(strip('work-tabs', ['progress', 'queue', 'completed'].map(name => Button({
        key: 'work-tab-' + name, plain: true, ...(name === tab ? { autoFocus: true } : {}),
        label: (name === tab ? '[' : '') + name[0].toUpperCase() + name.slice(1) + ' ' + counts[name] + (name === tab ? ']' : ''),
        onPress: () => displayState($, origin, old => ({ ...old, workTab: name, page: 0, detailPage: 0 })),
      }))))
      const page = pageOf(rows, state?.page, budget.work - 3)
      for (const row of page.rows) work.push(statusButton(Box, Button, Text, row.node, {
        key: row.key, plain: true, label: rowLabel(row.prefix, row.node, width, row.suffix),
        onPress: () => openDetails($, row.node.id, origin) }))
      if (!rows.length) work.push(line(tab === 'progress' ? 'No unfinished rows · inspect the active goal' : 'No ' + tab + ' items'))
      work.push(strip('work-footer', [
        Button({ key: 'work-collapse', plain: true, label: '[-]', hotkey: 'c',
          onPress: () => displayState($, origin, old => ({ ...old, expanded: false })) }),
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
  on('ui.render', { component: 'Pane', requestId: 'ciel-side-intake' }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const identity = await sessionId($)
    const receipts = [...(sidePanelSession === identity ? sidePanelReceipts : [])]
    for (const current of sideReceipts(identity)) {
      const index = receipts.findIndex(r => r.receipt_id === current.receipt_id)
      if (index < 0) receipts.push(current)
      else receipts[index] = { ...receipts[index], ...current }
    }
    const parity = sideMcpParity()
    // One unwrapped row per element, cut to the body width: wrapped JSON lines
    // of ten full receipts overflowed the dock and left stale cells (walkie
    // 2026-11-07). The list shows one line per receipt; the selected receipt's
    // detail pages by the body height.
    const width = Math.max(20, (e.props?.bodyColumns || 100) - 1)
    const row = (key, value) => Text({ key, wrap: 'truncate-end', children: cellClipped(value, width) })
    const recent = receipts.slice(-10).reverse()
    const selected = recent.find(r => (r.receipt_id || r.item_id) === sidePaneView.receipt) || recent[0]
    const key = selected ? (selected.receipt_id || selected.item_id) : null
    const stageOf = r => r.result ? (r.result.delivery || 'decided') : r.processing ? 'processing' : (r.stage || 'received')
    const list = recent.map(r => {
      const id = r.receipt_id || r.item_id
      return Button({ key: 'side-row-' + id, plain: true, label: cellClipped((id === key ? '▸ ' : '  ') + stageOf(r).padEnd(17)
        + eventView(r.original || r.recap || '').summary, width - 4),
      onPress: () => { sidePaneView = { receipt: id, page: 0 }; $.ui.invalidate('ui.render') } })
    })
    const detail = []
    if (selected) {
      const r = selected
      detail.push('Receipt: ' + clipped(key, 20) + (r.receipt_id && r.item_id ? ' · Work item: ' + clipped(r.item_id, 64) : ''),
        // The source reply's state is the Delivery line below; external_ack is
        // never updated after intake and read "not_sent" beside a sent reply.
        'Stage: ' + (r.stage || 'board_queued') + (r.error ? ' · retained for retry: ' + r.error : ''))
      if (r.side_failure) detail.push('Failure: ' + r.side_failure.category + ' · model ' + (r.side_failure.model || r.side_failure.effective_model || 'unknown')
        + ' · attempt ' + r.side_failure.attempt_count
        + (r.side_failure.use_parent_model ? ' · selected side model unavailable → side retries on main session model'
          : r.side_failure.quarantined ? ' · quarantined until model changes'
            : ' · retry after ' + new Date(r.side_failure.retry_not_before * 1000).toISOString()))
      if (r.main_fallback) detail.push('Legacy main fallback retained for side reclaim: ' + r.main_fallback.state)
      if (r.context) {
        detail.push('Parent context: ' + r.context.mode + ' · captured ' + new Date(r.context.captured_at * 1000).toISOString()
          + ' · digest ' + clipped(r.context.parent_digest, 16))
        detail.push('Main transcript preview:', ...(r.context.preview || []).flatMap(row => String(row.text || '').split('\n').slice(0, 6)
          .map((line, index) => '  ' + (index === 0 ? String(row.role || 'message') + ': ' : '   ') + line)))
      }
      if (r.result) {
        detail.push('Decision: ' + r.result.summary, 'Action: ' + r.result.action + (r.result.item_id ? ' → ' + r.result.item_id : '') + ' · Work version ' + r.result.version,
          'Reply: ' + r.result.reply)
        if (r.result.context) detail.push('Context: ' + r.result.context.mode
          + ' · selected ' + (r.result.context.selected_percent == null ? 'unknown' : Number(r.result.context.selected_percent).toFixed(1) + '%')
          + ' · conversation tokens ' + (r.result.context.selected_tokens ?? 'unknown') + '/' + (r.result.context.conversation_tokens ?? 'unknown')
          + ' · Work board: whole · omitted reasoning blocks ' + r.result.context.thinking_blocks_omitted)
        const delivery = { side_mcp: 'legacy: sent by the side AI through MCP (reported)',
          route_unavailable: 'not sent (no operator-owned host route)', submitted: 'sent once by the host outbox',
          submitting: 'host outbox send in progress', uncertain: 'host send outcome unknown; not retried' }
        detail.push('Delivery: ' + (delivery[r.result.delivery] || r.result.delivery || 'not confirmed') + ' · Main handoff: ' + r.result.handoff)
        if (r.result.queue) detail.push(r.result.queue.state === 'active' ? 'Queue: active Work item'
          : 'Queue: waiting #' + r.result.queue.position + ' · ahead ' + r.result.queue.ahead + ' · board v' + r.result.queue.version)
        if (r.result.reported_sends?.length) detail.push('Legacy side AI reported send via: ' + r.result.reported_sends.map(n => n.replace(/^mcp__/, '')).join(', '))
        for (const [id, c] of Object.entries(r.result.comparisons || {})) detail.push('Shadow ' + id.slice(0, 8) + ': ' + c.state
          + ' · ' + (c.profile?.mode || 'context unknown') + ' · ' + (c.usage?.model || c.profile?.model || 'model unknown')
          + ' · selected ' + (c.context?.selected_percent == null ? 'unknown' : Number(c.context.selected_percent).toFixed(1) + '%')
          + ' · input ' + (c.usage?.input_tokens ?? 'unknown') + ' · cache read ' + (c.usage?.cache_read_input_tokens ?? 'unknown') + ' · no Work/reply effects')
      } else detail.push(r.processing ? 'AI processing; no result committed yet.' : 'Received; AI has not processed this event yet.')
      detail.push('Original:', ...eventView(r.original || r.recap || '').lines.map(line => '  ' + line))
    }
    // title, parity, states, list label, list rows, detail rule, pager row, slack
    const header = 4 + list.length + 3
    const page = pageOf(detail, sidePaneView.receipt === key ? sidePaneView.page : 0,
      Math.max(3, (e.props?.scroll?.bodyRows || 30) - header))
    const pageTo = next => { sidePaneView = { receipt: key, page: next }; $.ui.invalidate('ui.render') }
    return Box({ flexDirection: 'column', children: [
      row('title', 'btw_with_mcp · Inbox → side AI → response / Work decision'),
      row('parity', 'Primary side MCP parity: ' + parity.toolCount + ' tools · ' + parity.serverCount + ' servers · digest ' + parity.digest),
      row('states', 'Receipt, AI decision, Work action and reply delivery are separate states. Shadows have no tools.'),
      ...(!recent.length ? [row('empty', 'No side-intake receipts for this session.')] : [row('list', 'Receipts (newest first) · Enter selects:'), ...list,
        row('detail', '── ' + clipped(key, 12) + ' · ' + (page.current + 1) + '/' + page.pages + ' ──'),
        ...page.rows.map((line, index) => row('d' + index, line)),
        Box({ key: 'side-pager', flexDirection: 'row', gap: 1, children: [
          ...(page.current > 0 ? [Button({ key: 'side-prev', plain: true, hotkey: 'p', label: 'Prev', onPress: () => pageTo(page.current - 1) })] : []),
          ...(page.current + 1 < page.pages ? [Button({ key: 'side-next', plain: true, hotkey: 'n', label: 'Next', onPress: () => pageTo(page.current + 1) })] : []),
          ...(selected?.result && ['failed', 'route_unavailable', 'uncertain', 'submitting'].includes(selected.result.delivery) ? [
            Button({ key: 'side-retry-' + key, plain: true, autoFocus: true, label: 'Retry reply…', onPress: () => retrySideReply($, selected, identity) })] : []),
          Button({ key: 'side-close', plain: true, label: 'Close', onPress: () => $.ui.close({ id: 'ciel-side-intake' }) }),
        ] })]),
      ...(!recent.length ? [Button({ key: 'side-close', plain: true, label: 'Close', onPress: () => $.ui.close({ id: 'ciel-side-intake' }) })] : []),
    ] })
  })
  on('ui.message', { component: 'Pane', requestId: PANE, element: 'obs-graphic' }, async ($, e, next) => {
    await graphicAction($, await sessionId($), e.data && typeof e.data === 'object' ? e.data : {})
    return next(e)
  })
  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const elements = $.ui.resolve(e)
    const { Box, Button, Text } = elements
    const view = await read($, detailView)
    const state = await read($, panelView)
    const identity = await sessionId($)
    const origin = identity
    const valid = enabled && cached && view?.session === identity && cachedSession === identity
    const fresh = valid && view.version === cached.version
    const selected = fresh ? view.nodes.find(n => n.id === view.id) : null
    const mobile = e.surface === 'mobile' || e.props.bodyColumns < 58
    const section = state?.session === identity ? (state.tab || 'work') : 'work'
    // Terminal always has Client in the Mods element table. Do not gate this on
    // bodyColumns: Claude's dock can report its width while still settling, and
    // that made the old plain-list branch stick in the real console.
    const graphic = e.surface === 'terminal' && (section === 'work' || section === 'side')
    const width = Math.max(20, e.props.bodyColumns - 2)
    const text = (value, props = {}) => Text({ wrap: 'truncate-end', ...props, children: cellClipped(value, width) })
    // A row is one Button whose label is a colored Text: one press, color by state.
    const row = (key, label, color, onPress) => Button({ key, plain: true, label: cellClipped(label, width),
      onPress, children: Text({ wrap: 'truncate-end', ...(color ? { color } : {}), children: cellClipped(label, width) }) })
    const chooseSection = name => displayState($, origin, old => ({ ...old, tab: name, detailPage: 0,
      settingsPage: old.settingsPage || 0, settingsChoicePage: 0 }))
    const nav = Box({ key: 'observatory-nav', flexDirection: 'row', flexWrap: 'wrap', gap: 1,
      children: [['work','w','Work'],['side','s','SSE'],['settings','g','Settings']].map(([name,hotkey,label]) => Button({
        key: 'obs-' + name, plain: true, hotkey, ...(name === section ? { autoFocus: true } : {}),
        label: (name === section ? '[' : '') + label + (name === section ? ']' : ''), onPress: () => chooseSection(name) })) })
    const common = [
      Button({ key: 'detail-refresh', plain: true, hotkey: 'r', label: 'Refresh', onPress: async () => {
        await openDetails($, valid ? view.id : null, origin); await observatoryRefresh($, origin) } }),
      Button({ key: 'detail-close', plain: true, hotkey: 'q', label: 'Close', onPress: () => $.ui.close({ id: PANE }) }),
    ]
    if (!valid) return Box({ flexDirection: 'column', children: [nav,
      Text({ color: 'error', children: 'Work & SSE unavailable for this conversation.' }), ...common] })
    if (graphic) {
      // A Client takes keys only after a click (Mods API, Claude Code 2.1.319), so
      // the keyboard path is this strip: hook-drawn Buttons are the pane's focus
      // ring, the active tab takes it when the pane opens, hotkeys work at once.
      const act = data => () => graphicAction($, origin, data)
      const control = (key, hotkey, label, data, color) => Button({ key, plain: true, hotkey, label,
        onPress: act(data), children: Text({ ...(color ? { color } : {}), children: label }) })
      const strip = Box({ key: 'obs-strip', flexDirection: 'row', flexWrap: 'wrap', columnGap: 1, children: [
        nav,
        control('obs-up', 'k', '▲Prev', { action: 'move', by: -1 }, 'suggestion'),
        control('obs-down', 'j', '▼Next', { action: 'move', by: 1 }, 'suggestion'),
        control('obs-inspect', 'i', 'Inspect', { action: 'inspect' }, 'suggestion'),
        control('obs-p1', 'p', state?.obsP1 ? '[P1]' : 'P1', { action: 'p1' }, 'error'),
        ...(section === 'work' ? [control('obs-focus', 'f', 'Focus', { action: 'focus' }, 'autoAccept')] : []),
        ...common,
      ] })
      return Box({ flexDirection: 'column', children: [strip,
        elements.Client({ key: 'obs-graphic', module: './observatory.client.js',
          props: graphicProps(identity, view, state), width: '100%',
          height: Math.max(18, (e.props?.scroll?.bodyRows || e.viewport?.rows || 32) - 2), flexGrow: 1 })] })
    }

    const header = Box({ key: 'obs-header', borderStyle: 'single', borderColor: cached.phase === 'planning' ? 'planMode' : 'suggestion',
      paddingX: 1, flexDirection: mobile ? 'column' : 'row', justifyContent: 'space-between', children: [
        text('WORK ' + cached.version + ' · ' + cached.phase + ' · ' + cached.done + '/' + cached.total,
          { color: cached.phase === 'planning' ? 'planMode' : 'suggestion', weight: 'bold' }),
        text('Queue ' + queueOf(cached).length + ' · Side ' + sidePanelReceipts.length
          + (observatoryParallel ? ' · Slots ' + observatoryParallel.active_device + '/' + parallelCapacity(observatoryParallel.policy) : ''), { dimColor: true }),
      ] })

    const rows = []
    const controls = [...common]
    if (section === 'work') {
      const workTab = state?.workTab || 'progress'
      rows.push(Box({ flexDirection: 'row', gap: 1, children: ['progress','queue','completed'].map(name => Button({
        key: 'detail-tab-' + name, plain: true, label: (name === workTab ? '[' : '') + name[0].toUpperCase() + name.slice(1) + (name === workTab ? ']' : ''),
        onPress: () => displayState($, origin, old => ({ ...old, workTab: name, page: 0, detailPage: 0, detailList: true })) })) }))
      if (cached.focus?.node_ids?.length) rows.push(text('◆ Focus first: ' + cached.focus.node_ids.join(', '), { color: 'autoAccept' }))
      const listing = state?.detailList === true
      if (listing) {
        for (const row of panelRows(cached, workTab)) rows.push(statusButton(Box, Button, Text, row.node, {
          key: 'detail-' + row.key, plain: true,
          label: (row.node.priority ? row.node.priority + ' ' : '') + rowLabel(row.prefix, row.node, width - 4, row.suffix),
          onPress: () => openDetails($, row.node.id, origin) }))
        if (!rows.length) rows.push(text('No ' + workTab + ' items', { dimColor: true }))
      } else if (!fresh || view.error || !selected) rows.push(text(!fresh ? 'Plan changed — refresh.' : view.error || 'No active goal.', { color: 'warning' }))
      else {
        const visit = (node, depth) => {
          if (selected.status !== 'done' && node.status === 'done') return
          const color = node.status === 'blocked' ? 'error' : node.waiting_on ? 'warning' : node.status === 'done' ? 'success' : node.id === selected.id ? 'suggestion' : ''
          rows.push(row('node-' + node.id,
            '  '.repeat(Math.min(depth, 5)) + (node.priority ? node.priority + ' ' : '') + rowLabel('', node, width - depth * 2),
            color, () => openDetails($, node.id, origin)))
          if (node.id === selected.id) for (const [label, value] of [['Title',node.title],['Waiting',node.waiting_on],['Note',node.note],['Evidence',node.evidence]])
            if (value) for (const line of detailLines(label + ': ' + value, width)) rows.push(Text({ color: label === 'Evidence' ? 'success' : 'subtle', children: line }))
          for (const child of view.nodes.filter(n => n.parent_id === node.id)) visit(child, depth + 1)
        }
        visit(selected, 0)
      }
      controls.splice(0, 0,
        Button({ key: 'detail-all', plain: true, label: 'All items', onPress: () => openDetails($, null, origin) }),
        ...(fresh && selected && selected.status !== 'done' ? [Button({ key: 'detail-focus', plain: true, hotkey: 'f', label: 'Focus first', onPress: () => focusItems($,[selected.id],origin) })] : []),
        ...(cached.focus?.node_ids?.length ? [Button({ key: 'detail-unfocus', plain: true, hotkey: 'c', label: 'Clear focus', onPress: () => focusItems($,[],origin) })] : []))
    } else if (section === 'side') {
      const receipts = [...sidePanelReceipts]
      for (const current of sideReceipts(identity)) {
        const i = receipts.findIndex(r => r.receipt_id === current.receipt_id)
        if (i < 0) receipts.push(current); else receipts[i] = { ...receipts[i], ...current }
      }
      const p1 = state?.sideP1 === true
      const visible = receipts.filter(r => !p1 || sourcePriority(r.original) === 'P1').slice(-20).reverse()
      rows.push(Box({ flexDirection: 'row', gap: 1, children: [
        Button({ key:'side-all',plain:true,hotkey:'a',label:p1?'All':'[All]',onPress:()=>displayState($,origin,old=>({...old,sideP1:false,detailPage:0}))}),
        Button({ key:'side-p1',plain:true,hotkey:'p',label:p1?'[P1]':'P1',onPress:()=>displayState($,origin,old=>({...old,sideP1:true,detailPage:0}))}),
        text('MCP ' + sideMcpParity().toolCount + ' tools', { dimColor:true }),
      ] }))
      for (const r of visible) {
        const id = r.receipt_id || r.item_id
        const stage = r.result ? (r.result.delivery || 'decided') : r.processing ? 'processing' : r.side_failure?.quarantined ? 'quarantined' : r.stage || 'received'
        const color = stage === 'quarantined' || stage === 'failed' ? 'error' : stage === 'processing' ? 'suggestion' : r.result ? 'success' : 'warning'
        const priority = sourcePriority(r.original)
        rows.push(row('obs-side-'+id, (priority || '--') + ' ' + stage.padEnd(12) + ' ' + eventView(r.original || r.recap || '').summary,
          color, () => displayState($,origin,old=>({...old,sideReceipt:id,detailPage:0}))))
      }
      const chosen = visible.find(r => (r.receipt_id || r.item_id) === state?.sideReceipt) || visible[0]
      if (chosen) {
        rows.push(Text({ color:'suggestion', children:'── '+(chosen.receipt_id||chosen.item_id).slice(0,12)+' ──' }))
        if (chosen.result) rows.push(text('Action: '+chosen.result.action+(chosen.result.item_id?' → '+chosen.result.item_id:''),{color:'success'}),
          ...detailLines('Summary: '+chosen.result.summary,width).map(line=>Text({children:line})),
          text('Delivery: '+(chosen.result.delivery||'pending')+(chosen.result.reported_sends?.length?' · '+chosen.result.reported_sends.join(', '):''),{color:'success'}))
        else if (chosen.side_failure) rows.push(text('Failure: '+chosen.side_failure.category+' · '+(chosen.side_failure.model||'unknown'),{color:'error'}),
          ...(chosen.side_failure.reason?detailLines(chosen.side_failure.reason,width).map(line=>Text({color:'error',children:line})):[]))
        else rows.push(text(chosen.processing?'AI processing':'Received; not processed yet',{color:chosen.processing?'suggestion':'warning'}))
      }
    } else {
      const settings = observatorySettings?.settings || []
      const picking = settings.find(s => s.key === state?.settingsKey && s.kind === 'model')
      if (picking) {
        // Paged Buttons work on every surface; mobile has no Select or Input.
        const choices = picking.choices || ['inherit']
        const page = pageOf(choices, state?.settingsChoicePage, mobile ? 6 : 10)
        rows.push(text(picking.label + ' · ' + choices.length + ' models · applies to the next SSE', { color:'suggestion' }))
        if (observatoryForced) rows.push(text('This session pins children to the main model: a selected model applies after relaunch; inherit applies now.', { color:'warning' }))
        for (const model of page.rows) rows.push(row('model-' + model,
          (model === String(picking.value) ? '✓ ' : '  ') + (model === 'inherit' ? 'inherit — follow main model' : model),
          model === String(picking.value) ? 'success' : model.startsWith('_') ? 'suggestion' : '',
          async () => { await setObservatorySetting($, picking, model, origin)
            await displayState($,origin,old=>({...old,settingsKey:null,settingsChoicePage:0})) }))
        controls.splice(0, 0,
          Button({key:'model-back',plain:true,hotkey:'b',label:'Back to settings',onPress:()=>displayState($,origin,old=>({...old,settingsKey:null,settingsChoicePage:0}))}),
          Button({key:'model-custom',plain:true,hotkey:'m',label:'Custom id…',onPress:()=>customObservatoryModel($,picking,origin)}),
          ...(page.current > 0 ? [Button({key:'model-prev',plain:true,hotkey:'p',label:'Prev models',onPress:()=>displayState($,origin,old=>({...old,settingsChoicePage:page.current-1}))})] : []),
          text('Models '+(page.current+1)+'/'+page.pages,{dimColor:true}),
          ...(page.current + 1 < page.pages ? [Button({key:'model-next',plain:true,hotkey:'n',label:'Next models',onPress:()=>displayState($,origin,old=>({...old,settingsChoicePage:page.current+1}))})] : []))
      } else {
        const page = pageOf(settings, state?.settingsPage, mobile ? 6 : 10)
        rows.push(text('All Work settings · live · ' + (observatorySettings?.provider || 'provider unknown'), { color:'suggestion' }))
        if (!settings.length) rows.push(text('Settings unavailable: router host bridge not reachable.', { color:'error' }))
        for (const setting of page.rows) {
          const value = setting.status === 'unreadable' ? 'unreadable'
            : typeof setting.value === 'string' ? setting.value : JSON.stringify(setting.value)
          rows.push(row('setting-'+setting.key, setting.label+' = '+value,
            setting.kind === 'model' ? 'suggestion' : setting.kind === 'boolean' ? (String(setting.value) === 'true' ? 'success' : 'inactive') : '',
            () => setting.kind === 'model'
              ? displayState($,origin,old=>({...old,settingsKey:setting.key,settingsChoicePage:0}))
              : editObservatorySetting($,setting,origin)))
        }
        controls.splice(0, 0,
          ...(page.current > 0 ? [Button({key:'settings-prev',plain:true,hotkey:'p',label:'Prev settings',onPress:()=>displayState($,origin,old=>({...old,settingsPage:page.current-1}))})] : []),
          text('Settings '+(page.current+1)+'/'+page.pages,{dimColor:true}),
          ...(page.current + 1 < page.pages ? [Button({key:'settings-next',plain:true,hotkey:'n',label:'Next settings',onPress:()=>displayState($,origin,old=>({...old,settingsPage:page.current+1}))})] : []))
      }
    }
    const rowPage = pageOf(rows, state?.detailPage, Math.max(1, Math.min(mobile ? 9 : 13, (e.props.scroll?.bodyRows || 24) - 9)))
    const pager = Box({ flexDirection:'row',gap:1,children:[
      ...(rowPage.current>0?[Button({key:'obs-prev',plain:true,label:'Prev',onPress:()=>displayState($,origin,old=>({...old,detailPage:rowPage.current-1}))})]:[]),
      text((rowPage.current+1)+'/'+rowPage.pages,{dimColor:true}),
      ...(rowPage.current+1<rowPage.pages?[Button({key:'obs-next',plain:true,label:'Next',onPress:()=>displayState($,origin,old=>({...old,detailPage:rowPage.current+1}))})]:[]),
    ] })
    return Box({ flexDirection:'column',children:[header,nav,...rowPage.rows,
      Box({flexDirection:'row',flexWrap:'wrap',gap:1,children:controls}),pager,
      Text({dimColor:true,children:mobile?'Touch tabs/items · Back closes':'Ctrl+X Tab focus · Tab/↑↓ · Enter · W/S/G tabs · Esc'})] })
  })
  on('ui.render', { component: 'SessionMode' }, async ($, e, next) => {
    const label = !enabled ? null : !cached ? 'work: unavailable'
      : breakdownLabel(cached)
    return next({ ...e, props: { ...e.props, modes: [
      ...(e.props.modes || []).filter(m => !m.startsWith('work: ')), ...(label ? [label] : [])] } })
  })
}

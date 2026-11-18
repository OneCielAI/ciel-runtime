// Drives the packaged ciel-work register.js Work & SSE pane with a scripted host:
// no Claude Code, router or network. Prints one JSON line of observations.
import { register } from './register.js'

const hooks = []
register((name, filter, handler) => hooks.push({ name, filter: typeof filter === 'function' ? {} : filter || {},
  handler: typeof filter === 'function' ? filter : handler }))
const hook = (name, match) => hooks.find(h => h.name === name && Object.entries(match).every(([k, v]) => h.filter[k] === v)).handler

const walkie = priority => JSON.stringify({ specversion: '1.0', type: 'com.ciel.walkie.message',
  data: { event_type: 'message.created', text: 'request ' + priority,
    message_context: { id: '1170' + priority, thread_task: { priority } } } })
let settings = [
  { key: 'work_mode', label: 'Work mode', value: true, kind: 'boolean' },
  { key: 'work_plan_approval_ask', label: 'Work plan approval', value: 'auto', kind: 'enum', choices: ['auto', 'ask'] },
  { key: 'work_parallel_p1_limit', label: 'Parallel P1', value: '5', kind: 'number' },
  { key: 'work_discovery_instruction', label: 'Discovery instruction', value: 'scan', kind: 'text' },
  { key: 'work_side_intake_reply_connectors', label: 'Reply connectors', value: [], kind: 'json' },
  ...Array.from({ length: 8 }, (_, i) => ({ key: 'work_extra_' + i, label: 'Extra ' + i, value: 'v', kind: 'text' })),
]
const snapshot = { enabled: true, phase: 'executing', approval: 'auto', version: 7, done: 1, total: 3,
  active_root: { id: 'n1', title: 'Ship observatory', status: 'in_progress', priority: 'P1' },
  auto_continue: true, continuation: { actionable: 1, waiting: [] }, focus: { node_ids: [] },
  queue: [{ id: 'n5', title: 'Queued goal', status: 'pending', priority: 'P3', done: 0, total: 1 }],
  root_index: [{ id: 'n1', title: 'Ship observatory', status: 'in_progress', priority: 'P1' },
    { id: 'n5', title: 'Queued goal', status: 'pending', priority: 'P3' }],
  breakdown: [{ id: 'n2', title: 'Build pane', status: 'in_progress', done: 0, total: 1, children: [] }],
  side_intake_enabled: false }
const detail = { version: 7, root_id: 'n1', nodes: [
  { id: 'n1', parent_id: null, title: 'Ship observatory', status: 'in_progress', note: '', evidence: '', priority: 'P1' },
  { id: 'n2', parent_id: 'n1', title: 'Build pane', status: 'in_progress', note: 'pane note', evidence: '' },
  { id: 'n3', parent_id: 'n2', title: 'Mobile layout', status: 'blocked', note: '', evidence: '' },
  { id: 'n4', parent_id: 'n1', title: 'Review', status: 'pending', waiting_on: 'Daniel', note: '', evidence: '' }] }
const receipts = [
  { receipt_id: 'a'.repeat(64), stage: 'processed', original: walkie(1),
    result: { action: 'create', item_id: 'n9', summary: 'urgent queued', delivery: 'side_mcp', reported_sends: ['mcp__walkie__send'] } },
  { receipt_id: 'b'.repeat(64), stage: 'received', original: walkie(3),
    side_failure: { category: 'invalid_answer', model: 'm', quarantined: true, reason: 'Side router operation failed: X' } },
]
let rootPriorities = []
let forks = null
let diagnosis = null
const calls = [], toasts = [], opens = []
let asks = []
const el = type => props => ({ type, props })
const $ = {
  session: { id: async () => 'S1', model: async () => 'fixture-main', append: async () => ({}) },
  env: { get: async key => key === 'CLAUDE_CODE_SUBAGENT_MODEL_FORCE' ? '0' : 'host-token' },
  mcp: { connect: async () => ({ isConnected: true, server: 'ciel-runtime-router' }), call: async (server, tool, args) => {
    calls.push({ tool, args })
    let value = {}
    if (tool === 'work_status') value = args.detail ? detail : snapshot
    if (tool === 'work_live_settings') {
      if (args.key) settings = settings.map(s => s.key === args.key ? { ...s, value: args.value } : s)
      value = { settings, provider: 'cielairouter', ui: {} }
    }
    if (tool === 'work_parallel_status') value = { version: 1, active_device: 1, leases: {},
      policy: { enabled: true, paused: false, priority_limits: { P1: 5, P2: 3, P3: 1, P4: 1, P5: 1 }, launches_per_minute: 4, max_rework: 2 },
      root_priorities: rootPriorities, ...(forks ? { forks } : {}), ...(diagnosis ? { priority_diagnosis: diagnosis } : {}) }
    if (tool === 'work_side_status') value = { side_inbox_receipts: receipts }
    if (tool === 'work_side_claim') value = { claim: null, settings: {}, handoff_replies: [] }
    return { content: [{ type: 'text', text: JSON.stringify(value) }] }
  } },
  ui: { invalidate() {}, log() {}, toast: t => toasts.push(t), open: async args => { opens.push(args); return { isPlaced: true } },
    close: async () => {}, ask: async () => asks.shift(), resolve: () => ({ Box: el('Box'), Text: el('Text'), Button: el('Button'), Client: el('Client') }) },
  clock: { after: () => ({ cancel() {} }) },
  tool: { list: async () => [], call: async () => ({}) },
  agent: { list: async () => [], register: async () => ({}), spawn: async () => ({ deny: 'fixture' }) },
  command: { register: async () => {} },
}
const pane = hook('ui.render', { component: 'Pane', requestId: 'work-details' })
const render = (surface, columns) => pane($, { surface, component: 'Pane', requestId: 'work-details',
  props: { bodyColumns: columns, scroll: { bodyRows: 30 } }, viewport: { columns, rows: 40 } }, async () => null)
function walk(node, out = [], parent = null) {
  if (!node || typeof node !== 'object') return out
  if (Array.isArray(node)) { node.forEach(n => walk(n, out, parent)); return out }
  node.parentType = parent
  out.push(node)
  walk(node.props?.children, out, node.type)
  return out
}
// A row Button draws its colored child Text in place of its label: count it once.
const texts = tree => walk(tree).flatMap(n => n.type === 'Text' && n.parentType !== 'Button' && typeof n.props.children === 'string'
  ? [n.props.children] : n.type === 'Button' && typeof n.props.label === 'string' ? [n.props.label] : [])
const press = async (tree, key) => { const b = walk(tree).find(n => n.type === 'Button' && n.props.key === key)
  if (!b) throw new Error('no button ' + key); await b.props.onPress({ surface: 'terminal' }) }
const colors = tree => [...new Set(walk(tree).filter(n => n.type === 'Text' && n.props.color).map(n => n.props.color))]
const types = tree => [...new Set(walk(tree).map(n => n.type))]
const hovers = tree => walk(tree).filter(n => n.props?.hover).length

await hook('session.start', {})($, { isInteractive: true }, async e => e)
const view = await hook('command.run', { command: 'work-mode' })($, { args: 'view', origin: { kind: 'composer' } })
const out = { view: view.text, opened: opens.find(o => o.id === 'work-details') }
let tree = await render('terminal', 110)
const client = walk(tree).find(n => n.props?.key === 'obs-graphic')
out.graphical = Boolean(client)
out.graphicProps = client?.props?.props || {}
// The remaining portable assertions exercise the plain cross-surface fallback;
// observatory.test.ts drives the terminal Client itself.
tree = await render('desktop', 110)
out.work = { texts: texts(tree), colors: colors(tree), types: types(tree), hovers: hovers(tree),
  headerDirection: walk(tree).find(n => n.props?.key === 'obs-header').props.flexDirection }
await press(tree, 'obs-side'); tree = await render('desktop', 110)
out.side = { texts: texts(tree), colors: colors(tree) }
await press(tree, 'side-p1'); tree = await render('desktop', 110)
out.sideP1 = texts(tree).filter(t => /request|urgent|quarantined|side_mcp/.test(t))
await press(tree, 'obs-settings'); tree = await render('desktop', 110)
out.settings = { first: texts(tree).filter(t => t.includes(' = ')) }
await press(tree, 'settings-next'); tree = await render('desktop', 110)
out.settings.second = texts(tree).filter(t => t.includes(' = '))
await press(tree, 'settings-prev'); tree = await render('desktop', 110)
const before = calls.length
await press(tree, 'setting-work_mode')
asks = ['ask']; await press(tree, 'setting-work_plan_approval_ask')
asks = ['3']; await press(tree, 'setting-work_parallel_p1_limit')
out.writes = calls.slice(before).filter(c => c.tool === 'work_live_settings' && c.args.key).map(c => [c.args.key, c.args.value])
const mobile = await render('mobile', 40)
out.mobile = { types: types(mobile), hint: texts(mobile).at(-1), settingsRows: texts(mobile).filter(t => t.includes(' = ')).length,
  headerDirection: walk(mobile).find(n => n.props?.key === 'obs-header').props.flexDirection, hovers: hovers(mobile) }
const narrow = await render('terminal', 50)
out.narrow = { hint: texts(narrow).at(-1) }
const composer = await hook('command.run', { command: 'work-mode' })($, { args: 'settings', origin: { kind: 'composer' } })
out.composer = composer.text
// A display refresh (turn start) begun while /work-mode view reads its snapshot must not
// cancel opening the pane.
const call = $.mcp.call
let release, gate = new Promise(resolve => { release = resolve })
$.mcp.call = async (server, tool, args) => {
  if (gate && tool === 'work_status' && !args.detail) { const held = gate; gate = null; await held }
  return call(server, tool, args)
}
opens.length = 0
const racing = hook('command.run', { command: 'work-mode' })($, { args: 'view', origin: { kind: 'composer' } })
await new Promise(resolve => setTimeout(resolve, 0))
await hook('turn.start', {})($, {}, async e => e)
release()
out.race = { text: (await racing).text, opened: opens.some(o => o.id === 'work-details') }
$.mcp.call = call
// P0 lives in the P0 store; the goal's stored field keeps P1/P3 (Celly 2026-11-17
// saw its P0 goals as P1). After a refresh both panes label the goal P0.
tree = await render('desktop', 110)
await press(tree, 'obs-work'); tree = await render('desktop', 110)
const graphicBefore = walk(await render('terminal', 110)).find(n => n.props?.key === 'obs-graphic')?.props?.props
out.effective = { before: (graphicBefore?.nodes || []).filter(n => n.id === 'n5' || n.id === 'n1').map(n => n.id + ':' + n.priority) }
rootPriorities = [{ root_id: 'n5', title: 'Queued goal', canonical_priority: 'P3', effective_priority: 'P0' },
  { root_id: 'n1', title: 'Ship observatory', canonical_priority: 'P1', effective_priority: 'P1' }]
forks = { available: true, confirmed_count: 1, confirmed: [{ lease_id: 'l1', item_id: 'n5', item_title: 'Queued goal',
  priority: 'P1', effective_priority: 'P0', agent_name: 'work-p0-x', native: { status: 'running', last_seen_at: 1 } }] }
await press(tree, 'detail-refresh')
const graphicAfter = walk(await render('terminal', 110)).find(n => n.props?.key === 'obs-graphic')?.props?.props
out.effective.after = (graphicAfter?.nodes || []).filter(n => n.id === 'n5' || n.id === 'n1').map(n => n.id + ':' + n.priority)
out.effective.forks = (graphicAfter?.parallel?.confirmed || []).map(f => f.item + ':' + f.priority)
tree = await render('desktop', 110)
out.effective.desktop = texts(tree).filter(t => t.includes('Queued goal'))
// Stage 3: the router's per-priority diagnosis reaches the pane and /work-mode parallel.
diagnosis = { summary: 'P1 1/3 can fill · P2 0/2 no candidates (free slot but no runnable item (goal in planning 1))',
  attention: ['P2'], rows: [
    { priority: 'P0', limit: null, active: 0, free: null, candidates: 0, state: 'no_work', reason: 'no unfinished item' },
    { priority: 'P1', limit: 3, active: 1, free: 2, candidates: 2, state: 'can_fill', reason: '2 runnable; the next pump fills free slots' },
    { priority: 'P2', limit: 2, active: 0, free: 2, candidates: 0, state: 'no_candidates',
      reason: 'free slot but no runnable item (goal in planning 1)' }] }
tree = await render('desktop', 110)
await press(tree, 'detail-refresh')
const graphicDiag = walk(await render('terminal', 110)).find(n => n.props?.key === 'obs-graphic')?.props?.props
out.dispatch = { pane: graphicDiag?.parallel?.dispatch || null,
  text: (await hook('command.run', { command: 'work-mode' })($, { args: 'parallel', origin: { kind: 'composer' } })).text }
console.log(JSON.stringify(out))

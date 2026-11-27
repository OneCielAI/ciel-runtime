// Renders the packaged terminal Observatory Client (observatory.client.js) with
// side receipts in the shape register.js passes it, on a scripted surface. No
// Claude Code. Prints, per width, the text of each receipt's row.
import Observatory from './observatory.client.js'

const el = type => props => ({ type, props })
const receipts = [
  { id: 'r-retry', priority: 'P3', summary: 'Retry row', failure: 'retrying' },
  { id: 'r-wait', priority: 'P3', summary: 'Waiting row', failure: 'waiting' },
  { id: 'r-dead', priority: 'P3', summary: 'Terminal row', failure: 'quarantined' },
  { id: 'r-fresh', priority: 'P3', summary: 'Fresh row', stage: 'received' },
  { id: 'r-done', priority: 'P3', summary: 'Answered row', failure: 'retrying', delivery: 'submitted' },
]
function texts(node, out = []) {
  if (!node || typeof node !== 'object') return out
  if (Array.isArray(node)) { node.forEach(child => texts(child, out)); return out }
  if (node.type === 'Text' && typeof node.props.children === 'string') out.push(node.props.children)
  texts(node.props?.children, out)
  return out
}
const out = {}
for (const columns of [100, 40]) {
  const surface = { elements: { Box: el('Box'), Text: el('Text') }, columns, rows: 30, state: { scroll: 0 },
    setState() {}, post() {}, onKey() {}, onPointer() {} }
  const rows = texts(Observatory({ tab: 'side', receipts, nodes: [], actors: [], parallel: {} }, surface))
  out[columns] = Object.fromEntries(receipts.map(row => [row.summary,
    (rows.find(text => text.includes(row.summary)) || '').replace(/\s+/g, ' ').trim()]))
}
// User 2026-11-26: the item list animates the row a fork is running now.
// Script the pane's own frame clock as the engine does and read the list
// before and after one beat: only the running row's mark may move.
const clock = { ms: 0, fn: null }
const workSurface = { elements: { Box: el('Box'), Text: el('Text') }, columns: 100, rows: 30, state: undefined,
  setState(next) { workSurface.state = next }, post() {}, onKey() {}, onPointer() {},
  every(ms, fn) { clock.ms = ms; clock.fn = fn; return () => {} } }
const workNodes = [
  { id: 'g1', title: 'Goal', status: 'in_progress', priority: 'P1', depth: 0, cls: 'run', done: 0, total: 1, forkChildren: 1 },
  { id: 'a', parent_id: 'g1', title: 'Cache', status: 'in_progress', priority: 'P1', depth: 1, cls: 'run', done: 0, total: 0, fork: true }]
const workProps = { tab: 'work', nodes: workNodes, view: 'all', parallel: { forks: 1, evidence: true, max: 4 },
  receipts: [], actors: [] }
const markOf = list => list.map(text => text.trim()).filter(text => text.length === 1 && '◐◓◑◒'.includes(text))
const first = texts(Observatory(workProps, workSurface))
clock.fn()
const second = texts(Observatory(workProps, workSurface))
const stillSurface = { elements: { Box: el('Box'), Text: el('Text') }, columns: 100, rows: 30, state: undefined,
  setState(next) { stillSurface.state = next }, post() {}, onKey() {}, onPointer() {},
  every(ms, fn) { clock.idleMs = ms; clock.idle = fn; return () => {} } }
Observatory({ ...workProps, nodes: [workNodes[0]] }, stillSurface)
clock.idle()
out.beat = { ms: clock.ms, before: markOf(first), after: markOf(second),
  changed: first.filter((text, index) => text !== second[index]).length,
  idleState: stillSurface.state === undefined }
console.log(JSON.stringify(out))

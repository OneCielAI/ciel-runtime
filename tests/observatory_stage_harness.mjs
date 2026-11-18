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
console.log(JSON.stringify(out))

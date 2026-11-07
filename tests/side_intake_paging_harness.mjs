// Drives hostJson and eventView from the packaged side-intake module.
import { createHash } from 'node:crypto'
import { hostJson, eventView } from './side-intake.js'

const text = (value) => ({ content: [{ type: 'text', text: value }], isError: false })
const whole = JSON.stringify({ claim: { text: 'question', workload: { nodes: Array.from({ length: 333 }, (_, i) => ({ id: 'n' + i, title: '대기 목표 ' + i })) } } })
const id = createHash('sha256').update(whole, 'utf8').digest('hex')
const parts = []
for (let i = 0; i < whole.length; i += 7000) parts.push(whole.slice(i, i + 7000))
const calls = []
let tamper = false
const $ = { mcp: { call: async (_server, tool, args) => {
  calls.push({ tool, part: args.part, hasToken: args._host?.token === 'tok' })
  // A changed character keeps the JSON valid but must fail the digest check.
  const piece = tamper && args.part === 1 ? parts[1].replace('대기', '변경') : parts[args.part]
  return text(JSON.stringify({ id: args.id, part: args.part, parts: parts.length, text: piece }))
} } }
const envelope = text(JSON.stringify({ paged: { id, parts: parts.length, chars: whole.length } }))
const value = await hostJson($, 'work_side_claim', envelope, 'claude:parent', 'tok')
let notJson = ''
try { await hostJson($, 'work_side_claim', text('Error: result (182,656 characters across 1 line) exceeds maximum allowed tokens'), 'claude:parent', 'tok') }
catch (error) { notJson = String(error.message) }
tamper = true
let mismatch = ''
try { await hostJson($, 'work_side_claim', envelope, 'claude:parent', 'tok') } catch (error) { mismatch = String(error.message) }
const event = JSON.stringify({ specversion: '1.0', id: 'e1', source: 'http://walkie/conversations/one', type: 'com.ciel.walkie.support.message',
  time: '2026-11-07T00:00:00Z', data: { activity_context: 'wkp_' + 'S'.repeat(340), event_type: 'agent.pending_reminder',
    payload: { held: 1, text: '첫 줄\n둘째 줄' }, text: '첫 줄\n둘째 줄', token: 'secret-value' } })
const view = eventView(event)
const plain = eventView('plain question\nsecond line')
console.log(JSON.stringify({
  nodes: value.claim.workload.nodes.length, pageCalls: calls.filter(c => c.tool === 'work_host_page').length,
  tokenSent: calls.every(c => c.hasToken), notJson, mismatch,
  summary: view.summary, lines: view.lines, plainSummary: plain.summary, plainLines: plain.lines,
}))

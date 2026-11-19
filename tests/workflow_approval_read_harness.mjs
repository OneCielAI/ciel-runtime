// Approval re-check reads (side-intake.js, workflow approval read section): only the registered request
// message, never the thread. Prints one JSON line per scenario.
import { readFileSync } from 'node:fs'
// The self-contained section of the packaged side-intake.js, evaluated alone.
const source = readFileSync(new URL('./side-intake.js', import.meta.url), 'utf8')
const from = source.indexOf('// ---- workflow approval read: begin')
const to = source.indexOf('// ---- workflow approval read: end')
if (from < 0 || to < 0) throw new Error('workflow approval read section not found in side-intake.js')
const readApprovalRequests = new Function(source.slice(from, to) + ';return readApprovalRequests')()
const scenario = process.argv[2]
const conversation = '11111111-2222-3333-4444-555555555555'
const otherConversation = '99999999-2222-3333-4444-555555555555'
const calls = []
const replies = Array.from({ length: 2000 }, (_, index) => ({ id: String(10001 + index), thread_root_id: '9001', text: 'reply ' + index }))
let messages = []
const $ = { mcp: { call: async (server, tool, args) => {
  calls.push({ tool, args })
  let value
  if (tool === 'walkie_read_messages') value = { messages: messages.filter(row => BigInt(row.id) > BigInt(args.after)).slice(0, 200) }
  else if (tool === 'walkie_call_tool') value = replies.filter(row => BigInt(row.id) > BigInt(args.arguments.after)).slice(0, 200)
  else throw new Error('unexpected tool ' + tool)
  return { isError: false, content: [{ type: 'text', text: JSON.stringify(value) }] }
} } }
const request = (root, id, extra = {}) => ({ root_id: root, request_read: { server: 'walkie', conversation_id: conversation,
  message_id: id, thread_root_id: '9001', ...extra } })
const approved = [{ user_id: 'u1', decision: 'approved', superseded_by: null, withdrawn_at: null }]
let out
if (scenario === 'long-thread') {
  // The request sits in a 2,000-reply thread; the root and replies are not read.
  messages = [{ id: '9001', text: 'root' }, { id: '9100', thread_root_id: '9001', text: '[proposal n1 v1 x]', approvals: approved }]
  const read = await readApprovalRequests($, [request('n1', '9100')])
  out = { reads: read.reads, tools: calls.map(c => c.tool), after: calls[0]?.args.after, checks: read.checks.length,
    rows: read.checks[0]?.source_message.messages.length, approvals: read.checks[0]?.source_message.messages[0].approvals.length }
} else if (scenario === 'batch') {
  // Three requests of one conversation: one page covers them; checks keep the router's order.
  messages = [{ id: '9100', thread_root_id: '9001', approvals: [] }, { id: '9200', thread_root_id: '9001', approvals: [] },
    { id: '9300', thread_root_id: '9001', approvals: [] }]
  const read = await readApprovalRequests($, [request('p0', '9300'), request('p1', '9100'), request('p3', '9200')])
  out = { reads: read.reads, order: read.checks.map(c => c.root_id) }
} else if (scenario === 'no-approvals') {
  // A row without approvals[]: replies after the cursor only when the row changed.
  messages = [{ id: '9100', thread_root_id: '9001', text: '[proposal]' }]
  const first = await readApprovalRequests($, [request('n1', '9100', { reply_cursor: '11000' })])
  const firstCalls = calls.length
  const unchanged = await readApprovalRequests($, [request('n1', '9100', { reply_cursor: first.checks[0].reply_cursor,
    row_digest: first.checks[0].row_digest })])
  out = { firstReads: first.reads, firstReplyCalls: calls.slice(0, firstCalls).filter(c => c.tool === 'walkie_call_tool').length,
    firstReplyAfter: calls.find(c => c.tool === 'walkie_call_tool')?.args.arguments.after,
    cursor: first.checks[0].reply_cursor, replyRows: first.checks[0].source_message.messages.length - 1,
    unchangedReads: unchanged.reads, unchangedTools: calls.slice(firstCalls).map(c => c.tool) }
} else if (scenario === 'forged') {
  // Rows that are not the registered request: another thread, another conversation, a root.
  messages = [{ id: '9100', thread_root_id: '7777', text: '[proposal]', approvals: approved },
    { id: '9200', thread_root_id: '9001', conversation_id: otherConversation, approvals: approved },
    { id: '9300', text: '[proposal]', approvals: approved }]
  const read = await readApprovalRequests($, [request('a', '9100'), request('b', '9200'), request('c', '9300')])
  out = { checks: read.checks.length, refused: read.refused }
} else throw new Error('unknown scenario')
console.log(JSON.stringify(out))

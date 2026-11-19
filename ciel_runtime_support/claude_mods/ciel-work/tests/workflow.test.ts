import { expect, mock, test } from 'claude-code/testing'
// Approval workflow (spec 4.4.1, 6, 6.1) and the KOBE 2026-11-18 start/gate fixes.
const T = 'mcp__ciel-runtime-router__'
const HOST_REPLIES = {
  work_parallel_status: { version: 1, active_device: 0, leases: {}, policy: { max_active: 5, p1_reserved: 1 } },
  work_side_claim: { claim: null, settings: {}, handoff_replies: [] },
  work_side_ack: { state: 'not_applicable' },
  work_side_status: { side_inbox_receipts: [] },
  work_parallel_event: { recorded: true },
  work_side_gate_check: { interval_minutes: 5, queued: false, reason: 'no_gates' },
  work_proposal_status: { enabled: true, goals: [], todo: [], counts: {} },
  work_side_workflow_event: { applied: false, decision: 'pending', reason: 'no complete approval' },
}
function setup(on) {
  const s = { snapshot: { enabled: true, phase: 'planning', approval: 'auto', version: 1,
    active_root: { id: 'g1', title: 'Goal' }, done: 0, total: 2 }, sessionId: 'session-a', calls: [], hostCalls: [],
    replies: { ...HOST_REPLIES }, holdStatus: null, appends: [], spawns: [], prompts: [], logs: [] }
  on('session.id', () => ({ value: s.sessionId }))
  on('mcp.call', async ($, e) => {
    if (e.server === 'walkie') {
      s.calls.push(e)
      if (s.readRows && e.tool === 'walkie_read_messages')
        return { value: { isError: false, content: [{ type: 'text', text: JSON.stringify({ messages:
          s.readRows.filter(row => BigInt(row.id) > BigInt(e.args.after)
            && (!row.conversation_id || row.conversation_id === e.args.conversation_id)) }) }] } }
      const target = e.args.operation_id ? null : e.args.after
      if (e.tool === 'walkie_read_messages') return { value: { isError: false, content: [{ type: 'text', text: JSON.stringify({ messages: [{ id: String(BigInt(target) + 1n), text: 'root' }] }) }] } }
      const root = e.args.arguments.root_id
      const message = s.threadMessage?.(root) || []
      return { value: { isError: false, content: [{ type: 'text', text: JSON.stringify(message) }] } }
    }
    if (Object.hasOwn(s.replies, e.tool)) {
      s.hostCalls.push(e)
      if (e.tool === 'work_parallel_status' && s.holdStatus) await s.holdStatus
      const reply = typeof s.replies[e.tool] === 'function' ? s.replies[e.tool](e) : s.replies[e.tool]
      return { value: { isError: false, content: [{ type: 'text', text: JSON.stringify(reply) }] } }
    }
    s.calls.push(e)
    return { value: { isError: false, content: [{ type: 'text', text: e.args.format === 'json' ? JSON.stringify(s.snapshot) : 'Goal' }] } }
  })
  on('command.register', () => ({ value: { command: 'x' } }))
  on('session.start', () => ({ cwd: '/isolated' }))
  on('session.end', () => ({ sessionId: s.sessionId }))
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  on('turn.complete', (_, e) => ({ text: e.answer }))
  on('prompt.submit', ($, e) => { s.prompts.push(e.text); return { text: e.text, context: e.context } })
  on('env.get', () => ({ value: 'fixture-host-token' }))
  on('agent.list', () => ({ value: [] }))
  // The engine stand-in: a fork runs on the parent's (main's) model unless a model is named.
  on('agent.spawn', ($, e, next) => { s.spawns.push(e); return next(e) })
  on('session.append', ($, e, next) => { s.appends.push(e.message.content[0].text); return next(e) })
  on('ui.log', (_, e) => { s.logs.push(e.text); return { value: undefined } })
  on('tool.call', () => ({ result: 'ok' }))
  return s
}
const gateCalls = s => s.hostCalls.filter(c => c.tool === 'work_side_gate_check' && !c.args.host_report)

test('a slow start host phase does not hold session.start or commands; the gate check is armed first', async ($, on) => {
  const s = setup(on); const clock = mock.clock(on)
  let release
  s.holdStatus = new Promise(resolve => { release = resolve })
  // KOBE 2026-11-18: session.start held the hooks worker 172 s paging the host status.
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  const command = await $.command.run({ command: 'work-mode', args: 'help' })
  expect(String(command.text)).toContain('work-mode')
  // The gate check fires on time although the host phase still waits on its status read.
  await clock.advance(60000)
  expect(s.hostCalls.some(c => c.tool === 'work_parallel_status')).toBe(true)
  expect(gateCalls(s).length).toBe(1)
  release()
  await clock.settle()
})

test('the gate check runs while main is busy and reads pending approvals P0, P1, then the rest', async ($, on) => {
  const s = setup(on); const clock = mock.clock(on)
  const due = [['n0', 'P0', '9100'], ['n1', 'P1', '9200'], ['n2', 'P3', '9300']].map(([root, priority, id]) => ({
    root_id: root, priority, conversation_id: '11111111-2222-3333-4444-555555555555', message_id: id,
    thread_root_id: '9001', proposal_version: 1, request_read: { server: 'walkie',
      conversation_id: '11111111-2222-3333-4444-555555555555', message_id: id, thread_root_id: '9001' } }))
  // The first call of the tick offers the approvals; the second carries the reads back.
  s.replies.work_side_gate_check = e => e.args.approvals_read
    ? { interval_minutes: 1, queued: false, reason: 'not_due', approval_results: e.args.approvals_read.map(check => check.root_id === 'n1'
      ? { root_id: 'n1', applied: true, decision: 'approved', state: 'executing' } : { root_id: check.root_id, applied: false, decision: 'pending' }) }
    : { interval_minutes: 1, queued: false, reason: 'not_due', approvals_due: due, main_idle: e.args.idle }
  s.readRows = due.map(row => ({ id: row.message_id, thread_root_id: '9001', text: '[proposal]', approvals: [] }))
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  await $.turn.start({ turnId: 'turn-1', text: 'long work' })
  await clock.advance(60000)
  // One tick: the gate check offers the approvals, the reads return with the
  // same tick's second gate-check call (no separate workflow call, no fork), in
  // the router's order; the three requests of one conversation cost one read.
  expect(gateCalls(s).length).toBe(2)
  expect(gateCalls(s).every(c => c.args.idle === false)).toBe(true)
  expect(s.hostCalls.filter(c => c.tool === 'work_side_workflow_event').length).toBe(0)
  expect(s.spawns.length).toBe(0)
  const checks = gateCalls(s)[1].args.approvals_read
  expect(checks.map(c => c.root_id)).toEqual(['n0', 'n1', 'n2'])
  expect(checks.every(c => c.source_message?.source_context === 'verified_request'
    && c.source_message.messages.length === 1)).toBe(true)
  expect(s.calls.filter(c => c.server === 'walkie').map(c => c.tool)).toEqual(['walkie_read_messages'])
  expect(s.logs.some(text => text.includes('n1 approved'))).toBe(true)
  // The next tick waits for this one and keeps the one-minute cadence while main works.
  await clock.advance(60000)
  expect(gateCalls(s).length).toBe(4)
})

test('20 waiting items on 3 threads and 2 awaiting approvals: one tick, 3 thread reads, 2 request reads', async ($, on) => {
  const s = setup(on); const clock = mock.clock(on)
  const A = '11111111-2222-3333-4444-555555555555', B = '22222222-2222-3333-4444-555555555555'
  const threads = [['40', '2035', 8], ['5000', '5003', 7], ['7000', '7001', 5]].map(([root, after, count]) => ({
    key: A + '|' + root, server: 'walkie', conversation_id: A, root_id: root, after,
    gates: Array.from({ length: count }, (_, n) => 'g' + root + '-' + n) }))
  const due = [['w1', 'P1', A, '9100'], ['w2', 'P2', B, '9200']].map(([root, priority, conversation, id]) => ({
    root_id: root, priority, conversation_id: conversation, message_id: id, thread_root_id: '9001', proposal_version: 1,
    request_read: { server: 'walkie', conversation_id: conversation, message_id: id, thread_root_id: '9001' } }))
  s.replies.work_side_gate_check = e => e.args.threads_read
    ? { interval_minutes: 1, queued: true, receipt_id: 'r'.repeat(64), receipt_ids: ['r'.repeat(64)], events: 1,
      gates: threads.flatMap(t => t.gates), threads_read: e.args.threads_read.length,
      approval_results: (e.args.approvals_read || []).map(c => ({ root_id: c.root_id, applied: true, decision: 'approved' })) }
    : { interval_minutes: 1, queued: false, reason: 'read_threads', read_threads: threads, approvals_due: due }
  s.readRows = [{ id: '9100', conversation_id: A, thread_root_id: '9001', text: '[proposal w1]', approvals: [] },
    { id: '9200', conversation_id: B, thread_root_id: '9001', text: '[proposal w2]', approvals: [] }]
  s.threadMessage = root => root === '40' ? [{ id: '2036', conversation_id: A, thread_root_id: '40', text: 'approved by Kim' }] : []
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  await clock.advance(60000)
  // One tick: two gate-check calls, the second carrying both kinds of reads.
  expect(gateCalls(s).length).toBe(2)
  const second = gateCalls(s)[1].args
  expect(second.threads_read.length).toBe(3)
  expect(second.approvals_read.map(c => c.root_id)).toEqual(['w1', 'w2'])
  const walkie = s.calls.filter(c => c.server === 'walkie')
  expect(walkie.filter(c => c.tool === 'walkie_call_tool').length).toBe(3)
  expect(walkie.filter(c => c.tool === 'walkie_read_messages').map(c => [c.args.conversation_id, c.args.after]))
    .toEqual([[A, '9099'], [B, '9199']])
  expect(walkie.length).toBe(5)
  // No workflow call of its own and no fork per request: at most the one side fork of the gate event.
  expect(s.hostCalls.filter(c => c.tool === 'work_side_workflow_event').length).toBe(0)
  expect(s.spawns.length).toBeLessThanOrEqual(1)
  expect(s.logs.some(text => text.includes('w1 approved')) && s.logs.some(text => text.includes('w2 approved'))).toBe(true)
})

test('main hears of its first workflow item once, woken only when idle', async ($, on) => {
  const s = setup(on); const clock = mock.clock(on)
  let claimed = 0
  s.replies.work_proposal_status = e => e.args.claim_notice
    ? { todo: [{ root_id: 'n7', state: 'proposal_due' }], next_for_main: { root_id: 'n7', state: 'proposal_due', key: 'n7:proposal_due:0:1',
        text: 'Ciel Work workflow: goal n7 (CSV export) [P1] is proposal_due and waits for main.', claimed: claimed++ === 0 } }
    : { goals: [], todo: [] }
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  await clock.advance(60000)
  expect(s.appends.filter(text => text.startsWith('Ciel Work workflow: goal n7')).length).toBe(1)
  expect(s.prompts.some(text => text.startsWith('Ciel Work workflow wake'))).toBe(true)
  // A router that names the wake text: the idle main's prompt carries the item itself.
  await clock.advance(120000)
  expect(s.appends.filter(text => text.startsWith('Ciel Work workflow: goal n7')).length).toBe(1)
})

test('a workflow notice appended while main works wakes main once it is idle', async ($, on) => {
  const s = setup(on); const clock = mock.clock(on)
  let claimed = 0
  s.replies.work_proposal_status = e => e.args.claim_notice
    ? { next_for_main: { root_id: 'n8', state: 'proposal_due', key: 'n8:proposal_due:0:1',
        text: 'Ciel Work workflow: goal n8 (Export) [P2] is proposal_due and waits for main.', claimed: claimed++ === 0 } }
    : { goals: [], todo: [] }
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  await $.turn.start({ turnId: 'busy', text: 'long work' })
  await clock.advance(60000)
  // Appended at once (main reads it at its next step); no wake while busy.
  expect(s.appends.filter(text => text.startsWith('Ciel Work workflow: goal n8')).length).toBe(1)
  expect(s.prompts.some(text => text.startsWith('Ciel Work workflow wake'))).toBe(false)
  await $.turn.complete({ turnId: 'busy', answer: 'done', durationMs: 1, isAborted: false, reason: 'answer' })
  await clock.advance(60000)
  expect(s.prompts.filter(text => text.startsWith('Ciel Work workflow wake')).length).toBe(1)
  expect(s.appends.filter(text => text.startsWith('Ciel Work workflow: goal n8')).length).toBe(1)
  await clock.advance(120000)
  expect(s.prompts.filter(text => text.startsWith('Ciel Work workflow wake')).length).toBe(1)
})

test('a delegated proposal fork is requested on main model with the host prompt, also with parallel on', async ($, on) => {
  const s = setup(on); const clock = mock.clock(on)
  on('ui.toast', () => ({ value: undefined }))
  let offered = false
  // Parallel execution on: the Work spawn guard must still let the host's planner fork through.
  s.replies.work_parallel_status = { version: 1, active_device: 0, leases: {}, policy: { enabled: true, paused: false, max_active: 5 } }
  s.replies.work_side_gate_check = () => ({ interval_minutes: 5, queued: false, reason: 'not_due',
    workflow_delegations: offered ? [] : (offered = true, [{ root_id: 'n7', role: 'proposal', item_id: null, prompt: 'You are a planning fork' }]) })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  await clock.advance(60000)
  expect(s.spawns.length).toBe(1)
  // No model is named: the fork runs on main's model (spec 6.1).
  expect(s.spawns[0].model).toBeUndefined()
  expect(s.spawns[0].description).toBe('Work workflow proposal n7')
  expect(s.spawns[0].prompt).toBe('You are a planning fork')
  // Offered once: the next tick does not start a second fork.
  await clock.advance(60000)
  expect(s.spawns.length).toBe(1)
})
test('/work-mode workflow lists stage, owner, proposal and request', async ($, on) => {
  const s = setup(on)
  s.replies.work_proposal_status = { enabled: true, todo: [{ root_id: 'n7', title: 'CSV export', state: 'awaiting_review', priority: 'P1' }],
    goals: [{ root_id: 'n7', title: 'CSV export', state: 'awaiting_approval', owner: 'requester', blocked_text: 'goal n7 waits for the requester',
      proposal: { version: 2, code: '[proposal n7 v2 abc]' }, approval_request: { message_id: '9100' }, steps: {}, reworks: 0 }],
    metrics: { models: [{ model: 'cheap', first_pass_rate: 0.5, mean_reworks: 1, final_pass_rate: 1 }] } }
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/isolated' })
  const view = String((await $.command.run({ command: 'work-mode', args: 'workflow' })).text)
  expect(view).toContain('MAIN NEXT: n7 CSV export · awaiting_review')
  expect(view).toContain('awaiting_approval · owner requester')
  expect(view).toContain('proposal v2 [proposal n7 v2 abc]')
  expect(view).toContain('request 9100')
  expect(view).toContain('MODEL cheap: first pass 50%')
})

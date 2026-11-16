// Drives the packaged ciel-work-gate hooks module with a fake host and a fake
// router. Prints one JSON object of observations per scenario. The real
// claude.exe behaviour (origin skip, pinned ask plugin, agent.list rows) is
// measured in owned real-console runs, not here.
import { pathToFileURL } from 'node:url'
import { createHash } from 'node:crypto'

const MODULE = process.argv[2]
const TOKEN = 'tok-' + 'x'.repeat(12)
const WORKTREE = 'C:\\work\\wt-lease-1'

async function load() {
  const mod = await import(pathToFileURL(MODULE).href + '?g=' + Math.random())
  const hooks = {}, catches = {}
  mod.register((name, handler) => {
    hooks[name] = handler
    return { catch(fn) { catches[name] = fn; return this } }
  })
  return { hooks, catches }
}

function world(options = {}) {
  const w = {
    rows: options.rows || [], listFails: !!options.listFails, token: options.token === undefined ? TOKEN : options.token,
    leases: options.leases || {}, board: options.board || { enabled: true, phase: 'executing', approval: 'ask', version: 3, done: 0, total: 2, active_root: null },
    routerFails: !!options.routerFails, leaseSequence: options.leaseSequence || null, paged: !!options.paged,
    calls: [], logs: [], forwarded: [], pages: new Map(),
  }
  const json = value => ({ content: [{ type: 'text', text: JSON.stringify(value) }] })
  w.$ = {
    agent: { list: async () => { if (w.listFails) throw new Error('list unavailable'); return w.rows } },
    env: { get: async key => key === 'CIEL_RUNTIME_WORK_PARALLEL_HOST_TOKEN' ? w.token : undefined },
    session: { id: async () => 'sess1' },
    ui: { log: (text, opts) => w.logs.push({ text, to: opts?.to }) },
    mcp: {
      call: async (server, tool, args) => {
        w.calls.push({ server, tool, args })
        if (w.routerFails) throw new Error('router down: secret-ish text')
        if (tool === 'work_host_page') {
          const page = w.pages.get(args.id)
          return json({ id: args.id, part: args.part, parts: page.length, text: page[args.part] })
        }
        let value
        if (tool === 'work_parallel_status') {
          const leases = w.leaseSequence ? (w.leaseSequence.shift() ?? w.leases) : w.leases
          value = { version: 7, leases, ...(options.forkModels ? { fork_models: options.forkModels } : {}) }
        } else if (tool === 'work_status') value = w.board
        else return { isError: true, content: [{ type: 'text', text: 'unexpected tool ' + tool }] }
        if (w.paged) {
          const whole = JSON.stringify(value)
          const id = createHash('sha256').update(whole).digest('hex')
          const half = Math.ceil(whole.length / 2)
          w.pages.set(id, [whole.slice(0, half), whole.slice(half)])
          return json({ paged: { id, parts: 2 } })
        }
        return json(value)
      },
    },
  }
  return w
}
function nextFor(w, kind, origin = 'engine') {
  const fn = async e => {
    fn.called = true
    w.forwarded.push(e)
    return kind === 'check' ? { decision: 'allow' } : kind === 'spawn' ? { agentId: e.__newId || 'a-new' } : { result: 'ran' }
  }
  fn.origin = { plugin: origin }
  fn.called = false
  return fn
}
const workRow = (id, name = 'work-mb1q2r3s-abcd12', extra = {}) => ({ id, type: 'fork', status: 'running', spawnedBy: 'ciel-work', name, description: 'Work item', ...extra })
const runningLease = (host_id, extra = {}) => ({ 'lease-1': { id: 'lease-1', state: 'running', host_id, item_id: 'n1', workspace_path: WORKTREE, ...extra } })

async function call(gate, w, e, origin = 'engine') {
  const next = nextFor(w, 'call', origin)
  const result = await gate.hooks['tool.call'](w.$, e, next)
  return { result, nextCalled: next.called }
}
async function check(gate, w, e, origin = 'engine') {
  const next = nextFor(w, 'check', origin)
  const result = await gate.hooks['tool.check'](w.$, e, next)
  return { result, nextCalled: next.called }
}
const routerCalls = w => w.calls.filter(c => c.tool !== 'work_host_page').map(c => c.tool)
const out = {}

async function scenario(name, fn) {
  try { out[name] = await fn() } catch (error) { out[name] = { error: String(error?.stack || error) } }
}

await scenario('main_write_passes', async () => {
  const gate = await load(); const w = world()
  const r = await call(gate, w, { tool: 'Write', tool_use_id: 't1', file_path: 'C:/x/a.txt', content: '' })
  return { nextCalled: r.nextCalled, deny: r.result?.deny ?? null, routerCalls: routerCalls(w).length, listed: false }
})
await scenario('user_subagent_passes', async () => {
  const gate = await load(); const w = world({ rows: [{ id: 'u1', type: 'general-purpose', status: 'running', description: 'mine' }] })
  const r = await call(gate, w, { tool: 'Bash', tool_use_id: 't1', agentId: 'u1', command: 'echo' })
  const c = await check(gate, w, { tool: 'Write', tool_use_id: 't2', agentId: 'u1', input: { file_path: 'C:/x' } })
  return { callNext: r.nextCalled, checkNext: c.nextCalled, routerCalls: routerCalls(w).length }
})
await scenario('model_dispatched_fork_passes', async () => {
  const gate = await load(); const w = world({ rows: [{ id: 'm1', type: 'fork', status: 'running', name: 'work-mb1q2r3s-abcd12', description: 'Work x' }] })
  const r = await call(gate, w, { tool: 'Bash', tool_use_id: 't1', agentId: 'm1', command: 'echo' })
  return { nextCalled: r.nextCalled, routerCalls: routerCalls(w).length }
})
await scenario('side_actor_passes', async () => {
  const gate = await load(); const w = world()
  const spawn = nextFor(w, 'spawn', 'ciel-work')
  await gate.hooks['agent.spawn'](w.$, { subagentType: 'fork', description: 'Side SSE 0123456789ab', prompt: 'p', fork: true, __newId: 's1' }, spawn)
  w.rows = [{ id: 's1', type: 'fork', status: 'running', spawnedBy: 'ciel-work', description: 'Side SSE 0123456789ab' }]
  const bash = await call(gate, w, { tool: 'Bash', tool_use_id: 't1', agentId: 's1', command: 'echo' })
  const resolve = await call(gate, w, { tool: 'mcp__ciel-runtime-router__work_side_resolve', tool_use_id: 't2', agentId: 's1', decision: {} })
  return { bashNext: bash.nextCalled, resolveNext: resolve.nextCalled, routerCalls: routerCalls(w).length }
})
await scenario('side_actor_by_row_after_reload', async () => {
  const gate = await load()
  const w = world({ rows: [{ id: 's2', type: 'fork', status: 'running', spawnedBy: 'ciel-work', description: 'Side SSE 0123456789ab' }] })
  const bash = await call(gate, w, { tool: 'Bash', tool_use_id: 't1', agentId: 's2', command: 'echo' })
  return { bashNext: bash.nextCalled }
})
await scenario('named_side_shape_is_work_fork', async () => {
  const gate = await load(); const w = world()
  const spawn = nextFor(w, 'spawn', 'ciel-work')
  await gate.hooks['agent.spawn'](w.$, { name: 'side-intake-0123456789abcdef', subagentType: 'fork', description: 'Side SSE 0123456789ab', prompt: 'p', fork: true, __newId: 's3' }, spawn)
  w.rows = [{ id: 's3', type: 'fork', status: 'running', spawnedBy: 'ciel-work', name: 'side-intake-0123456789abcdef', description: 'Side SSE 0123456789ab' }]
  const bash = await call(gate, w, { tool: 'Bash', tool_use_id: 't1', agentId: 's3', command: 'echo' })
  return { deny: bash.result?.deny ?? null, nextCalled: bash.nextCalled }
})
await scenario('prebind_write_and_bash_denied_after_one_reread', async () => {
  const gate = await load(); const w = world({ rows: [workRow('f1')], leases: {} })
  const write = await call(gate, w, { tool: 'Write', tool_use_id: 't1', agentId: 'f1', file_path: WORKTREE + '\\a.txt', content: 'x' })
  const afterWrite = routerCalls(w).length
  const bash = await call(gate, w, { tool: 'Bash', tool_use_id: 't2', agentId: 'f1', command: 'echo' })
  return { writeDeny: write.result?.deny ?? null, writeNext: write.nextCalled, routerAfterWrite: afterWrite,
    bashDeny: bash.result?.deny ?? null, bashNext: bash.nextCalled,
    hostArgs: w.calls[0]?.args?._host?.token === TOKEN && w.calls[0]?.args?.session_id === 'claude:sess1',
    decisionLogs: w.logs.filter(l => l.text.includes(' decision ') && l.to === 'debug').length,
    logLeaksToken: w.logs.some(l => l.text.includes(TOKEN)) }
})
await scenario('bind_seen_on_reread_allows', async () => {
  const gate = await load()
  const w = world({ rows: [workRow('f1')], leaseSequence: [{}, runningLease('f1')] })
  const bash = await call(gate, w, { tool: 'Bash', tool_use_id: 't1', agentId: 'f1', command: 'echo' })
  return { deny: bash.result?.deny ?? null, nextCalled: bash.nextCalled, routerCalls: routerCalls(w) }
})
await scenario('reads_and_worker_tools', async () => {
  const gate = await load(); const w = world({ rows: [workRow('f1')], leases: {} })
  const read = await call(gate, w, { tool: 'Read', tool_use_id: 't1', agentId: 'f1', file_path: 'C:/x' })
  const routerAfterRead = routerCalls(w).length
  const report = await call(gate, w, { tool: 'mcp__ciel-runtime-router__work_parallel_report', tool_use_id: 't2', agentId: 'f1',
    lease_id: 'lease-1', worker_token: 'wt', _host: { token: 'forged', internal: true, user_confirmed: true } })
  const forwardedReport = w.forwarded.at(-1)
  return { readNext: read.nextCalled, routerAfterRead, reportNext: report.nextCalled,
    reportHostDropped: !Object.prototype.hasOwnProperty.call(forwardedReport, '_host'),
    reportKept: forwardedReport.worker_token === 'wt' && forwardedReport.lease_id === 'lease-1' }
})
await scenario('denied_router_and_orchestration_tools', async () => {
  const gate = await load(); const w = world({ rows: [workRow('f1')], leases: runningLease('f1') })
  const tools = ['mcp__ciel-runtime-router__work_execute', 'mcp__ciel-runtime-router__work_select', 'mcp__ciel-runtime-router__work_node_update',
    'mcp__ciel-runtime-router__work_parallel_event', 'mcp__ciel-runtime-router__send_message', 'mcp__ciel-runtime-router__work_side_claim',
    'Agent', 'SendMessage', 'TaskStop', 'Workflow', 'CronCreate', 'ScheduleWakeup', 'EnterWorktree', 'AskUserQuestion', 'ExitPlanMode', 'FutureTool']
  const rows = {}
  for (const [i, tool] of tools.entries()) {
    const r = await call(gate, w, { tool, tool_use_id: 'd' + i, agentId: 'f1', user_confirmed: true })
    rows[tool] = { denied: typeof r.result?.deny === 'string', nextCalled: r.nextCalled }
  }
  return { rows, routerCalls: routerCalls(w).length }
})
await scenario('bound_writes_confined', async () => {
  const gate = await load(); const w = world({ rows: [workRow('f1')], leases: runningLease('f1') })
  const go = async (i, e) => { const r = await call(gate, w, { tool_use_id: 'c' + i, agentId: 'f1', ...e }); return r.result?.deny ?? 'allowed' }
  return {
    inside: await go(1, { tool: 'Write', file_path: 'c:/WORK/wt-lease-1/src/a.py', content: '' }),
    insideBackslash: await go(2, { tool: 'Edit', file_path: WORKTREE + '\\src\\b.py', old_string: 'a', new_string: 'b' }),
    outside: await go(3, { tool: 'Write', file_path: 'C:\\work\\other\\a.py', content: '' }),
    sibling: await go(4, { tool: 'Write', file_path: 'C:\\work\\wt-lease-10\\a.py', content: '' }),
    escape: await go(5, { tool: 'Write', file_path: WORKTREE + '\\..\\other\\a.py', content: '' }),
    relative: await go(6, { tool: 'Write', file_path: 'src/a.py', content: '' }),
    notebook: await go(7, { tool: 'NotebookEdit', notebook_path: WORKTREE + '\\n.ipynb', new_source: '' }),
    patchInside: await go(8, { tool: 'apply_patch', input: '*** Begin Patch\n*** Update File: src/a.py\n@@\n-a\n+b\n*** End Patch' }),
    patchOutside: await go(9, { tool: 'apply_patch', input: '*** Begin Patch\n*** Add File: C:/elsewhere/x.py\n+x\n*** End Patch' }),
    patchNoPath: await go(10, { tool: 'apply_patch', input: 'nothing' }),
    bash: await go(11, { tool: 'Bash', command: 'echo' }),
    otherMcp: await go(12, { tool: 'mcp__other__write', path: '/x' }),
  }
})
await scenario('worktree_projection_cases', async () => {
  const run = async lease => {
    const gate = await load(); const w = world({ rows: [workRow('f1')], leases: { 'lease-1': lease } })
    const r = await call(gate, w, { tool: 'Write', tool_use_id: 'w', agentId: 'f1', file_path: 'C:/anywhere/a.py', content: '' })
    return r.result?.deny ?? 'allowed'
  }
  return {
    unconfined: await run({ id: 'lease-1', state: 'running', host_id: 'f1', workspace_path: null }),
    unprojected: await run({ id: 'lease-1', state: 'running', host_id: 'f1' }),
    invalid: await run({ id: 'lease-1', state: 'running', host_id: 'f1', workspace_path: 'relative/dir' }),
  }
})
await scenario('spawn_cwd_cross_check', async () => {
  const gate = await load(); const w = world({ leases: runningLease('f9') })
  const spawn = nextFor(w, 'spawn', 'ciel-work')
  await gate.hooks['agent.spawn'](w.$, { name: 'work-mb1q2r3s-abcd12', subagentType: 'fork', description: 'Work x', cwd: 'C:\\work\\elsewhere', prompt: 'p', fork: true, __newId: 'f9' }, spawn)
  w.rows = [workRow('f9')]
  const r = await call(gate, w, { tool: 'Write', tool_use_id: 'w', agentId: 'f9', file_path: WORKTREE + '\\a.py', content: '' })
  return { deny: r.result?.deny ?? null }
})
await scenario('board_phase', async () => {
  const run = async (board, tool, e, lease = {}) => {
    const gate = await load(); const w = world({ rows: [workRow('f1')], leases: runningLease('f1', lease), board })
    const r = await call(gate, w, { tool, tool_use_id: 'b', agentId: 'f1', ...e })
    return r.result?.deny ?? 'allowed'
  }
  const write = { file_path: WORKTREE + '\\a.py', content: '' }
  return {
    planningWrite: await run({ enabled: true, phase: 'planning' }, 'Write', write),
    planningBash: await run({ enabled: true, phase: 'planning' }, 'Bash', { command: 'echo' }),
    p0PlanningWrite: await run({ enabled: true, phase: 'planning' }, 'Write', write,
      { effective_priority: 'P0', planning_write_authorized: true }),
    disabledWrite: await run({ enabled: false }, 'Write', write),
    invalidWrite: await run({ enabled: true, phase: 'weird' }, 'Write', write),
  }
})
await scenario('lease_moved_on', async () => {
  const gate = await load(); const w = world({ rows: [workRow('f1')], leases: runningLease('f1', { state: 'submitted' }) })
  const r = await call(gate, w, { tool: 'Bash', tool_use_id: 'm', agentId: 'f1', command: 'echo' })
  return { deny: r.result?.deny ?? null, routerCalls: routerCalls(w).length }
})
await scenario('state_unreadable', async () => {
  const gate = await load(); const w = world({ rows: [workRow('f1')], routerFails: true })
  const bash = await call(gate, w, { tool: 'Bash', tool_use_id: 'u1', agentId: 'f1', command: 'echo' })
  const read = await call(gate, w, { tool: 'Read', tool_use_id: 'u2', agentId: 'f1', file_path: 'C:/x' })
  const w2 = world({ rows: [workRow('f1')], token: null })
  const gate2 = await load()
  const noToken = await call(gate2, w2, { tool: 'Bash', tool_use_id: 'u3', agentId: 'f1', command: 'echo' })
  return { bashDeny: bash.result?.deny ?? null, readNext: read.nextCalled, noTokenDeny: noToken.result?.deny ?? null,
    noTokenRouterCalls: routerCalls(w2).length, leaksRouterText: String(bash.result?.deny).includes('secret-ish') }
})
await scenario('classification_unreadable_leaves_alone', async () => {
  const gate = await load(); const w = world({ listFails: true })
  const r = await call(gate, w, { tool: 'Bash', tool_use_id: 'c1', agentId: 'z1', command: 'echo' })
  return { nextCalled: r.nextCalled, logged: w.logs.some(l => l.text.includes('classification_unreadable') && l.text.includes('count=1') && l.to === 'debug') }
})
await scenario('recognized_names', async () => {
  const names = ['work-mwem4prg-n92avv24pi9', 'work-auto-mwem4prg-w8y984vaug', 'work-rework-0123abcd-a1', 'work-rework-lease-with-hyphens-a12']
  const result = {}
  for (const [i, name] of names.entries()) {
    const gate = await load(); const w = world({ rows: [workRow('r' + i, name)], leases: {} })
    const r = await call(gate, w, { tool: 'Bash', tool_use_id: 'rn' + i, agentId: 'r' + i, command: 'echo' })
    result[name] = r.result?.deny ?? null
  }
  return result
})
await scenario('unrecognized_name', async () => {
  const gate = await load(); const w = world({ rows: [workRow('f7', 'mystery-fork')], leases: {} })
  const r = await call(gate, w, { tool: 'Write', tool_use_id: 'n1', agentId: 'f7', file_path: 'C:/x', content: '' })
  const unnamed = world({ rows: [workRow('f8', undefined, { name: undefined, description: 'Work x' })], leases: {} })
  const gate2 = await load()
  const r2 = await call(gate2, unnamed, { tool: 'Bash', tool_use_id: 'n2', agentId: 'f8', command: 'echo' })
  return { deny: r.result?.deny ?? null, unnamedDeny: r2.result?.deny ?? null }
})
await scenario('agent_spawn_rules', async () => {
  const gate = await load(); const w = world({ rows: [workRow('f1')] })
  const fromFork = nextFor(w, 'spawn', 'engine')
  const denied = await gate.hooks['agent.spawn'](w.$, { parentAgentId: 'f1', subagentType: 'general-purpose', description: 'x', prompt: 'p', fork: false, __newId: 'k1' }, fromFork)
  const fromMain = nextFor(w, 'spawn', 'engine')
  const allowed = await gate.hooks['agent.spawn'](w.$, { subagentType: 'general-purpose', description: 'mine', prompt: 'p', fork: false, __newId: 'k2' }, fromMain)
  // A model (engine-origin) spawn is not recorded as a ciel-work spawn.
  w.rows = []
  const after = await call(gate, w, { tool: 'Bash', tool_use_id: 's1', agentId: 'k2', command: 'echo' })
  return { deny: denied?.deny ?? null, forkNext: fromFork.called, mainAgent: allowed?.agentId ?? null, mainNext: fromMain.called,
    engineSpawnNotRecorded: after.nextCalled }
})
await scenario('presence_probe', async () => {
  const gate = await load(); const w = world()
  const nonce = 'ab'.repeat(16)
  const ask = await check(gate, w, { tool: 'Write', input: { file_path: '', content: '', ciel_work_gate_probe: nonce } }, 'ciel-work')
  const engine = await check(gate, w, { tool: 'Write', input: { ciel_work_gate_probe: nonce } }, 'engine')
  const realCall = await check(gate, w, { tool: 'Write', tool_use_id: 'r1', input: { ciel_work_gate_probe: nonce } }, 'ciel-work')
  const badNonce = await check(gate, w, { tool: 'Write', input: { ciel_work_gate_probe: 'nope' } }, 'ciel-work')
  return { ask: ask.result, askNext: ask.nextCalled, engineNext: engine.nextCalled, realCallNext: realCall.nextCalled,
    badNonceNext: badNonce.nextCalled, routerCalls: routerCalls(w).length }
})
await scenario('check_reuses_call_decision', async () => {
  const gate = await load(); const w = world({ rows: [workRow('f1')], leases: runningLease('f1') })
  await call(gate, w, { tool: 'Bash', tool_use_id: 'q1', agentId: 'f1', command: 'echo' })
  const afterCall = routerCalls(w).length
  const c = await check(gate, w, { tool: 'Bash', tool_use_id: 'q1', agentId: 'f1', input: { command: 'echo' } })
  const prebind = world({ rows: [workRow('f2')], leases: {} })
  const gate2 = await load()
  const direct = await check(gate2, prebind, { tool: 'Write', tool_use_id: 'q2', agentId: 'f2', input: { file_path: 'C:/x' } })
  return { afterCall, afterCheck: routerCalls(w).length, checkNext: c.nextCalled, directDecision: direct.result }
})
await scenario('paged_state', async () => {
  const gate = await load(); const w = world({ rows: [workRow('f1')], leases: runningLease('f1'), paged: true })
  const r = await call(gate, w, { tool: 'Write', tool_use_id: 'p1', agentId: 'f1', file_path: WORKTREE + '\\a.py', content: '' })
  const tampered = world({ rows: [workRow('f1')], leases: runningLease('f1'), paged: true })
  const gate2 = await load()
  const realCall = tampered.$.mcp.call
  tampered.$.mcp.call = async (server, tool, args) => {
    const result = await realCall(server, tool, args)
    if (tool === 'work_host_page') {
      const value = JSON.parse(result.content[0].text); value.text = value.text + ' '
      return { content: [{ type: 'text', text: JSON.stringify(value) }] }
    }
    return result
  }
  const bad = await call(gate2, tampered, { tool: 'Bash', tool_use_id: 'p2', agentId: 'f1', command: 'echo' })
  return { deny: r.result?.deny ?? null, nextCalled: r.nextCalled, pageCalls: w.calls.filter(c => c.tool === 'work_host_page').length,
    tamperedDeny: bad.result?.deny ?? null }
})
await scenario('catch_handlers', async () => {
  const gate = await load(); const w = world({ rows: [workRow('f1')], leases: {} })
  await call(gate, w, { tool: 'Bash', tool_use_id: 'x0', agentId: 'f1', command: 'echo' })   // classifies f1
  const n1 = nextFor(w, 'call'); n1.called = false
  const forFork = await gate.catches['tool.call'](w.$, { tool: 'Bash', agentId: 'f1' }, n1)
  const n2 = nextFor(w, 'call')
  const forOther = await gate.catches['tool.call'](w.$, { tool: 'Bash', agentId: 'other' }, n2)
  const n3 = nextFor(w, 'check')
  const checkFork = await gate.catches['tool.check'](w.$, { tool: 'Bash', agentId: 'f1' }, n3)
  const n4 = nextFor(w, 'check')
  const checkMain = await gate.catches['tool.check'](w.$, { tool: 'Bash' }, n4)
  return { forFork: forFork?.deny ?? null, forOtherNext: n2.called, checkFork, checkMainNext: n4.called,
    spawnCatch: typeof gate.catches['agent.spawn'] === 'function' }
})


// Per-priority fork models (turn.step): the model is named on every request of
// a ciel-work fork, the first one included; main and other agents are untouched.
async function step(gate, w, e) {
  const seen = []
  const next = value => {
    seen.push(value)
    return (async function* () { return { ok: true } })()
  }
  const stream = gate.hooks['turn.step'](w.$, e, next)
  for await (const _chunk of stream) { /* drain */ }
  return seen[0] ?? null
}
const MODELS = { provider: 'p', models: { P0: 'model-p0', P1: null, P2: 'model-p2', P3: null, P4: null, P5: null, side: 'model-side' },
  upstream: { P0: 'up-p0', P1: null, P2: 'up-p2', P3: null, P4: null, P5: null, side: 'up-side' }, status: {} }
const ALIAS_MAIN = 'ciel-runtime-p-main'
await scenario('fork_model_by_host_id', async () => {
  const gate = await load()
  const w = world({ rows: [workRow('f1')], leases: runningLease('f1', { priority: 'P2' }), forkModels: MODELS })
  const first = await step(gate, w, { agentId: 'f1', index: 0, model: ALIAS_MAIN, messages: [] })
  const second = await step(gate, w, { agentId: 'f1', index: 1, model: ALIAS_MAIN, messages: [] })
  return { first: first?.model, second: second?.model, statusReads: routerCalls(w).filter(t => t === 'work_parallel_status').length }
})
await scenario('fork_model_pre_bind_by_dispatch_name', async () => {
  const gate = await load()
  const leases = { 'lease-9': { id: 'lease-9', state: 'assigned', host_id: null, dispatch_name: 'work-mb1q2r3s-abcd12', priority: 'P1', effective_priority: 'P0' } }
  const w = world({ rows: [workRow('f2')], leases, forkModels: MODELS })
  const first = await step(gate, w, { agentId: 'f2', index: 0, model: ALIAS_MAIN })
  return { first: first?.model }
})
await scenario('fork_model_unknown_lease_not_cached', async () => {
  const gate = await load()
  const w = world({ rows: [workRow('f3', 'work-zzzzzz-yyyy')], leaseSequence: [{}], leases: runningLease('f3', { priority: 'P2' }), forkModels: MODELS })
  const first = await step(gate, w, { agentId: 'f3', index: 0, model: ALIAS_MAIN })
  const second = await step(gate, w, { agentId: 'f3', index: 1, model: ALIAS_MAIN })
  return { first: first?.model, second: second?.model }
})
await scenario('fork_model_side', async () => {
  const gate = await load()
  const w = world({ rows: [{ id: 's9', type: 'fork', status: 'running', spawnedBy: 'ciel-work', description: 'Side SSE 0123456789ab' }], forkModels: MODELS })
  const first = await step(gate, w, { agentId: 's9', index: 0, model: ALIAS_MAIN })
  return { first: first?.model }
})
// A side retry after the side model failed (ciel-work marks it " main-model"):
// main's chosen model, or the inherited engine model (main's) when none is set.
await scenario('fork_model_side_retry_uses_main_model', async () => {
  const gate = await load()
  const row = { id: 's10', type: 'fork', status: 'running', spawnedBy: 'ciel-work', description: 'Side SSE 0123456789ab main-model' }
  const chosen = world({ rows: [row], forkModels: { ...MODELS, main: { model: 'ciel-runtime-p-chosen', upstream: 'up-chosen', id: 'chosen' } } })
  const withMain = await step(gate, chosen, { agentId: 's10', index: 0, model: ALIAS_MAIN })
  const gate2 = await load()
  const plain = world({ rows: [row], forkModels: MODELS })
  const inherit = await step(gate2, plain, { agentId: 's10', index: 0, model: ALIAS_MAIN })
  const bash = await call(gate2, plain, { tool: 'Bash', tool_use_id: 't1', agentId: 's10', command: 'echo' })
  return { withMain: withMain?.model, inherit: inherit?.model, sideBashNext: bash.nextCalled }
})
await scenario('fork_model_inherit_and_others', async () => {
  const gate = await load()
  const w = world({ rows: [workRow('f4'), { id: 'u1', type: 'general-purpose', status: 'running', description: 'mine' }],
    leases: runningLease('f4', { priority: 'P3' }), forkModels: MODELS })
  const inherit = await step(gate, w, { agentId: 'f4', index: 0, model: ALIAS_MAIN })
  const main = await step(gate, w, { index: 0, model: ALIAS_MAIN })
  const user = await step(gate, w, { agentId: 'u1', index: 0, model: ALIAS_MAIN })
  const noModels = world({ rows: [workRow('f5')], leases: runningLease('f5', { priority: 'P2' }) })
  const absent = await step(gate, noModels, { agentId: 'f5', index: 0, model: ALIAS_MAIN })
  return { inherit: inherit?.model, main: main?.model, user: user?.model, absent: absent?.model,
    mainRouterCalls: 0 }
})
await scenario('fork_model_router_failure_keeps_model', async () => {
  const gate = await load()
  const w = world({ rows: [workRow('f6')], routerFails: true, forkModels: MODELS })
  const first = await step(gate, w, { agentId: 'f6', index: 0, model: ALIAS_MAIN })
  return { first: first?.model, logged: w.logs.some(l => l.text.includes('fork-model-unreadable')),
    leaked: w.logs.some(l => l.text.includes('secret-ish')) }
})

await scenario('fork_model_direct_path_uses_upstream_id', async () => {
  const gate = await load()
  const w = world({ rows: [workRow('f7')], leases: runningLease('f7', { priority: 'P2' }), forkModels: MODELS })
  const first = await step(gate, w, { agentId: 'f7', index: 0, model: 'upstream-main' })
  return { first: first?.model }
})

await scenario('fork_model_rework_name_before_bind', async () => {
  const gate = await load()
  const leases = { 'rep-77': { id: 'rep-77', state: 'reserved', host_id: null, priority: 'P2', role: 'resume' } }
  const w = world({ rows: [workRow('f8', 'work-rework-rep-77-a1')], leases, forkModels: MODELS })
  const first = await step(gate, w, { agentId: 'f8', index: 0, model: ALIAS_MAIN })
  return { first: first?.model }
})

// Main session model: read at a turn's first step, kept for the whole turn.
const MAIN_MODELS = { ...MODELS, main: { model: 'ciel-runtime-p-chosen', upstream: 'up-chosen', id: 'chosen' } }
await scenario('main_model_turn_pinned', async () => {
  const gate = await load()
  const w = world({ forkModels: MAIN_MODELS })
  const a0 = await step(gate, w, { turnId: 't1', index: 0, model: ALIAS_MAIN })
  // A remote change during the turn: the next steps of t1 keep the turn's model.
  delete MAIN_MODELS.main
  const a1 = await step(gate, w, { turnId: 't1', index: 1, model: ALIAS_MAIN })
  const b0 = await step(gate, w, { turnId: 't2', index: 0, model: ALIAS_MAIN })
  MAIN_MODELS.main = { model: 'ciel-runtime-p-chosen', upstream: 'up-chosen', id: 'chosen' }
  const direct = await step(gate, w, { turnId: 't3', index: 0, model: 'upstream-main' })
  return { a0: a0?.model, a1: a1?.model, b0: b0?.model, direct: direct?.model,
    statusReads: routerCalls(w).filter(t => t === 'work_parallel_status').length }
})
await scenario('main_model_midturn_and_failure', async () => {
  const gate = await load()
  const w = world({ forkModels: { ...MODELS, main: { model: 'ciel-runtime-p-chosen', upstream: 'up-chosen', id: 'chosen' } } })
  const mid = await step(gate, w, { turnId: 'm1', index: 3, model: ALIAS_MAIN })
  const mid2 = await step(gate, w, { turnId: 'm1', index: 4, model: ALIAS_MAIN })
  const failing = world({ routerFails: true, forkModels: MODELS })
  const failed = await step(gate, failing, { turnId: 'f1', index: 0, model: ALIAS_MAIN })
  const failedNext = await step(gate, failing, { turnId: 'f1', index: 1, model: ALIAS_MAIN })
  // A fork never takes the main model, even with no fork slot configured.
  const fork = world({ rows: [workRow('f9')], leases: runningLease('f9', { priority: 'P3' }),
    forkModels: { ...MODELS, main: { model: 'ciel-runtime-p-chosen', upstream: 'up-chosen', id: 'chosen' } } })
  const forkStep = await step(gate, fork, { agentId: 'f9', index: 0, model: ALIAS_MAIN })
  return { mid: mid?.model, mid2: mid2?.model, midReads: routerCalls(w).length, failed: failed?.model,
    failedNext: failedNext?.model, failedReads: routerCalls(failing).length,
    logged: failing.logs.some(l => l.text.includes('main-model-unreadable')),
    leaked: failing.logs.some(l => l.text.includes('secret-ish')), forkStep: forkStep?.model }
})

console.log(JSON.stringify(out))

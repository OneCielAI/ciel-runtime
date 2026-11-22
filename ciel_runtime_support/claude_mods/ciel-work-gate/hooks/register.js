// ciel-work-gate: the Work rules inside forks that ciel-work started with
// $.agent.spawn.
//
// Why a separate plugin: a plugin's own tool.call, tool.check and turn.step
// hooks never run for a fork that plugin spawned (the origin recursion skip;
// measured on claude.exe 2.1.323, runs run-p0-e1-20261112-222712 and
// run-p0-opt-origin-20261112-232916). A different plugin does see that fork's
// calls with its agentId, and its deny stops them before they run (O1, O2).
// A plugin may have only one hooks module, so this cannot live inside
// ciel-work (run-p0-opt-origin-20261112-231351).
//
// Scope (lead ruling G2): ciel-work forks only. A ciel-work fork whose spawn
// has the exact side-actor shape (no name, subagentType fork, description
// "Side SSE <12 hex>") is left alone; every other ciel-work fork is a Work
// fork, including one whose name matches no Work pattern. Main, the user's
// own subagents and forks main's model dispatched (no spawnedBy) are never
// touched.
//
// Design and rulings: docs/journal/2026/11/12/features/work-parallel-open-items/
// blocker-3-work-fork-gate-design.md and lead-rulings-blocker3-20261113.md.

const GATE = 'ciel-work-gate'
// ciel-work refuses to start Work forks unless the presence probe answers this
// exact version (tests pin both sides to the same literal).
const GATE_VERSION = '1'
const OWNER = 'ciel-work'
const PREFIX = 'mcp__ciel-runtime-router__'
// " main-model": ciel-work's retry of an event after the side model failed; it
// runs on main's own model (forkModel), never the side slot's.
const SIDE_DESCRIPTION = /^Side SSE [0-9a-f]{12}( main-model)?$/
// The names ciel-work gives Work forks: work_parallel_run's key (register.js
// WORK_FORK_NAME), the pump's auto-fill key 'work-auto-<time>-<random>'
// (parallelPump), and reworkForkLeaseId's 'work-rework-<lease>-a<N>'.
const WORKER_NAME = /^work-[0-9a-z]{6,}-[0-9a-z]{4,}$/
const AUTO_NAME = /^work-auto-[0-9a-z]+-[0-9a-z]+$/
const P0_NAME = /^work-p0-[0-9a-f]{32}$/
const REWORK_NAME = /^work-rework-[\s\S]+-a[1-9]\d{0,2}$/
// Tool classes (design "Tool classes for Work forks", approved by G4).
const READ_TOOLS = new Set(['Read', 'Glob', 'Grep', 'ListAgents', 'WebFetch', 'WebSearch', 'Skill', 'ReportFindings'])
const FILE_WRITES = new Set(['Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'apply_patch'])
const SHELLS = new Set(['Bash', 'PowerShell', 'Monitor'])
// The worker's report path and two reads; every other router tool is denied.
const ROUTER_ALLOWED = new Set(['work_parallel_report', 'work_parallel_status', 'work_status', 'work_proposal_status'].map(tool => PREFIX + tool))
const ROUTER_HOST_ONLY = new Set(['work_priority_command'].map(tool => PREFIX + tool))
// A fork is bound while its lease is one of these (backend bind sets running).
const BOUND_STATES = new Set(['running', 'waiting_input'])
const PROBE_KEY = 'ciel_work_gate_probe'
const PROBE_NONCE = /^[0-9a-f]{32}$/
const DENY = 'Ciel Work fork gate: '

// Spawn inputs of forks ciel-work started, keyed by agentId: the exact shape
// G2 classifies by. Lost on a module reload; $.agent.list() is the fallback.
const spawns = new Map()
// Agents this module classified as Work forks: the .catch handlers deny only
// these when a hook body fails, so a failure never touches another agent.
const workForks = new Set()
// Decisions tool.call made, reused by tool.check for the same tool_use_id.
const decided = new Map()
let classificationUnreadable = 0

function remember(map, key, value, limit) {
  map.delete(key); map.set(key, value)
  while (map.size > limit) map.delete(map.keys().next().value)
}
function note($, text) {
  // Debug log only: generated ids, tool names, fixed rule codes, never input text.
  try { $.ui.log(GATE + ' ' + text, { to: 'debug' }) } catch { /* Logging never decides. */ }
}
function shortId(id) { return typeof id === 'string' ? id.slice(0, 12) : '-' }

function spawnShape(e) {
  return { name: typeof e.name === 'string' && e.name ? e.name : null,
    subagentType: typeof e.subagentType === 'string' ? e.subagentType : null,
    description: typeof e.description === 'string' ? e.description : '',
    cwd: typeof e.cwd === 'string' && e.cwd ? e.cwd : null }
}
// ciel-work's side actor in session "fresh": its registered agent type, an
// empty conversation with main's system prompt (side-intake.js FRESH_SIDE_TYPE).
const FRESH_SIDE_TYPE = 'ciel-work:side-fresh'
function sideShape(shape) {
  return shape.name === null && (shape.subagentType === 'fork' || shape.subagentType === FRESH_SIDE_TYPE)
    && SIDE_DESCRIPTION.test(shape.description)
}
// { kind: 'main' | 'other' | 'side' | 'work' | 'unclassified', name, recognized, cwd }
async function classify($, agentId) {
  if (typeof agentId !== 'string' || !agentId) return { kind: 'main' }
  const spawn = spawns.get(agentId) || null
  let rows = null
  try { rows = await $.agent.list() } catch { rows = null }
  const listed = Array.isArray(rows)
  const row = listed ? rows.find(agent => agent?.id === agentId) || null : null
  if (!spawn && !listed) {
    // G2: no evidence either way. The agent is left alone; acceptance requires 0.
    classificationUnreadable++
    note($, 'classification_unreadable agent=' + shortId(agentId) + ' count=' + classificationUnreadable)
    return { kind: 'unclassified' }
  }
  // agent.list is the authority for rules 1-2; the spawn record covers a fork
  // not yet (or no longer) listed.
  const owned = row ? row.spawnedBy === OWNER && (row.type === 'fork' || row.type === FRESH_SIDE_TYPE) : spawn !== null
  if (!owned) return { kind: 'other' }
  const shape = spawn || { name: typeof row.name === 'string' && row.name ? row.name : null,
    subagentType: row.type, description: typeof row.description === 'string' ? row.description : '', cwd: null }
  if (sideShape(shape)) return { kind: 'side', mainModel: shape.description.endsWith(' main-model') }
  workForks.add(agentId)
  while (workForks.size > 400) workForks.delete(workForks.values().next().value)
  const recognized = shape.name !== null
    && (WORKER_NAME.test(shape.name) || AUTO_NAME.test(shape.name) || P0_NAME.test(shape.name) || REWORK_NAME.test(shape.name))
  return { kind: 'work', name: shape.name, recognized, cwd: shape.cwd }
}

// The one place a router credential is read (lead ruling G1). B6 replaces this
// body; no deny rule reads a credential, and nothing here stores one.
async function routerCredential($) {
  const token = await $.env.get('CIEL_RUNTIME_WORK_PARALLEL_HOST_TOKEN')
  return typeof token === 'string' && token ? { token } : null
}
function fault(text) { return Object.assign(new Error(text), { gateFault: true }) }
function textOf(result) {
  return (result?.content || []).filter(block => block?.type === 'text').map(block => block.text).join('\n')
}
function parsed(tool, result) {
  if (!result || result.isError) throw fault(tool + ' was refused or failed')
  try { return JSON.parse(textOf(result)) } catch { throw fault(tool + ' did not return JSON') }
}
const TRANSIENT_MCP_ERROR = /timed?\s*out|timeout|not connected|disconnected|connection closed|no connected MCP|ECONNREFUSED|ECONNRESET|EPIPE|ETIMEDOUT|socket hang up|fetch failed|unable to connect/i
const gateReconnects = new Map()
async function mcpRead($, tool, payload, identity) {
  try { return await $.mcp.call('ciel-runtime-router', tool, payload) }
  catch (error) {
    if (!TRANSIENT_MCP_ERROR.test(String(error?.message || ''))) throw error
    const currentId = await $.session.id()
    if ('claude:' + currentId !== identity) throw error
    let retry = gateReconnects.get(identity)
    if (!retry) {
      retry = Promise.resolve().then(() => $.mcp.connect('ciel-runtime-router'))
      gateReconnects.set(identity, retry)
      const clear = () => { if (gateReconnects.get(identity) === retry) gateReconnects.delete(identity) }
      void retry.then(clear, clear)
    }
    let connection
    try { connection = await retry } catch { throw error }
    if (!connection?.isConnected || 'claude:' + await $.session.id() !== identity) throw error
    return $.mcp.call(connection.server || 'ciel-runtime-router', tool, payload)
  }
}
async function routerJson($, tool, extra = {}) {
  const credential = await routerCredential($)
  if (!credential) throw fault('the Work host credential is not provisioned')
  const id = await $.session.id()
  if (typeof id !== 'string' || !/^[A-Za-z0-9_-]{1,200}$/.test(id)) throw fault('the session identity is unavailable')
  const identity = 'claude:' + id
  const args = tool === 'work_status' ? { format: 'json', ...extra } : { ...extra }
  const value = parsed(tool, await mcpRead($, tool, { ...args, session_id: identity, _host: credential }, identity))
  if (!value?.paged) return value
  // Large host results arrive as verified pages (host_result_pages.py).
  const { id: pageId, parts } = value.paged
  if (typeof pageId !== 'string' || !/^[0-9a-f]{64}$/.test(pageId) || !Number.isInteger(parts) || parts < 1 || parts > 512)
    throw fault(tool + ' returned an invalid paged result')
  const pieces = []
  for (let part = 0; part < parts; part++) {
    const page = parsed('work_host_page', await mcpRead($, 'work_host_page',
      { id: pageId, part, session_id: identity, _host: credential }, identity))
    if (page?.id !== pageId || page.part !== part || page.parts !== parts || typeof page.text !== 'string')
      throw fault(tool + ' changed while its pages were read')
    pieces.push(page.text)
  }
  const whole = pieces.join('')
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(whole)))
  if ([...digest].map(byte => byte.toString(16).padStart(2, '0')).join('') !== pageId)
    throw fault(tool + ' pages did not match their digest')
  try { return JSON.parse(whole) } catch { throw fault(tool + ' pages were not JSON') }
}
async function leaseOf($, agentId) {
  const state = await routerJson($, 'work_parallel_status', { projection: 'gate', host_id: agentId })
  if (!state || typeof state.leases !== 'object' || state.leases === null) throw fault('work_parallel_status has no lease table')
  return Object.values(state.leases).find(lease => lease?.host_id === agentId) || null
}

// Paths: absolute only. Windows paths compare case-insensitively with forward
// slashes; '.' and '..' are resolved; anything else is not a confinable path.
function pathKey(value) {
  if (typeof value !== 'string' || !value) return null
  const text = value.replaceAll(String.fromCharCode(92), '/')
  const drive = /^[A-Za-z]:\//.test(text)
  if (!drive && !text.startsWith('/')) return null
  const parts = []
  for (const part of text.split('/')) {
    if (part === '' || part === '.') continue
    if (part === '..') {
      if (parts.length <= (drive ? 1 : 0)) return null
      parts.pop(); continue
    }
    parts.push(part)
  }
  const joined = (drive ? '' : '/') + parts.join('/')
  return drive ? joined.toLowerCase() : joined
}
function within(root, target) { return target === root || target.startsWith(root + '/') }
// The file paths a write names; null when a target cannot be read from the input.
function writeTargets(tool, input, root) {
  if (tool === 'NotebookEdit') return [input?.notebook_path]
  if (tool !== 'apply_patch') return [input?.file_path]
  const targets = []
  for (const value of Object.values(input || {})) {
    if (typeof value !== 'string') continue
    for (const line of value.split(/\r?\n/)) {
      const matched = /^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+)$/.exec(line)
      if (!matched) continue
      const target = matched[1].trim()
      targets.push(pathKey(target) !== null ? target : root + '/' + target)
    }
  }
  return targets
}

function allow(rule) { return { verdict: 'allow', rule } }
function deny(rule, text) { return { verdict: 'deny', rule, reason: DENY + text } }
function forkLabel(fork) {
  return fork.recognized ? 'this Work fork' : 'this unrecognized ciel-work fork' + (fork.name ? ' (' + fork.name + ')' : '')
}
// The decision for one call of a classified Work fork. Never throws.
async function decide($, tool, input, agentId, fork) {
  const who = forkLabel(fork)
  if (READ_TOOLS.has(tool)) return allow('read')
  if (ROUTER_ALLOWED.has(tool)) return allow('router-worker')
  if (ROUTER_HOST_ONLY.has(tool)) return deny('router-host', tool.slice(PREFIX.length) + ' is available only to the coordinator host.')
  if (tool.startsWith(PREFIX))
    return deny('router', tool.slice(PREFIX.length) + ' is not available to ' + who
      + '; a worker reports only through work_parallel_report with its worker_token.')
  const fileWrite = FILE_WRITES.has(tool)
  if (!fileWrite && !SHELLS.has(tool) && !tool.startsWith('mcp__'))
    return deny('tool', tool + ' is not available to ' + who + '.')
  let lease
  try {
    lease = await leaseOf($, agentId)
    // Pre-bind re-read (G4): the host records the bind just after the spawn resolves.
    if (!lease || !BOUND_STATES.has(lease.state)) lease = await leaseOf($, agentId)
  } catch (error) {
    return deny('state-unreadable', 'Work state unreadable (' + (error?.gateFault ? error.message : 'router read failed')
      + '); ' + tool + ' by ' + who + ' is refused.')
  }
  if (!lease)
    return deny('pre-bind', 'the host has not recorded ' + who + "'s lease binding yet, so " + tool
      + ' is refused. Reads are allowed; retry the call after the binding is recorded.')
  if (!BOUND_STATES.has(lease.state))
    return deny('lease-state', who + "'s lease " + lease.id + ' is ' + lease.state + ', so ' + tool + ' is refused.')
  if (!fileWrite) return allow('bound')
  // Parity row 1: file writes wait while Work is planning or unreadable.
  let board
  try { board = await routerJson($, 'work_status', { projection: 'phase' }) } catch (error) {
    return deny('board-unreadable', 'the Work board is unreadable (' + (error?.gateFault ? error.message : 'router read failed')
      + '); ' + tool + ' is refused.')
  }
  if (board?.enabled !== false) {
    if (board?.enabled !== true || !['planning', 'executing'].includes(board.phase))
      return deny('board-unreadable', 'the Work board snapshot is not valid; ' + tool + ' is refused.')
    // P0 is dispatched without plan approval; the host marks its exact lease.
    if (board.phase === 'planning' && lease.planning_write_authorized !== true)
      return deny('planning', 'Work is planning; ' + tool + ' waits for approval.')
  }
  // Parity row 13 (G3): the host projection names the lease worktree.
  if (!Object.prototype.hasOwnProperty.call(lease, 'workspace_path'))
    return deny('worktree-unprojected', 'the host status view does not name lease ' + lease.id
      + "'s worktree, so " + tool + ' cannot be confined and is refused.')
  if (lease.workspace_path === null) return allow('unconfined')
  const root = pathKey(lease.workspace_path)
  if (root === null) return deny('worktree-invalid', "lease " + lease.id + "'s worktree path is not absolute; " + tool + ' is refused.')
  if (fork.cwd !== null && pathKey(fork.cwd) !== root)
    return deny('worktree-mismatch', who + ' was started outside lease ' + lease.id + "'s worktree; " + tool + ' is refused.')
  const targets = writeTargets(tool, input, root)
  if (!targets.length) return deny('target-unknown', tool + ' names no file path the gate can check; it is refused.')
  for (const target of targets) {
    const key = pathKey(target)
    if (key === null) return deny('target-relative', tool + ' needs an absolute path inside the lease worktree.')
    if (!within(root, key))
      return deny('outside-worktree', tool + ' targets a path outside lease ' + lease.id + "'s worktree; it is refused.")
  }
  return allow('confined')
}
// Classify, decide and log one call. Returns null for an agent the gate leaves alone.
async function judge($, tool, input, agentId, callId) {
  const started = Date.now()
  const fork = await classify($, agentId)
  if (fork.kind !== 'work') return null
  const result = await decide($, tool, input, agentId, fork)
  note($, 'decision agent=' + shortId(agentId) + ' tool=' + tool + ' verdict=' + result.verdict + ' rule=' + result.rule
    + ' recognized=' + fork.recognized + ' ms=' + (Date.now() - started) + (callId ? ' call=' + shortId(callId) : ''))
  return result
}
function argsOf(e) {
  const { tool, tool_use_id, agentId, ...input } = e
  return input
}

// Per-priority Work-fork and SSE side-fork models (user requirement
// 2026-11-14: a model per P0-P5 and for the side actor). turn.step fires for
// every model request of a ciel-work fork, the first one included, so the
// model is named before the request leaves. A fork's lease is found by its
// bound host id or, before the bind lands, by its spawn name (the dispatch
// name ciel-work reserved before spawning). The status view carries each
// slot's router alias (models) and direct-gateway id (upstream); the request
// gets the form main's own request uses. null keeps the engine's model.
const forkModels = new Map()
function slotNames(state, slot) {
  const alias = state?.fork_models?.models?.[slot]
  if (typeof alias !== 'string' || !alias) return null
  const upstream = state.fork_models.upstream?.[slot]
  return { alias, upstream: typeof upstream === 'string' && upstream ? upstream : alias }
}
async function forkModel($, agentId) {
  const fork = await classify($, agentId)
  if (fork.kind !== 'work' && fork.kind !== 'side') return null
  const cached = forkModels.get(agentId)
  if (cached && cached.until > Date.now()) return cached.names
  const statusArgs = fork.kind === 'side'
    ? { projection: 'gate', models_only: true }
    : { projection: 'gate', for_model: true, host_id: agentId }
  const state = await routerJson($, 'work_parallel_status', statusArgs)
  if (!state?.fork_models || typeof state.fork_models.models !== 'object' || state.fork_models.models === null) return null
  let names = null
  // A side retry after the side model failed: main's model (the main model
  // setting when one is chosen; otherwise null keeps the inherited engine
  // model, which is main's own).
  if (fork.kind === 'side' && fork.mainModel) {
    names = mainNames(state)
    note($, 'fork-model-side-main agent=' + shortId(agentId))
  } else if (fork.kind === 'side') names = slotNames(state, 'side')
  else {
    const leases = Object.values(state.leases || {})
    // A rework launch name embeds its replacement lease id (reworkForkLeaseId).
    const rework = fork.name ? /^work-rework-([\s\S]+)-a[1-9]\d{0,2}$/.exec(fork.name)?.[1] : null
    const lease = leases.find(row => row?.host_id === agentId)
      || (fork.name ? leases.find(row => row?.dispatch_name === fork.name || row?.agent_name === fork.name) : null)
      || (rework ? leases.find(row => row?.id === rework) || null : null)
    const priority = lease ? (lease.effective_priority || lease.priority) : null
    names = typeof priority === 'string' && /^P[0-5]$/.test(priority) ? slotNames(state, priority) : null
    // Approval workflow escalation: main moved this step to another model.
    const override = lease ? state.fork_models.overrides?.[lease.id] : null
    if (override && typeof override.alias === 'string' && override.alias)
      names = { alias: override.alias, upstream: typeof override.upstream === 'string' && override.upstream ? override.upstream : override.alias }
    if (!names) note($, 'fork-model-inherit agent=' + shortId(agentId) + ' lease=' + (lease ? 'found' : 'missing')
      + ' priority=' + (typeof priority === 'string' ? priority.slice(0, 2) : '-'))
    // Without a lease yet the answer is not cached, so the next step looks again.
    if (!lease) return names
  }
  remember(forkModels, agentId, { names, until: Date.now() + 30000 }, 400)
  return names
}
// Main session model (user requirement 2026-11-15): one choice per workspace
// and provider (work_main_model), applied to main's own requests, never to a
// fork. It is read at the first step of each main turn and kept for every step
// of that turn, so a remote change never switches the model inside a tool loop.
// A turn first seen past its first step (gate loaded mid-turn) and a turn whose
// read failed keep the engine's model.
const mainTurns = new Map()
function mainNames(state) {
  const view = state?.fork_models?.main
  if (!view || typeof view.model !== 'string' || !view.model) return null
  return { alias: view.model, upstream: typeof view.upstream === 'string' && view.upstream ? view.upstream : view.model }
}
async function mainModel($, e) {
  const turn = typeof e.turnId === 'string' && e.turnId ? e.turnId : null
  if (turn && mainTurns.has(turn)) return mainTurns.get(turn)
  let names = null
  try {
    if (e.index === 0) names = mainNames(await routerJson($, 'work_parallel_status', { projection: 'gate', models_only: true }))
  } finally {
    if (turn) remember(mainTurns, turn, names, 50)
  }
  return names
}
function requestModel(names, current) {
  if (!names) return null
  // Router launches send the ciel-runtime-<provider>- alias; direct gateway and
  // plain-id launches send the upstream id.
  return typeof current === 'string' && current.startsWith('ciel-runtime-') ? names.alias : names.upstream
}

export function register(on) {
  on('agent.spawn', async ($, e, next) => {
    // Parity row 10: a Work fork never starts agents.
    if (typeof e.parentAgentId === 'string' && e.parentAgentId) {
      const parent = await classify($, e.parentAgentId)
      if (parent.kind === 'work')
        return { deny: DENY + forkLabel(parent) + ' cannot start agents; Work dispatch is the coordinator\'s.' }
    }
    const result = await next(e)
    if (next.origin?.plugin === OWNER && typeof result?.agentId === 'string' && result.agentId)
      remember(spawns, result.agentId, spawnShape(e), 400)
    return result
  }).catch(($, e, next) => next.called ? next(e)
    : workForks.has(e.parentAgentId) ? { deny: DENY + 'the spawn check failed; a Work fork cannot start agents.' } : next(e))

  on('turn.step', async function* ($, e, next) {
    if (typeof e.agentId !== 'string' || !e.agentId) {
      let model = null
      try { model = requestModel(await mainModel($, e), e.model) } catch (error) {
        note($, 'main-model-unreadable ' + (error?.gateFault ? error.message : 'read failed'))
      }
      if (!model || model === e.model) return yield* next(e)
      note($, 'main-model step=' + e.index + ' model=' + model)
      return yield* next({ ...e, model })
    }
    let model = null
    try { model = requestModel(await forkModel($, e.agentId), e.model) } catch (error) {
      note($, 'fork-model-unreadable agent=' + shortId(e.agentId) + ' ' + (error?.gateFault ? error.message : 'read failed'))
    }
    if (!model || model === e.model) return yield* next(e)
    note($, 'fork-model agent=' + shortId(e.agentId) + ' step=' + e.index + ' model=' + model)
    return yield* next({ ...e, model })
  }).catch(async function* ($, e, next) { return yield* next(e) })

  on('tool.call', async ($, e, next) => {
    if (typeof e.agentId !== 'string' || !e.agentId) return next(e)
    // A side fork reads the Work board on demand (user 2026-11-18). ciel-work's
    // own hooks never run in forks it spawned, so the read carried no session
    // and was refused; the gate names main's conversation here and drops any
    // model-supplied identity or host field. A list read shows every goal.
    if (e.tool === PREFIX + 'work_status') {
      const fork = await classify($, e.agentId)
      // A Work fork reads its own item the same way (Andy 2026-11-19: every P0
      // fork's work_status answered "conversation identity is unavailable").
      // Only a side fork's list read is widened to every goal.
      if (fork.kind === 'side' || fork.kind === 'work') {
        const id = await $.session.id()
        if (typeof id === 'string' && /^[A-Za-z0-9_-]{1,200}$/.test(id)) {
          const { session_id, _host, ...rest } = e
          return next({ ...rest, session_id: 'claude:' + id,
            ...(fork.kind === 'side' && !rest.root_id ? { all_goals: true } : {}) })
        }
      }
    }
    const input = argsOf(e)
    const result = await judge($, e.tool, input, e.agentId, e.tool_use_id)
    if (!result) return next(e)
    if (typeof e.tool_use_id === 'string' && e.tool_use_id) remember(decided, e.tool_use_id, result, 200)
    if (result.verdict === 'deny') return { deny: result.reason }
    // A Work fork never carries a host attestation of its own.
    if (ROUTER_ALLOWED.has(e.tool) && Object.prototype.hasOwnProperty.call(e, '_host')) {
      const { _host, ...rest } = e
      note($, 'host-field-dropped agent=' + shortId(e.agentId) + ' tool=' + e.tool)
      return next(rest)
    }
    return next(e)
  }).catch(($, e, next) => next.called ? next(e)
    : workForks.has(e.agentId) ? { deny: DENY + 'the gate check failed; this Work fork call is refused.' } : next(e))

  on('tool.check', async ($, e, next) => {
    // Presence probe (G5): a query from ciel-work, never a real call. The
    // engine pins ToolCheckResult.plugin on an ask a hook answered.
    if (!e.tool_use_id && (typeof e.agentId !== 'string' || !e.agentId) && next.origin?.plugin === OWNER
      && typeof e.input?.[PROBE_KEY] === 'string' && PROBE_NONCE.test(e.input[PROBE_KEY]))
      return { decision: 'ask', reason: GATE + ' ' + GATE_VERSION + ' ' + e.input[PROBE_KEY] }
    if (typeof e.agentId !== 'string' || !e.agentId) return next(e)
    let result = typeof e.tool_use_id === 'string' ? decided.get(e.tool_use_id) || null : null
    if (result) decided.delete(e.tool_use_id)
    else result = await judge($, e.tool, e.input, e.agentId, e.tool_use_id)
    if (!result) return next(e)
    if (result.verdict === 'deny') return { decision: 'deny', reason: result.reason }
    return next(e)
  }).catch(($, e, next) => workForks.has(e.agentId)
    ? { decision: 'deny', reason: DENY + 'the gate check failed; this Work fork call is refused.' } : next(e))
}

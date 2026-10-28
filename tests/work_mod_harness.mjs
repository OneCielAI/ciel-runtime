// Drives the packaged ciel-work register.js with a scripted host: no Claude Code,
// no router. Each scenario prints one JSON line describing the gate decisions.
import { register } from './register.js'

const handlers = {}
register((name, filter, handler) => { handlers[name] = typeof filter === 'function' ? filter : handler })

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const snapshot = (phase = 'executing', version = 5) => ({ enabled: true, phase, approval: 'auto', version,
  done: 0, total: 1, active_root: { id: 'n1', title: 'goal', status: 'in_progress' }, auto_continue: true,
  continuation: { actionable: 0, waiting: [] }, queue: [] })
const text = value => ({ content: [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value) }] })

function host(respond) {
  const state = { session: 'S1', calls: 0, logs: [] }
  const $ = {
    session: { id: async () => state.session },
    mcp: { call: async (server, tool, args) => { state.calls++; return respond(tool, args, state) } },
    ui: { invalidate() {}, toast() {}, log(t) { state.logs.push(t) }, open: async () => {}, ask: async () => 'Approve' },
    clock: { after() { return { cancel() {} } } },
    command: { register: async () => {} },
  }
  return { $, state }
}
function next(value) {
  const fn = async () => value
  fn.origin = { plugin: 'engine' }
  return fn
}
const check = ($, agentId) => handlers['tool.check']($, { tool: 'Edit', agentId,
  input: { file_path: 'C:/w/a.py', old_string: 'a', new_string: 'b' } }, next({ decision: 'allow' }))
const call = ($, agentId) => handlers['tool.call']($, { tool: 'Edit', agentId,
  file_path: 'C:/w/a.py', old_string: 'a', new_string: 'b' }, next({ ran: true }))
const verdict = r => r?.decision === 'deny' ? 'deny: ' + r.reason : r?.deny ? 'deny: ' + r.deny : 'allow'

const scenarios = {
  // Two agents gate their own Edit while the router answers the earlier read last.
  async 'concurrent-check'() {
    const delays = [60, 10]
    const { $ } = host(async () => { await sleep(delays.shift() ?? 0); return text(snapshot()) })
    const first = check($, 'agent-a')
    await sleep(10)
    const second = check($, 'agent-b')
    return { first: verdict(await first), second: verdict(await second) }
  },
  async 'concurrent-call'() {
    const delays = [60, 10]
    const { $ } = host(async () => { await sleep(delays.shift() ?? 0); return text(snapshot()) })
    const first = call($, 'agent-a')
    await sleep(10)
    const second = call($, undefined)
    return { first: verdict(await first), second: verdict(await second) }
  },
  // A main-thread turn starting while a subagent's gate read is in flight.
  async 'turn-start-during-check'() {
    const { $ } = host(async () => { await sleep(40); return text(snapshot()) })
    const pending = check($, 'agent-a')
    await sleep(10)
    await handlers['turn.start']($, {}, next(undefined))
    return { first: verdict(await pending) }
  },
  async 'concurrent-planning'() {
    const delays = [60, 10]
    const { $ } = host(async () => { await sleep(delays.shift() ?? 0); return text(snapshot('planning')) })
    const first = check($, 'agent-a')
    await sleep(10)
    const second = check($, 'agent-b')
    return { first: verdict(await first), second: verdict(await second) }
  },
  // Each gate stands on its own read: the failed one still denies, the verified one allows.
  async 'concurrent-mixed'() {
    const plan = [[60, null], [10, snapshot()]]
    const { $ } = host(async () => {
      const [delay, value] = plan.shift(); await sleep(delay)
      return value ? text(value) : { isError: true, content: [{ type: 'text', text: 'store unreadable' }] }
    })
    const first = check($, 'agent-a')
    await sleep(10)
    const second = check($, 'agent-b')
    return { first: verdict(await first), second: verdict(await second) }
  },
  async 'execute-during-edit'() {
    const delays = [60, 10]
    const { $ } = host(async (tool) => {
      if (tool === 'work_execute') return text('ok')
      await sleep(delays.shift() ?? 0); return text(snapshot('planning'))
    })
    const execute = handlers['tool.call']($, { tool: 'mcp__ciel-runtime-router__work_execute' }, next({ ran: true }))
    await sleep(10)
    const edit = check($, 'agent-b')
    return { first: verdict(await execute), second: verdict(await edit) }
  },
  async 'planning'() {
    const { $ } = host(async () => text(snapshot('planning')))
    return { first: verdict(await check($, 'agent-a')) }
  },
  async 'session-end-during-check'() {
    const { $ } = host(async () => { await sleep(40); return text(snapshot()) })
    const pending = check($, 'agent-a')
    await sleep(10)
    await handlers['session.end']($, {}, next(undefined))
    return { first: verdict(await pending) }
  },
  async 'stale-snapshot'() {
    const { $ } = host(async () => text(snapshot('executing', 4)))
    const { $: newer } = host(async () => text(snapshot('planning', 5)))
    await check(newer, 'agent-b')
    return { first: verdict(await check($, 'agent-a')) }
  },
  async 'router-timeout'() {
    const { $, state } = host(async () => { throw new Error('Timeout: PRIVATE-TOKEN') })
    return { first: verdict(await check($, 'agent-a')), logs: state.logs }
  },
  async 'router-error'() {
    const { $ } = host(async () => ({ isError: true, content: [{ type: 'text', text: 'boom' }] }))
    return { first: verdict(await check($, 'agent-a')) }
  },
  async 'router-throws'() {
    const { $ } = host(async () => { throw new Error('no connected MCP tool') })
    return { first: verdict(await call($, 'agent-a')) }
  },
  async 'invalid-snapshot'() {
    const { $ } = host(async () => text({ enabled: true, phase: 'executing' }))
    return { first: verdict(await check($, 'agent-a')) }
  },
  async 'not-json'() {
    const { $ } = host(async () => text('Work phase: executing'))
    return { first: verdict(await check($, 'agent-a')) }
  },
  // The conversation changes while the read is in flight: the gate must not use it.
  async 'identity-change'() {
    const { $, state } = host(async () => { state.session = 'S2'; return text(snapshot()) })
    return { first: verdict(await check($, 'agent-a')) }
  },
  async 'disabled'() {
    const { $ } = host(async () => text({ enabled: false }))
    return { first: verdict(await check($, 'agent-a')) }
  },
  // A Stop whose read is overtaken by a new main turn must not continue the old turn.
  async 'stop-superseded'() {
    const ready = { ...snapshot(), continuation: { actionable: 1, next: { id: 'n2', title: 'x', root_id: 'n1' }, waiting: [] } }
    const { $ } = host(async () => { await sleep(40); return text(ready) })
    const pending = handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    await sleep(10)
    await handlers['turn.start']($, {}, next(undefined))
    const result = await pending
    return { block: Boolean(result?.block) }
  },
  // A UI/inspector refresh is not a new model turn and must not cancel Stop.
  async 'stop-display-refresh'() {
    const ready = { ...snapshot(), continuation: { actionable: 1, next: { id: 'n3', title: 'queued', root_id: 'n2', phase: 'executing' }, waiting: [] } }
    const delays = [60, 5]
    const { $, state } = host(async () => { await sleep(delays.shift() ?? 0); return text(ready) })
    const pending = handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    await sleep(10)
    await handlers['command.run']($, { args: 'status' })
    const result = await pending
    return { block: Boolean(result?.block), text: result?.block || '', logs: state.logs }
  },
  async 'stop-session-end'() {
    const ready = { ...snapshot(), continuation: { actionable: 1, next: { id: 'n2', title: 'x', root_id: 'n1' }, waiting: [] } }
    const { $ } = host(async () => { await sleep(40); return text(ready) })
    const pending = handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    await sleep(10)
    await handlers['session.end']($, {}, next(undefined))
    return { block: Boolean((await pending)?.block) }
  },
  async 'stop-continues'() {
    const ready = { ...snapshot(), continuation: { actionable: 1, next: { id: 'n2', title: 'x', root_id: 'n1' }, waiting: [] } }
    const { $ } = host(async () => text(ready))
    const result = await handlers['classic.Stop']($, { session_id: 'S1' }, next({}))
    return { block: Boolean(result?.block) }
  },
}

const name = process.argv[2]
process.stdout.write(JSON.stringify({ scenario: name, ...(await scenarios[name]()) }) + '\n')

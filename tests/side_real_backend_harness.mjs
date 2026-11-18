// side-intake.js against the REAL side backend (side_real_backend_server.py:
// real MCP dispatch, session Work store and side inbox in a temporary
// directory). The engine side is the same model side_delivery_harness.mjs uses
// (owned probes delivery-4f4c90b8, prompt-gate-0dd78bf3). Two imports are two
// module instances; each instance here has its own $.state and conversation,
// which is two claude.exe processes on one session. argv[2] is mode[:variant].
import { spawn } from 'node:child_process'
import * as first from './side-intake.js'
const second = await import('./side-intake.js?second')
const [mode, variant] = String(process.argv[2] || '').split(':')

const bridge = spawn(process.env.PYTHON || 'python', ['-u', 'side_real_backend_server.py'],
  { cwd:process.cwd(), env:process.env, stdio:['pipe', 'pipe', 'inherit'] })
let buffer = ''; const waits = []
bridge.stdout.setEncoding('utf8')
bridge.stdout.on('data', chunk => {
  buffer += chunk
  while (buffer.includes('\n')) {
    const i = buffer.indexOf('\n'), line = buffer.slice(0, i); buffer = buffer.slice(i + 1)
    waits.shift()?.(JSON.parse(line))
  }
})
const rpc = (action, data = {}) => new Promise((resolve, reject) => {
  waits.push(value => value.ok ? resolve(value.value) : reject(Object.assign(Error(value.error), { trace:value.trace })))
  bridge.stdin.write(JSON.stringify({ action, ...data }) + '\n')
})
const tick = (ms = 5) => new Promise(resolve => setTimeout(resolve, ms))
// The module's own clock (its hook-delay grace for "a step carried the row").
const realNow = Date.now.bind(Date)
let skew = 0
Date.now = () => realNow() + skew
const later = (ms = 2000) => { skew += ms }

const init = await rpc('init')
const WAKE = 'Ciel side event requires main action: {"event_id":"'
// What the router answered and what the mods wrote to $.state, for item 5.
const seen = { wakeProtocols:new Set(), stateKeys:new Set(), calls:[], backendCalls:0 }
// serial: every router call waits its turn and the backend clock moves `latency`
// seconds before it is handled (one slow, single-threaded router).
const router = { serial:false, latency:0, queue:Promise.resolve(), hold:null }
function conversation() {
  const C = { rows:[], carried:0, submits:[], appends:[], requests:[], turns:0 }
  C.append = async a => { const text = a.message.content[0].text; C.appends.push(text); C.rows.push(text); return { message:a.message, uuid:'uuid-' + C.appends.length } }
  C.submit = async a => { C.submits.push(a.text); C.rows.push(a.text); C.turns++; return { text:a.text } }
  C.request = () => { const now = C.rows.slice(C.carried); C.carried = C.rows.length; C.requests.push(now); return now }
  C.view = () => ({ payloadAppends:C.appends.filter(text => text.includes(WAKE)).length,
    payloadPrompts:C.submits.filter(text => text.includes(WAKE)).length,
    wakePrompts:C.submits.filter(text => !text.includes(WAKE)).length,
    requests:C.requests.map(rows => rows.filter(text => text.includes(WAKE)).length) })
  return C
}
function hostState() {
  const map = new Map()
  return { map, api:{
    get:async ref => { await tick(1); const held = map.get(ref.plugin + '/' + ref.key)
      return { value:held ? structuredClone(held.value) : undefined, version:held?.version || 0 } },
    set:async (ref, value, options = {}) => { await tick(1); seen.stateKeys.add(ref.key)
      const name = ref.plugin + '/' + ref.key, held = map.get(name) || { version:0 }
      if (options.ifVersion !== undefined && options.ifVersion !== held.version) return { isSet:false, version:held.version }
      map.set(name, { value:structuredClone(value), version:held.version + 1 }); return { isSet:true, version:held.version + 1 } } } }
}
function instance(label, mod, sid = 'parent') {
  const I = { label, mod, busy:false, C:conversation(), state:hostState(), claim:null, logs:[], wakeCalls:[], gate:null }
  mod.sideMainBusy(() => I.busy)
  const forward = async (name, args) => {
    if (router.serial) {
      const run = router.queue.then(async () => { if (router.latency) await rpc('advance', { seconds:router.latency }); return rpc('tool', { tool:name, args }) })
      router.queue = run.catch(() => {})
      return run
    }
    return rpc('tool', { tool:name, args })
  }
  I.$ = { session:{ id:async () => sid, model:async () => 'fixture-main',
      messages:async () => I.C.rows.map(text => ({role:'user',text,toolUses:[]})), append:I.C.append,
      send:async () => ({ isDelivered:true }) },
    prompt:{ submit:I.C.submit }, state:I.state.api, clock:{ after:() => ({ cancel() {} }) },
    mcp:{ connect:async () => ({}), call:async (server, name, args) => {
      seen.backendCalls++
      if (name === 'work_side_wake') {
        I.wakeCalls.push(args.state)
        // A scenario may hold one kind of wake call of this instance.
        if (I.gate?.state === args.state) { const gate = I.gate; I.gate = null; gate.arrived(); await gate.release }
        // Or let the router grant it and then die before using the answer
        // (process crash after the lease, before prompt.submit).
        if (I.dieAfter === args.state) {
          I.dieAfter = null; await forward(name, args); seen.calls.push(label + ':' + args.state + ':died')
          return new Promise(() => {})
        }
        // Or lose one before it reaches the router (a transport failure).
        if (I.failOnce === args.state) {
          I.failOnce = null; seen.calls.push(label + ':' + args.state + ':lost')
          throw new Error('router transport failure')
        }
      }
      const answer = await forward(name, args)
      if (name === 'work_side_wake') {
        seen.calls.push(label + ':' + args.state + (answer.error ? ':refused' : ''))
        if (!answer.error) { try { seen.wakeProtocols.add(JSON.parse(answer.text).protocol ?? 'none') } catch {} }
      }
      if (name === 'work_side_claim' && !answer.error) { const claim = JSON.parse(answer.text).claim; if (claim) I.claim = claim }
      return { isError:answer.error, content:[{ type:'text', text:answer.text }] }
    } },
    tool:{ list:async () => [], call:async () => ({}) }, agent:{ list:async () => [], spawn:async () => ({ agentId:'actor-' + label }) },
    env:{ get:async key => key === 'CIEL_RUNTIME_WORK_PARALLEL_HOST_TOKEN' ? init.token : undefined },
    ui:{ invalidate:() => {}, log:text => I.logs.push(String(text)), status:() => {} } }
  I.poll = () => mod.pollSide(I.$, 'claude:' + sid)
  I.complete = decision => mod.completeSide(I.$, { agentId:'actor-' + label, reason:'answer',
    answer:JSON.stringify({ decision, expected_version:I.claim.workload.version }) })
  I.hold = state => { const gate = { state }; gate.reached = new Promise(done => { gate.arrived = done })
    gate.release = new Promise(done => { gate.open = done }); I.gate = gate; return gate }
  // turn.start carries the entered prompt's text; the step's request is pinned
  // when turn.step is raised (append-views-54579a2e).
  I.turnStart = async (text = I.C.submits.at(-1) || '') => {
    I.busy = true; mod.sidePromptTurnStart(); await mod.sideMainTurnStart?.(I.$, text) }
  I.step = async () => { const request = I.C.request(); await mod.sideMainStep?.(I.$); return request }
  I.turnComplete = () => { I.busy = false; mod.sidePromptTurnComplete(I.$) }
  return I
}
const settle = async (...conversations) => {
  for (let stable = 0, last = ''; stable < 12;) {
    await tick()
    const now = seen.backendCalls + ':' + conversations.map(C => C.rows.length).join(':')
    stable = now === last ? stable + 1 : 0; last = now
  }
}
const proof = () => ({ wakeProtocols:[...seen.wakeProtocols], stateKeys:[...seen.stateKeys].sort() })
// One side event whose decision asks main to act: resume (offered by the claim)
// or time_limit (scheduled at completion).
async function decided(I, kind) {
  const event = await rpc('receive', { text:kind === 'time_limit' ? 'report is due within 4 hours' : 'approval 7 granted', key:'event-' + kind })
  await I.mod.startSide(I.$)
  await I.poll()
  await I.complete({ action:'update', kind:'request', item_id:init.gate, summary:'reported', reply:'noted', note:'n',
    ...(kind === 'time_limit' ? { time_limit:{ text:'within 4 hours', evidence:'report is due within 4 hours' } } : {}) })
  return event
}

const cases = {
  // The whole delivery on the real backend: election, append, receipt, read.
  async wake() {
    const A = instance('A', first)
    if (variant === 'busy') { await A.turnStart(); await A.step() }
    const event = await decided(A, 'time_limit')
    await settle(A.C)
    const delivered = { ...A.C.view(), backend:await rpc('inspect', { event }) }
    // Busy: the first step after the append only fences it; the turn's next
    // request (a tool result or a Stop continuation) is causal proof.
    if (variant === 'busy') { await A.step(); await A.step() } else if (A.C.turns) { await A.turnStart(); await A.step(); A.turnComplete() }
    await settle(A.C)
    return { delivered, after:{ ...A.C.view(), backend:await rpc('inspect', { event }) }, wakeCalls:A.wakeCalls, ...proof() }
  },
  // Conversation change before read. Idle main A gets the append and one wake
  // prompt, which is queued but whose turn has not stepped yet (a prompt can
  // wait seconds before it enters: prompt-registry-e2bed500). The session then
  // continues in a new conversation (native lineage: same Work board, new
  // identity) whose transcript lacks A's row. Queued is not read, so the new
  // conversation gets the payload re-appended exactly once and one wake prompt.
  async 'continue-before-turn'() {
    const A = instance('A', first)
    const event = await decided(A, 'time_limit')
    await settle(A.C)
    const inA = { ...A.C.view(), backend:await rpc('inspect', { event }) }
    const lineage = await rpc('continue', { identity:'claude:parent2' })
    const B = instance('B', second, 'parent2')
    await second.startSide(B.$)
    for (let i = 0; i < 3; i++) { await B.poll(); await settle(A.C, B.C) }
    const inB = { ...B.C.view(), backend:await rpc('inspect', { event }) }
    if (B.C.turns) { await B.turnStart(); await B.step(); B.turnComplete() }
    await settle(A.C, B.C)
    for (let i = 0; i < 2; i++) { await B.poll(); await settle(A.C, B.C) }
    return { lineage, inA, inB, after:{ ...B.C.view(), backend:await rpc('inspect', { event }) },
      calls:seen.calls, ...proof() }
  },
  // Liveness: A holds the time-limit event's wake-prompt lease and dies before
  // submitting; main is idle and nobody types. B (same conversation) polls.
  // Within the lease window nothing may submit; after it, exactly one wake.
  async 'prompt-holder-dies'() {
    const A = instance('A', first), B = instance('B', second)
    A.dieAfter = 'prompt_prepare'
    const event = await decided(A, 'time_limit')
    await settle(A.C)
    await second.startSide(B.$)
    for (let i = 0; i < 3; i++) { await B.poll(); await settle(A.C, B.C) }
    const inWindow = { A:A.C.view(), B:B.C.view(), backend:await rpc('inspect', { event }) }
    await rpc('advance', { seconds:61 })
    for (let i = 0; i < 3; i++) { await B.poll(); await settle(A.C, B.C) }
    return { inWindow, A:A.C.view(), B:B.C.view(), backend:await rpc('inspect', { event }),
      promptCalls:seen.calls.filter(call => /:prompt_/.test(call)) }
  },
  // Prompt lease under a lost router call, with a second process on the same
  // conversation polling throughout. variant prepare: A's first prompt_prepare
  // is lost, so A submits nothing then and asks again later. variant record:
  // A's prompt_queued record is lost after its prompt was submitted, so the
  // lease stays submitting and no process may add a second prompt.
  async 'prompt-failure'() {
    const A = instance('A', first), B = instance('B', second)
    A.failOnce = variant === 'record' ? 'prompt_queued' : 'prompt_prepare'
    const event = await decided(A, 'time_limit')
    await settle(A.C)
    const afterFirst = { ...A.C.view(), backend:await rpc('inspect', { event }) }
    await second.startSide(B.$)
    for (let i = 0; i < 3; i++) { await A.poll(); await B.poll(); await settle(A.C, B.C) }
    return { afterFirst, A:A.C.view(), B:B.C.view(), backend:await rpc('inspect', { event }),
      promptCalls:seen.calls.filter(call => /:prompt_/.test(call)), ...proof() }
  },
  // Item 5: two claude.exe on one session against the CURRENT backend. Both
  // hold their prepare until both arrived, then race the real election.
  async 'two-process'() {
    const A = instance('A', first), B = instance('B', second)
    const event = await decided(A, 'resume')
    await settle(A.C)
    await second.startSide(B.$)
    const before = await rpc('inspect', { event })
    const ga = A.hold('prepare'), gb = B.hold('prepare')
    const polls = [A.poll(), B.poll()]
    await Promise.all([ga.reached, gb.reached])
    ga.open(); gb.open()
    await Promise.all(polls)
    for (let i = 0; i < 3; i++) { await A.poll(); await B.poll() }
    await settle(A.C, B.C)
    return { before:before.wake.state, A:A.C.view(), B:B.C.view(), backend:await rpc('inspect', { event }),
      calls:seen.calls, promptAttempts:seen.calls.filter(call => call.includes(':prompt_prepare')).length, ...proof() }
  },
  // Item 6: the intent lease. variant adversarial: two live owners, and every
  // start is held more than the 5 s lease while the other owner prepares.
  // single-slow: one owner whose start is 60 s late. serial-slow: two owners
  // behind one router that takes 7 s for every call, in arrival order.
  async lease() {
    const A = instance('A', first), B = instance('B', second)
    const event = await decided(A, 'resume')
    await settle(A.C)
    await second.startSide(B.$)
    // Payload deliveries, whichever channel carried them.
    const appended = () => [A, B].reduce((sum, I) => sum + I.C.view().payloadAppends + I.C.view().payloadPrompts, 0)
    if (variant === 'single-slow') {
      const gate = A.hold('start')
      const poll = A.poll()
      await gate.reached
      await rpc('advance', { seconds:60 })
      gate.open(); await poll; await settle(A.C)
      return { appended:appended(), backend:await rpc('inspect', { event }), calls:seen.calls }
    }
    if (variant === 'serial-slow') {
      router.serial = true; router.latency = 7
      let rounds = 0
      for (; rounds < 6 && !appended(); rounds++) { await Promise.all([A.poll(), B.poll()]); await settle(A.C, B.C) }
      router.serial = false
      return { rounds, appended:appended(), backend:await rpc('inspect', { event }), calls:seen.calls }
    }
    // adversarial
    let holder = A, gate = A.hold('start'), poll = A.poll()
    await gate.reached
    const rounds = 6
    for (let round = 0; round < rounds; round++) {
      const other = holder === A ? B : A
      await rpc('advance', { seconds:6 })
      const nextGate = other.hold('start'), nextPoll = other.poll()
      await nextGate.reached
      gate.open(); await poll
      holder = other; gate = nextGate; poll = nextPoll
    }
    const whileAdversarial = { appended:appended(), backend:await rpc('inspect', { event }) }
    // The interleaving stops: the last owner's start is no longer overtaken.
    gate.open(); await poll; await settle(A.C, B.C)
    return { rounds, whileAdversarial, appended:appended(), backend:await rpc('inspect', { event }),
      refusedStarts:seen.calls.filter(call => call.endsWith(':start:refused')).length, calls:seen.calls }
  },
}
let result
try { result = await cases[mode]() } finally { bridge.stdin.end() }
console.log(JSON.stringify({ mode, variant:variant || null, ...result }))
process.exit(0)

// Feeds the host raw wake secrets on the authenticated claim path (main_wakes,
// handoff_replies) and checks that no prompt, append, pane log, status line or
// side fork prompt the host writes contains them. The claim's handoff_context
// is router-redacted; a raw value there would come from a router bug, so this
// harness supplies the redacted shape the router now returns.
import {startSide,pollSide,completeSide} from './side-intake.js'
const ATTEMPT = 'c'.repeat(31) + '7', NONCE = 'router-side-nonce-9f9f9f', RECEIPT = 'receipt-uuid-5151'
// A real-shaped side inbox claim generation (32-hex); host-held, never model data.
const GENERATION = '9'.repeat(31) + '3'
const STARTED = 'd'.repeat(64), FRESH = 'f'.repeat(64), ANSWERED = 'a'.repeat(64)
const sinks = {prompts:[], appends:[], logs:[], status:[], spawns:[]}
let claimed = false
const raw = {state:'submitting', phase:'started', attempt_id:ATTEMPT, channel:'prompt', revision:3}
// The engine's host-held $.state (version + ifVersion compare-and-set).
const hostStore = new Map()
const $ = {
  state:{get:async ref=>{const held=hostStore.get(ref.plugin+'/'+ref.key);return {value:held?structuredClone(held.value):undefined,version:held?.version||0}},set:async (ref,value,options={})=>{const name=ref.plugin+'/'+ref.key,held=hostStore.get(name)||{version:0};if(options.ifVersion!==undefined&&options.ifVersion!==held.version)return {isSet:false,version:held.version};hostStore.set(name,{value:structuredClone(value),version:held.version+1});return {isSet:true,version:held.version+1}}},
  session:{id:async () => 'parent', messages:async () => [],
    append:async a => { sinks.appends.push(JSON.stringify(a)); return {uuid:'u-1'} }},
  prompt:{submit:async a => { sinks.prompts.push(a.text); return {} }},
  mcp:{connect:async () => ({}), call:async (server, name, args) => {
    let value = {}
    if (name === 'work_side_claim') {
      const claim = claimed ? null : {receipt_id:'e'.repeat(64), generation:GENERATION, text:'event', event_key:'handoff:'+STARTED,
        lease_until:Date.now()/1000+120, source_read:null, route:{},
        workload:{version:1, truncated:false, nodes:[], queue_order:[]},
        handoff_context:[{event_id:STARTED, state:'requested', summary:'needs main',
          wake:{state:'submitting', phase:'started', revision:3, attempt_ref:'0123456789abcdef', delivery_uncertain:true}}]}
      claimed = true
      value = {claim, settings:{}, main_wakes:[
          {event_id:STARTED, state:'requested', item_id:'n1', summary:'needs main', wake:{...raw}},
          {event_id:FRESH, state:'requested', item_id:'n2', summary:'fresh', wake:{state:'pending', revision:0}}],
        handoff_replies:[{event_id:ANSWERED, state:'answered', answer:'ok',
          wake:{state:'acknowledged', attempt_id:ATTEMPT, owner_nonce:NONCE, receipt:RECEIPT}, receipt:null}]}
    }
    if (name === 'work_side_wake') {
      if (args.state === 'prepare') value = {protocol:2, state:'submitting', phase:'intent', attempt_id:'b'.repeat(32),
        owner_nonce:args.owner_nonce, channel:args.channel, revision:1}
      else if (args.state === 'start') value = {protocol:2, state:'submitting', phase:'started', attempt_id:'b'.repeat(32),
        owner_nonce:args.owner_nonce, channel:args.channel, revision:2}
      else value = {protocol:2, state:args.state}
    }
    if (name === 'work_side_resolve') value = {receipt:{receipt_id:'e'.repeat(64)}, result:{action:'answer', duplicate:false}}
    if (name === 'work_side_reply') value = {receipt:{}, arguments:null}
    return {content:[{type:'text', text:JSON.stringify(value)}]}
  }},
  tool:{call:async () => ({})},
  agent:{list:async () => [], spawn:async a => { sinks.spawns.push(JSON.stringify(a)); return {agentId:'actor'} }},
  env:{get:async () => 'x'.repeat(40)},
  ui:{invalidate:() => {}, log:text => sinks.logs.push(String(text)), status:text => sinks.status.push(JSON.stringify(text))},
  clock:{after:() => ({cancel(){}})},
}
await startSide($)
await pollSide($, 'claude:parent')
await completeSide($, {agentId:'actor', reason:'answer',
  answer:JSON.stringify({decision:{action:'answer', kind:'info', summary:'s', reply:'r'}, expected_version:1})})
await new Promise(resolve => setTimeout(resolve, 20))
console.log(JSON.stringify({sinks, secrets:[ATTEMPT, NONCE, RECEIPT, GENERATION, 'b'.repeat(32), 'ciel-side-wake']}))

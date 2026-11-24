// Drives side-intake.js release notifications with a scripted host. Each case
// resolves one side event whose committed result released recorded waits and
// prints who was told, and how.
import {startSide,pollSide,completeSide,sideMainBusy,sideParkedResume} from './side-intake.js'

const scenario = process.argv[2]
let busy = scenario !== 'main-idle' && scenario !== 'main-idle-repolled'
sideMainBusy(() => busy)
// Stage 3: register.js resumes a parked worker on its own lease first.
const resumeCalls = []
if (scenario === 'fork-parked-resumed' || scenario === 'fork-parked-refused' || scenario === 'group-parked-resumed')
  sideParkedResume(async ids => { resumeCalls.push(ids)
    return new Map(ids.map(id => [id, scenario === 'fork-parked-refused' ? 'the resume was not delivered: declined' : true])) })
const sent = [], appended = [], submitted = [], wakes = [], logs = []
// The backend grants the notice of one event receipt once (side_notice.py).
let noticeClaimed = false
const leases = {
  'fork-running': {L1:{id:'L1',item_id:'n2',host_id:'agent-run',state:'running',slot:true}},
  'fork-parked': {L2:{id:'L2',item_id:'n2',host_id:'agent-park',state:'waiting_input',slot:false}},
  'fork-parked-resumed': {L2:{id:'L2',item_id:'n2',host_id:'agent-park',state:'waiting_input',slot:false}},
  'fork-parked-refused': {L2:{id:'L2',item_id:'n2',host_id:'agent-park',state:'waiting_input',slot:false}},
  'fork-refuses': {L1:{id:'L1',item_id:'n2',host_id:'agent-gone',state:'running',slot:true}},
  // A released group wait (Andy 2026-11-23): the forks work on its leaves.
  'group-parked-resumed': {L3:{id:'L3',item_id:'n3',host_id:'agent-park',state:'waiting_input',slot:false}},
  'group-running': {L4:{id:'L4',item_id:'n3',host_id:'agent-run',state:'running',slot:true}},
  'group-leaf-still-waits': {L3:{id:'L3',item_id:'n3',host_id:'agent-park',state:'waiting_input',slot:false}},
}[scenario] || {}
const group = scenario.startsWith('group-')
const released = group ? [{id:'n2',waiting_on:'group approval'}] : [{id:'n3',waiting_on:'approval from Andy via Walkie'}]
let claimed = false
const timers = []
// The engine's host-held $.state (version + ifVersion compare-and-set).
const hostStore = new Map()
const $ = {
  state:{get:async ref=>{const held=hostStore.get(ref.plugin+'/'+ref.key);return {value:held?structuredClone(held.value):undefined,version:held?.version||0}},set:async (ref,value,options={})=>{const name=ref.plugin+'/'+ref.key,held=hostStore.get(name)||{version:0};if(options.ifVersion!==undefined&&options.ifVersion!==held.version)return {isSet:false,version:held.version};hostStore.set(name,{value:structuredClone(value),version:held.version+1});return {isSet:true,version:held.version+1}}},
  session:{model:async()=>'fixture-main',id:async()=>'parent',
    append:async args=>{appended.push(args.message.content[0].text); return {uuid:'append-'+appended.length}},
    send:async args=>{sent.push({to:args.to,text:args.text}); return args.to.agentId === 'agent-gone' ? {isDelivered:false,reason:'gone'} : {isDelivered:true}}},
  prompt:{submit:async args=>{submitted.push(args.text); return {}}},
  mcp:{connect:async()=>({}),call:async(server,name,args)=>{
    let value = {}
    if (name === 'work_side_claim') {
      const claim = claimed ? null : {receipt_id:'e'.repeat(64),generation:'10000000000000000000000000000005',text:'Andy: approved',event_key:'walkie:1',
        lease_until:Date.now()/1000+120,route:{},workload:{version:4,truncated:false,active_root:'n1',queue_order:[],
          nodes:group ? [{id:'n1',parent_id:null,title:'goal'},{id:'n2',parent_id:'n1',title:'deploy',waiting_on:'group approval'},
            {id:'n3',parent_id:'n2',title:'gate',...(scenario === 'group-leaf-still-waits' ? {waiting_on:'its own review'} : {})}]
          : [{id:'n1',parent_id:null,title:'goal'},{id:'n2',parent_id:'n1',title:'deploy'},{id:'n3',parent_id:'n2',title:'gate',waiting_on:released[0].waiting_on}]}}
      claimed = true
      value = {claim,settings:{model:'inherit',context_mode:'isolated'},handoff_replies:[],main_wakes:[]}
    }
    if (name === 'work_parallel_status') value = {version:1,policy:{},leases}
    if (name === 'work_side_resolve') value = {receipt:{receipt_id:args._host.receipt_id,stage:'processed'},
      result:{action:'update',item_id:released[0].id,summary:'Andy approved the deploy',duplicate:false,released_waits:released,handoff:'not_required'}}
    if (name === 'work_side_reply') value = {receipt:{receipt_id:args.receipt_id,stage:'processed'},arguments:null}
    if (name === 'work_side_wake') {
      wakes.push({event_id:args.event_id,state:args.state})
      if (args.state === 'notice_prepare') {
        value = noticeClaimed ? {protocol:2,state:'already_appended'} : {protocol:2,state:'claimed',attempt_id:'notice-1'}
        noticeClaimed = true
      } else value = {protocol:2,state:String(args.state).replace('notice_','')}
    }
    return {content:[{type:'text',text:JSON.stringify(value)}]}
  }},
  tool:{list:async()=>[],call:async()=>({text:'stopped'})},
  agent:{list:async()=>[],register:async()=>({}),spawn:async()=>({agentId:'actor1'})},
  env:{get:async key=>key==='CIEL_RUNTIME_DIRECT_GATEWAY_ACTIVE'?'0':'x'.repeat(40)},
  ui:{log:text=>logs.push(text),status:()=>{},invalidate:()=>{}},
  clock:{after:(ms,callback)=>{timers.push(callback);return {cancel(){}}}},
}
await startSide($)
await pollSide($,'claude:parent')
await completeSide($,{agentId:'actor1',reason:'answer',answer:JSON.stringify({understanding:'understood in my own words',decision:{action:'update',kind:'request',item_id:released[0].id,
  summary:'Andy approved the deploy',reply:'ok',note:'approved',release_waits:released},expected_version:4})})
// The release notice is delivered after completeSide returns. Wait until it has
// settled instead of a fixed delay: Linux CI under load read an empty list after
// 20 ms (5c4b85a, test_busy_main_reads_the_release_in_its_running_turn).
// Settled means a fork message was sent with no fallback, or the notice was
// recorded appended; the idle case also waits for its wake prompt.
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const recorded = () => wakes.some(call => call.state === 'notice_appended')
const settled = () => (scenario === 'fork-running' || scenario === 'group-running') ? sent.length > 0
  : (scenario === 'main-idle' || scenario === 'main-idle-repolled') ? recorded() && submitted.length > 0
  : recorded()
for (const end = Date.now() + 5000; Date.now() < end && !settled();) await sleep(5)
if (scenario === 'main-idle-repolled') {
  // Reproduces the prompt.submit hook window before turn.start sets mainBusy=true:
  // another pump tick calls wakeUnread while the first wake prompt is already queued.
  await pollSide($,'claude:parent')
}
// Anything that would arrive after settling (a duplicate notice or prompt) is
// still given the old window to show up.
await sleep(20)
console.log(JSON.stringify({sent,appended,submitted,wakes,logs,resumeCalls}))

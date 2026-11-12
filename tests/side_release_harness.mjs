// Drives side-intake.js release notifications with a scripted host. Each case
// resolves one side event whose committed result released recorded waits and
// prints who was told, and how.
import {startSide,pollSide,completeSide,sideMainBusy} from './side-intake.js'

const scenario = process.argv[2]
let busy = scenario !== 'main-idle'
sideMainBusy(() => busy)
const sent = [], appended = [], submitted = [], wakes = [], logs = []
const leases = {
  'fork-running': {L1:{id:'L1',item_id:'n2',host_id:'agent-run',state:'running',slot:true}},
  'fork-parked': {L2:{id:'L2',item_id:'n2',host_id:'agent-park',state:'waiting_input',slot:false}},
  'fork-refuses': {L1:{id:'L1',item_id:'n2',host_id:'agent-gone',state:'running',slot:true}},
}[scenario] || {}
const released = [{id:'n3',waiting_on:'approval from Andy via Walkie'}]
let claimed = false
const timers = []
// The engine's host-held $.state (version + ifVersion compare-and-set).
const hostStore = new Map()
const $ = {
  state:{get:async ref=>{const held=hostStore.get(ref.plugin+'/'+ref.key);return {value:held?structuredClone(held.value):undefined,version:held?.version||0}},set:async (ref,value,options={})=>{const name=ref.plugin+'/'+ref.key,held=hostStore.get(name)||{version:0};if(options.ifVersion!==undefined&&options.ifVersion!==held.version)return {isSet:false,version:held.version};hostStore.set(name,{value:structuredClone(value),version:held.version+1});return {isSet:true,version:held.version+1}}},
  session:{model:async()=>'fixture-main',id:async()=>'parent',
    append:async args=>{appended.push(args.message.content[0].text); return {}},
    send:async args=>{sent.push({to:args.to,text:args.text}); return args.to.agentId === 'agent-gone' ? {isDelivered:false,reason:'gone'} : {isDelivered:true}}},
  prompt:{submit:async args=>{submitted.push(args.text); return {}}},
  mcp:{connect:async()=>({}),call:async(server,name,args)=>{
    let value = {}
    if (name === 'work_side_claim') {
      const claim = claimed ? null : {receipt_id:'e'.repeat(64),generation:'g1',text:'Andy: approved',event_key:'walkie:1',
        lease_until:Date.now()/1000+120,route:{},workload:{version:4,truncated:false,active_root:'n1',queue_order:[],
          nodes:[{id:'n1',parent_id:null,title:'goal'},{id:'n2',parent_id:'n1',title:'deploy'},{id:'n3',parent_id:'n2',title:'gate',waiting_on:released[0].waiting_on}]}}
      claimed = true
      value = {claim,settings:{model:'inherit',context_mode:'isolated'},handoff_replies:[],main_wakes:[]}
    }
    if (name === 'work_parallel_status') value = {version:1,policy:{},leases}
    if (name === 'work_side_resolve') value = {receipt:{receipt_id:args._host.receipt_id,stage:'processed'},
      result:{action:'update',item_id:'n3',summary:'Andy approved the deploy',duplicate:false,released_waits:released,handoff:'not_required'}}
    if (name === 'work_side_reply') value = {receipt:{receipt_id:args.receipt_id,stage:'processed'},arguments:null}
    if (name === 'work_side_wake') wakes.push(args)
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
await completeSide($,{agentId:'actor1',reason:'answer',answer:JSON.stringify({decision:{action:'update',kind:'request',item_id:'n3',
  summary:'Andy approved the deploy',reply:'ok',note:'approved',release_waits:released},expected_version:4})})
await new Promise(resolve => setTimeout(resolve, 20))
console.log(JSON.stringify({sent,appended,submitted,wakes,logs}))

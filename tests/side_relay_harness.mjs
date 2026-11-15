// Drives side-intake.js live-item relay (user requirement 2026-11-15) with a
// scripted host. The side decides an update with relay: true; the host forwards
// the message to main and to the Work fork running that item, each of which
// decides whether it matters. Prints who was told, and how.
import {startSide,pollSide,completeSide,sideMainBusy} from './side-intake.js'

const scenario = process.argv[2]
let busy = scenario !== 'relay-idle'
sideMainBusy(() => busy)
const sent = [], appended = [], submitted = [], wakes = [], logs = [], resolved = []
let noticeClaimed = false
const leases = {
  'relay-running': {L1:{id:'L1',item_id:'n3',host_id:'agent-run',state:'running',slot:true},
                    L9:{id:'L9',item_id:'n9',host_id:'agent-other',state:'running',slot:true}},
  'relay-parked': {L2:{id:'L2',item_id:'n2',host_id:'agent-park',state:'waiting_input',slot:false}},
  'relay-other': {L9:{id:'L9',item_id:'n9',host_id:'agent-other',state:'running',slot:true}},
  'relay-refuses': {L1:{id:'L1',item_id:'n2',host_id:'agent-gone',state:'running',slot:true}},
}[scenario] || {}
let claimed = false
const timers = []
const hostStore = new Map()
const MESSAGE = 'Andy: the CSV delimiter must be a semicolon, not a comma'
const $ = {
  state:{get:async ref=>{const held=hostStore.get(ref.plugin+'/'+ref.key);return {value:held?structuredClone(held.value):undefined,version:held?.version||0}},set:async (ref,value,options={})=>{const name=ref.plugin+'/'+ref.key,held=hostStore.get(name)||{version:0};if(options.ifVersion!==undefined&&options.ifVersion!==held.version)return {isSet:false,version:held.version};hostStore.set(name,{value:structuredClone(value),version:held.version+1});return {isSet:true,version:held.version+1}}},
  session:{model:async()=>'fixture-main',id:async()=>'parent',
    append:async args=>{appended.push(args.message.content[0].text); return {uuid:'append-'+appended.length}},
    send:async args=>{sent.push({to:args.to,text:args.text}); return args.to.agentId === 'agent-gone' ? {isDelivered:false,reason:'gone'} : {isDelivered:true}}},
  prompt:{submit:async args=>{submitted.push(args.text); return {}}},
  mcp:{connect:async()=>({}),call:async(server,name,args)=>{
    let value = {}
    if (name === 'work_side_claim') {
      const claim = claimed ? null : {receipt_id:'e'.repeat(64),generation:'10000000000000000000000000000005',text:MESSAGE,event_key:'walkie:1',
        lease_until:Date.now()/1000+120,route:{},workload:{version:4,truncated:false,active_root:'n1',queue_order:[],
          nodes:[{id:'n1',parent_id:null,title:'export'},{id:'n2',parent_id:'n1',title:'csv writer'},{id:'n3',parent_id:'n2',title:'delimiter'},{id:'n8',parent_id:null,title:'other goal'},{id:'n9',parent_id:'n8',title:'other step'}]}}
      claimed = true
      value = {claim,settings:{model:'inherit',context_mode:'isolated'},handoff_replies:[],main_wakes:[]}
    }
    if (name === 'work_parallel_status') value = {version:1,policy:{},leases}
    if (name === 'work_side_resolve') {
      resolved.push(args.decision)
      value = {receipt:{receipt_id:args._host.receipt_id,stage:'processed'},
        result:{action:'update',item_id:'n2',title:'csv writer',summary:'Delimiter is a semicolon',duplicate:false,
          relay:args.decision.relay ? {item_id:'n2'} : undefined,handoff:'not_required'}}
    }
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
await completeSide($,{agentId:'actor1',reason:'answer',answer:JSON.stringify({decision:{action:'update',kind:'request',item_id:'n2',
  summary:'Delimiter is a semicolon',reply:'forwarded',note:'CSV delimiter must be ;',relay:scenario !== 'no-relay'},expected_version:4})})
await new Promise(resolve => setTimeout(resolve, 20))
console.log(JSON.stringify({sent,appended,submitted,wakes,logs,resolved}))

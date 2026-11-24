// Drives side-intake.js fork answers (user 2026-11-23) with a scripted host:
// one gate-check side event whose committed result answers a fork parked on a
// question. Prints what the Work host was asked to resume, what main was told,
// and the decision the host forwarded to the router.
import {startSide,pollSide,completeSide,sideMainBusy,sideParkedResume,decisionOf,routerDecision} from './side-intake.js'

const scenario = process.argv[2]
sideMainBusy(() => scenario !== 'stalled-idle')
const resumeCalls = []
sideParkedResume(async (ids,texts) => { resumeCalls.push({ids,texts:Object.fromEntries(texts || [])})
  return new Map(ids.map(id => [id, scenario === 'undelivered' ? 'the resume was not delivered: declined' : true])) })
const appended = [], wakes = [], logs = [], resolved = [], submitted = []
let claimed = false, noticeClaimed = false
const answer = {lease_id:'L2',answer:'MAIN DECISION 13:16Z: merge now; no notice needed',source:'n2 progress'}
const stall = {item_id:'n2',lease_id:'L3',kind:'wait',waiting_on:'production release 0.4.509 deployed',
  checked:'release.json says 0.4.508 (#162759)',needed:'deploy 0.4.509 or decide the order'}
const stalledCase = scenario === 'stalled' || scenario === 'stalled-idle'
const decision = stalledCase
  ? {action:'answer',kind:'status',summary:'n2 유지: still 0.4.508',reply:'stalled',stall_notices:[stall]}
  : {action:'answer',kind:'status',summary:'n2 has MAIN DECISION',reply:'answered L2',
     fork_answers:[answer], ...(scenario === 'refused-ask' ? {ask_main:true} : {})}
const result = {action:'answer',summary:decision.summary,reply:decision.reply,duplicate:false,handoff:'not_required',
  ...(stalledCase ? {stall_notices:[stall]} : scenario === 'refused-ask'
    ? {ask_main:true,unanswered_forks:[{lease_id:'L9',reason:'not a parked worker of this conversation'}]}
    : {fork_answers:[{...answer,item_id:'n2'}]})}
const hostStore = new Map()
const $ = {
  state:{get:async ref=>{const held=hostStore.get(ref.plugin+'/'+ref.key);return {value:held?structuredClone(held.value):undefined,version:held?.version||0}},set:async (ref,value,options={})=>{const name=ref.plugin+'/'+ref.key,held=hostStore.get(name)||{version:0};if(options.ifVersion!==undefined&&options.ifVersion!==held.version)return {isSet:false,version:held.version};hostStore.set(name,{value:structuredClone(value),version:held.version+1});return {isSet:true,version:held.version+1}}},
  session:{model:async()=>'fixture-main',id:async()=>'parent',
    append:async args=>{appended.push(args.message.content[0].text); return {uuid:'append-'+appended.length}},
    send:async()=>({isDelivered:true})},
  prompt:{submit:async args=>{submitted.push(args.text); return {}}},
  mcp:{connect:async()=>({}),call:async(server,name,args)=>{
    let value = {}
    if (name === 'work_side_claim') {
      const claim = claimed ? null : {receipt_id:'e'.repeat(64),generation:'10000000000000000000000000000005',
        text:'Ciel 게이트 재확인',event_key:'gate-check:abc:1:1:deadbeef',lease_until:Date.now()/1000+120,
        route:{source_kind:'gate_check'},decision_fields:['action','kind','item_id','summary','reply','ask_main','fork_answers','stall_notices'],
        workload:{version:4,truncated:false,active_root:'n1',queue_order:[],
          nodes:[{id:'n1',parent_id:null,title:'goal'},{id:'n2',parent_id:'n1',title:'merge PR 221'}]}}
      claimed = true
      value = {claim,settings:{model:'inherit',context_mode:'isolated'},handoff_replies:[],main_wakes:[]}
    }
    if (name === 'work_parallel_status') value = {version:1,policy:{},leases:{}}
    if (name === 'work_side_resolve') { resolved.push(args.decision)
      value = {receipt:{receipt_id:args._host.receipt_id,stage:'processed'},result} }
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
  clock:{after:(ms,callback)=>({cancel(){}})},
}
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
if (scenario === 'fields') {
  const parsed = decisionOf(JSON.stringify({understanding:'u',decision,expected_version:4}))
  console.log(JSON.stringify({kept:parsed.decision.fork_answers || null,
    oldRouter:routerDecision({decision_fields:['action','kind','summary','reply']},parsed.decision),
    newRouter:routerDecision({decision_fields:['action','kind','summary','reply','fork_answers']},parsed.decision),
    noFieldsRouter:routerDecision({},parsed.decision)}))
} else {
  await startSide($)
  await pollSide($,'claude:parent')
  await completeSide($,{agentId:'actor1',reason:'answer',answer:JSON.stringify({understanding:'one line',decision,expected_version:4})})
  const done = () => scenario === 'delivered' ? logs.some(l => l.startsWith('Side answered parked fork'))
    : scenario === 'stalled-idle' ? submitted.length > 0
    : wakes.some(call => call.state === 'notice_appended')
  for (const end = Date.now() + 5000; Date.now() < end && !done();) await sleep(5)
  await sleep(20)
  console.log(JSON.stringify({resumeCalls,appended,wakes,logs,resolved,submitted}))
}

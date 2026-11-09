import {startSide,pollSide,completeSide,sideOwns,sideTool,sideMcpParity,sideSpawnInput} from './side-intake.js'
let snapshotVersion=1
let available=[{name:'mcp__ciel-runtime-router__work_status',mcp:true},
 {name:'mcp__ciel-runtime-router__work_side_code_read',mcp:true},{name:'mcp__ciel-runtime-router__work_side_code_search',mcp:true},
 {name:'mcp__mail__read',mcp:true},
 {name:'mcp__github__create_issue',mcp:true},{name:'mcp__database__query',mcp:true},
 {name:'mcp__ciel-runtime-router__restart_session',mcp:true},{name:'mcp__ciel-runtime-router__work_node_update',mcp:true},
 {name:'mcp__ciel-runtime-router__work_side_resolve',mcp:true},{name:'mcp__ciel-runtime-router__submit_input',mcp:true},
 {name:'mcp__walkie__walkie_call_tool',mcp:true,description:'read-only helper'},{name:'mcp__walkie__walkie_threads_stop',mcp:true},
 {name:'mcp__walkie__walkie_read_messages',mcp:true},{name:'mcp__walkie__walkie_send_message',mcp:true},{name:'mcp__aap__run_agent',mcp:true},
 {name:'mcp__x__get_shell',mcp:true},{name:'mcp__x__read_and_post',mcp:true},{name:'mcp__x__search_and_replace',mcp:true},
 {name:'mcp__x__fetch_and_save',mcp:true},{name:'mcp__x__reply_and_close',mcp:true},{name:'mcp__x__ack_then_delete_all',mcp:true},
 {name:'Read',mcp:false},{name:'mcp__malformed',mcp:true},{name:'mcp__flag__text',mcp:'true'},{mcp:true},null]
let settings={model:'inherit',context_mode:'full'}
let wakes=[]
const submits=[]
let events=[
 {receipt_id:'a'.repeat(64),generation:'g1',text:'question',event_key:'source:one',lease_until:Date.now()/1000+120,
  source_read:{server:'walkie',tool:'walkie_read_messages',arguments:{conversation_id:'conversation',after:'40'},message_id:'41'},
  route:{channel:'external:source',sender_id:'sender',source_kind:'external_event'}},
]
const calls=[],spawns=[],timers=[]
const $={
 session:{model:async()=>'fixture-main',id:async()=>'parent',append:async args=>calls.push({append:args}),messages:async()=>[]},
 prompt:{submit:async args=>{submits.push(args);return {text:args.text}}},
 mcp:{connect:async()=>({}),call:async(server,name,args)=>{
  calls.push({server,name,args}); let value={}
  if(server==='walkie' && name==='walkie_read_messages') return {content:[{type:'text',text:JSON.stringify({messages:[{id:'41',text:'move the existing work to queue position 2'}]})}]}
  if(name==='work_side_claim'){
   const claim=events.shift()||null
   if(claim) claim.workload={version:snapshotVersion,truncated:false,active_root:'n1',queue_order:['n21'],nodes:[{id:'n1',title:'main'},{id:'n21',priority:'P1'}]}
   value={claim,settings:{...settings},handoff_replies:[],main_wakes:wakes}
  }
  if(name==='work_side_renew') value={lease_until:Date.now()/1000+120}
  if(name==='work_side_wake') value=args.state==='prepare' ? {state:'submitting',attempt_id:'attempt'} : {state:args.state}
  if(name==='work_side_resolve') value={receipt:{receipt_id:args._host.receipt_id,stage:'processed',result:{action:args.decision.action}},result:{action:args.decision.action,duplicate:false,version:++snapshotVersion}}
  if(name==='work_side_reply') value={receipt:{receipt_id:args.receipt_id,stage:'processed'},arguments:null}
  if(name==='work_status') value={version:snapshotVersion,active_root:{id:'n1'},queue:[]}
  return {content:[{type:'text',text:JSON.stringify(value)}]}
 }},
 tool:{list:async()=>available,call:async args=>{calls.push({toolCall:args});return {text:'stopped'}}},
 agent:{list:async()=>[],register:async args=>calls.push({registered:args}),spawn:async args=>{
   const rewritten=sideSpawnInput({...args,parentAgentId:undefined}, {origin:{plugin:'ciel-work'}}) || args
   spawns.push(rewritten);return {agentId:'actor'+spawns.length,model:rewritten.model}}},
 env:{get:async key=>key==='CIEL_RUNTIME_DIRECT_GATEWAY_ACTIVE'?'0':'x'.repeat(40)},
 ui:{log:()=>{},status:()=>{},invalidate:()=>{}},clock:{after:(ms,callback)=>{timers.push(callback);return {cancel(){}}}},
}
const drain=async()=>{while(timers.length) await timers.shift()()}
await startSide($)
await pollSide($,'claude:parent')
const first=sideOwns('actor1')
const nonWork=await sideTool($,{agentId:'actor1',tool:'mcp__mail__read',tool_use_id:'t1'},async()=>({result:'native'}))
const workDenied=await sideTool($,{agentId:'actor1',tool:'mcp__ciel-runtime-router__work_plan_update',tool_use_id:'t2'},async()=>({result:'bad'}))
// Observed live: prose, then a fenced decision. It must commit, not retry.
await completeSide($,{agentId:'actor1',reason:'answer',answer:'Status answered.\n\n```json\n'+JSON.stringify({decision:{action:'answer',kind:'status',summary:'done',reply:'answer'},expected_version:1})+'\n```',usage:{input_tokens:3}})
await drain()
available=[...available,{name:'mcp__calendar__read',mcp:true}]
events.push({receipt_id:'b'.repeat(64),generation:'g2',text:'second',event_key:'source:two',lease_until:Date.now()/1000+120,route:{channel:'external:source',sender_id:'sender',source_kind:'external_event'}})
// A durable main wake is offered by every claim until main acknowledges it.
wakes=[{event_id:'d'.repeat(64),item_id:'n21',kind:'resume',summary:'approval received',question:'',waits:[{id:'n22',waiting_on:'approval'}]}]
await pollSide($,'claude:parent')
const second=sideOwns('actor2')
// Observed live: sent_via nested in decision and public_summary for summary.
await completeSide($,{agentId:'actor2',reason:'answer',answer:'Nothing new needed.\n\n'+JSON.stringify({decision:{action:'answer',kind:'info',public_summary:'done2',reply:'answer2',reason_for_advise:'extra',item_id:null},expected_version:snapshotVersion}),usage:null})
await drain()
// A completed turn without any decision may already have sent through MCP:
// it is recorded as invalid_answer (never retried), and its actor still stops.
events.push({receipt_id:'c'.repeat(64),generation:'g3',text:'third',event_key:'source:three',lease_until:Date.now()/1000+120,route:{channel:'external:source',sender_id:'sender',source_kind:'external_event'}})
// Live UI model change between events: the next spawn itself names the new model.
settings={model:'ciel-runtime-fixture-_selected_b',context_mode:'full'}
await pollSide($,'claude:parent')
await completeSide($,{agentId:'actor3',reason:'answer',answer:'I replied in the thread.',usage:null})
await drain()
const registrations=calls.filter(c=>c.registered?.name==='side-intake').map(c=>c.registered)
console.log(JSON.stringify({
 spawns:spawns.map(s=>({subagentType:s.subagentType,model:s.model||null,name:s.name||null,description:s.description,prompt:JSON.parse(s.prompt)})),
 submits:submits.map(s=>s.text),registeredModels:registrations.map(r=>r.model),
 first:{current:first.current,retired:first.retired},second:{current:second.current,retired:second.retired},
 stopped:calls.filter(c=>c.toolCall?.tool==='TaskStop').map(c=>c.toolCall.task_id),
 nonWork,workDenied,registrations:registrations.map(r=>({tools:r.tools,mcpServers:r.mcpServers,maxTurns:r.maxTurns,effort:r.effort,omitClaudeMd:r.omitClaudeMd})),
 parity:sideMcpParity(),resolveCalls:calls.filter(c=>c.name==='work_side_resolve').map(c=>c.args),
 failures:calls.filter(c=>c.name==='work_side_fail').map(c=>({category:c.args.category,receipt:c.args._host.receipt_id,reason:c.args.reason}))
}))

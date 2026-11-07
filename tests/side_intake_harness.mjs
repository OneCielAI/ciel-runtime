import {startSide,pollSide,completeSide,sideOwns,sideTool,sideMcpParity} from './side-intake.js'
let snapshotVersion=1
let available=[{name:'mcp__ciel-runtime-router__work_status',mcp:true},{name:'mcp__mail__read',mcp:true},
 {name:'mcp__github__create_issue',mcp:true},{name:'mcp__database__query',mcp:true},
 {name:'Read',mcp:false},{name:'mcp__malformed',mcp:true},{name:'mcp__flag__text',mcp:'true'},{mcp:true},null]
let events=[
 {receipt_id:'a'.repeat(64),generation:'g1',text:'question',event_key:'source:one',lease_until:Date.now()/1000+120,route:{channel:'external:source',sender_id:'sender',source_kind:'external_event'}},
]
const calls=[],spawns=[],timers=[]
const $={
 session:{model:async()=>'fixture-main',id:async()=>'parent',append:async args=>calls.push({append:args}),messages:async()=>[]},
 mcp:{connect:async()=>({}),call:async(server,name,args)=>{
  calls.push({server,name,args}); let value={}
  if(name==='work_side_claim'){
   const claim=events.shift()||null
   if(claim) claim.workload={version:snapshotVersion,truncated:false,active_root:'n1',queue_order:['n21'],nodes:[{id:'n1',title:'main'},{id:'n21',priority:'P1'}]}
   value={claim,settings:{model:'inherit',context_mode:'full'},handoff_replies:[]}
  }
  if(name==='work_side_renew') value={lease_until:Date.now()/1000+120}
  if(name==='work_side_resolve') value={receipt:{receipt_id:args._host.receipt_id,stage:'processed',result:{action:args.decision.action}},result:{action:args.decision.action,duplicate:false,version:++snapshotVersion}}
  if(name==='work_side_reply') value={receipt:{receipt_id:args.receipt_id,stage:'processed'},arguments:null}
  if(name==='work_status') value={version:snapshotVersion,active_root:{id:'n1'},queue:[]}
  return {content:[{type:'text',text:JSON.stringify(value)}]}
 }},
 tool:{list:async()=>available,call:async args=>{calls.push({toolCall:args});return {text:'stopped'}}},
 agent:{list:async()=>[],register:async args=>calls.push({registered:args}),spawn:async args=>{spawns.push(args);return {agentId:'actor'+spawns.length}}},
 env:{get:async key=>key==='CIEL_RUNTIME_DIRECT_GATEWAY_ACTIVE'?'0':'x'.repeat(40)},
 ui:{log:()=>{},status:()=>{},invalidate:()=>{}},clock:{after:(ms,callback)=>{timers.push(callback);return {cancel(){}}}},
}
const drain=async()=>{while(timers.length) await timers.shift()()}
await startSide($)
await pollSide($,'claude:parent')
const first=sideOwns('actor1')
const nonWork=await sideTool($,{agentId:'actor1',tool:'mcp__mail__read',tool_use_id:'t1'},async()=>({result:'native'}))
const workDenied=await sideTool($,{agentId:'actor1',tool:'mcp__ciel-runtime-router__work_plan_update',tool_use_id:'t2'},async()=>({result:'bad'}))
await completeSide($,{agentId:'actor1',reason:'answer',answer:'```json\n'+JSON.stringify({decision:{action:'answer',summary:'done',reply:'answer'},expected_version:1})+'\n```',usage:{input_tokens:3}})
await drain()
available=[...available,{name:'mcp__calendar__read',mcp:true}]
events.push({receipt_id:'b'.repeat(64),generation:'g2',text:'second',event_key:'source:two',lease_until:Date.now()/1000+120,route:{channel:'external:source',sender_id:'sender',source_kind:'external_event'}})
await pollSide($,'claude:parent')
const second=sideOwns('actor2')
await completeSide($,{agentId:'actor2',reason:'answer',answer:JSON.stringify({decision:{action:'answer',summary:'done2',reply:'answer2'},expected_version:snapshotVersion}),usage:null})
await drain()
const registrations=calls.filter(c=>c.registered?.name==='side-intake').map(c=>c.registered)
console.log(JSON.stringify({
 spawns:spawns.map(s=>({subagentType:s.subagentType,name:s.name||null,description:s.description,prompt:JSON.parse(s.prompt)})),
 first:{current:first.current,retired:first.retired},second:{current:second.current,retired:second.retired},
 stopped:calls.filter(c=>c.toolCall?.tool==='TaskStop').map(c=>c.toolCall.task_id),
 nonWork,workDenied,registrations:registrations.map(r=>({tools:r.tools,mcpServers:r.mcpServers})),
 parity:sideMcpParity(),resolveCalls:calls.filter(c=>c.name==='work_side_resolve').map(c=>c.args)
}))

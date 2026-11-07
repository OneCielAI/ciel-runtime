import {startSide,pollSide,completeSide,sideOwns,sideReceipts,sideTool,sideMcpParity} from './side-intake.js'
let snapshotVersion=1
let handoffReplies=[]
let available=[{name:'mcp__ciel-runtime-router__work_status',mcp:true},{name:'mcp__mail__read',mcp:true},
 {name:'mcp__github__create_issue',mcp:true},{name:'mcp__database__query',mcp:true},
 {name:'Read',mcp:false},{name:'mcp__malformed',mcp:true},{name:'mcp__flag__text',mcp:'true'},{mcp:true},null]
let nonWorkNext=0
const logs=[]
let events=[{receipt_id:'a'.repeat(64),generation:'g1',text:'question\nsecond line',event_key:'source:one',lease_until:Date.now()/1000+120,route:{channel:'external:source',sender_id:'sender',source_kind:'external_event',thread_id:'one'}}]
const calls=[],spawns=[],sent=[],timers=[]
const $={
 session:{model:async()=>'fixture-main',id:async()=>'parent',send:async args=>{sent.push(args);return {isDelivered:true}},append:async args=>calls.push({append:args})},
 mcp:{connect:async()=>({}),call:async(server,name,args)=>{
  calls.push({server,name,args})
  let value={}
  if(name==='work_side_claim'){
    const claim=events.shift()||null
    if(claim)claim.workload={version:snapshotVersion,truncated:false,active_root:'n1',queue_order:['n21'],nodes:[{id:'n1',title:'main'},{id:'n21',title:'queued beyond preview',priority:'P1'}]}
    value={claim,settings:{model:'inherit'},handoff_replies:handoffReplies}
  }
  if(name==='work_side_renew')value={lease_until:Date.now()/1000+120}
  if(name==='work_side_compare'&&args.action==='plan')value={profiles:[{profile_id:'profile1',model:'fixture-main'}]}
  if(name==='work_side_compare'&&args.action==='context')value={key:'shadow-context',original:'after MCP change',workload:{version:snapshotVersion}}
  if(name==='work_status')value={version:snapshotVersion,active_root:{id:'n1',title:'main'},queue:[]}
  if(name==='work_side_resolve')value={receipt:{stage:'processed',recap:'q',result:{action:args.decision.action,summary:'done',reply:'answer'}},result:{action:args.decision.action,summary:'done',reply:'answer',duplicate:false,version:++snapshotVersion}}
  return {content:[{type:'text',text:JSON.stringify(value)}]}
 }},
 tool:{list:async()=>available},
 agent:{list:async()=>[],register:async args=>calls.push({registered:args}),spawn:async args=>{spawns.push(args);return {agentId:'actor'+spawns.length}}},
 env:{get:async()=>'x'.repeat(40)},
 ui:{log:text=>logs.push(text),status:()=>{},invalidate:()=>{}},clock:{after:(ms,callback)=>{timers.push(callback);return {cancel(){}}}},
}
await startSide($)
await pollSide($,'claude:parent')
await completeSide($,{agentId:'actor1',reason:'answer',answer:JSON.stringify({decision:{action:'answer',summary:'done',reply:'answer'},expected_version:1}),usage:{input_tokens:3}})
events.push({receipt_id:'b'.repeat(64),generation:'g2',text:'follow up',event_key:'source:two',lease_until:Date.now()/1000+120,route:{channel:'external:source',sender_id:'sender',source_kind:'external_event',thread_id:'two'}})
await pollSide($,'claude:parent')
await sideTool($,{agentId:'actor1',tool:'mcp__mail__read',tool_use_id:'tool-1'},async()=>{nonWorkNext++;return {result:'native'}})
sideOwns('actor1').current.lease_until = Date.now()/1000 + 50
await pollSide($,'claude:parent')
const resolved=calls.find(c=>c.name==='work_side_resolve')
handoffReplies=[{event_id:'a'.repeat(64),state:'answered',answer:'Use target A',receipt:{receipt_id:'a'.repeat(64),stage:'processed',recap:'q',result:{action:'advise',summary:'question',reply:'Which target?',handoff:'answered',delivery:'submitted'}}}]
await pollSide($,'claude:parent')
const beforeChange={spawns:spawns.length,parity:sideMcpParity()}
const workDenied=await sideTool($,{agentId:'actor1',tool:'mcp__ciel-runtime-router__work_plan_update',tool_use_id:'tool-2'},async()=>{throw new Error('Work mutation reached native next')})
available=[...available,{name:'mcp__calendar__read',mcp:true}]
events.push({receipt_id:'c'.repeat(64),generation:'g3',text:'after MCP change',event_key:'source:three',lease_until:Date.now()/1000+120,route:{channel:'external:source',sender_id:'sender',source_kind:'external_event',thread_id:'three'}})
await pollSide($,'claude:parent')
const activeAfterChange=sideOwns('actor1').current.receipt_id
const changedCurrent=sideOwns('actor2')?.current?.receipt_id
const changedRegistrations=calls.filter(c=>c.registered?.name==='side-intake').map(c=>c.registered)
const afterChange={spawns:spawns.length,resumes:sent.length,parity:sideMcpParity()}
sideOwns('actor2').comparisonEnabled=true
await completeSide($,{agentId:'actor2',reason:'answer',answer:JSON.stringify({decision:{action:'answer',summary:'done',reply:'answer'},expected_version:snapshotVersion}),usage:null})
while(timers.length)await timers.shift()()
const shadow=calls.filter(c=>c.registered?.name==='side-shadow').map(c=>c.registered)
// Same digest reuses the idle actor; a later digest change never resumes an idle stale actor.
events.push({receipt_id:'d'.repeat(64),generation:'g4',text:'same MCP',event_key:'source:four',lease_until:Date.now()/1000+120,route:{channel:'external:source',sender_id:'sender',source_kind:'external_event',thread_id:'four'}})
await pollSide($,'claude:parent')
const sameDigest={spawns:spawns.length,resumes:sent.length,to:sent.at(-1).to.agentId}
await completeSide($,{agentId:'actor1',reason:'answer',answer:JSON.stringify({decision:{action:'answer',summary:'done',reply:'answer'},expected_version:snapshotVersion}),usage:null})
available=[...available.filter(t=>t?.name!=='mcp__calendar__read'),{name:'mcp__drive__read',mcp:true}]
events.push({receipt_id:'e'.repeat(64),generation:'g5',text:'replaced MCP',event_key:'source:five',lease_until:Date.now()/1000+120,route:{channel:'external:source',sender_id:'sender',source_kind:'external_event',thread_id:'five'}})
await pollSide($,'claude:parent')
const registrations=calls.filter(c=>c.registered?.name==='side-intake').map(c=>c.registered)
console.log(JSON.stringify({handoff:sideReceipts('claude:parent').find(r=>r.receipt_id==='a'.repeat(64))?.result?.handoff,spawns:beforeChange.spawns,resumes:afterChange.resumes,parent:resolved.args.session_id,
 hostReceipt:resolved.args._host.receipt_id,usage:resolved.args.usage,
 workload:JSON.parse(spawns[0].prompt).workload,heartbeat:calls.some(c=>c.name==='work_side_renew'),current:activeAfterChange,tools:registrations[0].tools,logs,
 nonWorkNext,workDenied,beforeParity:beforeChange.parity,afterParity:afterChange.parity,registrations:changedRegistrations.length,
 changedSpawns:afterChange.spawns,changedCurrent,changedTools:changedRegistrations.at(-1).tools,
 changedServers:changedRegistrations.at(-1).mcpServers,changedDisallowed:changedRegistrations.at(-1).disallowedTools,
 shadow:shadow.map(r=>({tools:r.tools,mcpServers:r.mcpServers})),sameDigest,
 replaced:{spawns:spawns.length,resumes:sent.length,current:sideOwns('actor4')?.current?.receipt_id,registrations:registrations.length,
  tools:registrations.at(-1).tools,parity:sideMcpParity()}}))

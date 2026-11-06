import {startSide,pollSide,completeSide,sideOwns,sideReceipts} from './side-intake.js'
let snapshotVersion=1
let handoffReplies=[]
let available=[{name:'mcp__ciel-runtime-router__work_status',mcp:true},{name:'mcp__mail__read',mcp:true}]
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
  if(name==='work_status')value={version:snapshotVersion,active_root:{id:'n1',title:'main'},queue:[]}
  if(name==='work_side_resolve')value={receipt:{stage:'processed',recap:'q',result:{action:args.decision.action,summary:'done',reply:'answer'}},result:{action:args.decision.action,summary:'done',reply:'answer',duplicate:false,version:++snapshotVersion}}
  return {content:[{type:'text',text:JSON.stringify(value)}]}
 }},
 tool:{list:async()=>available},
 agent:{register:async args=>calls.push({registered:args}),spawn:async args=>{spawns.push(args);return {agentId:'actor1'}}},
 env:{get:async()=>'x'.repeat(40)},
 ui:{log:text=>logs.push(text),status:()=>{},invalidate:()=>{}},clock:{after:(ms,callback)=>{timers.push(callback);return {cancel(){}}}},
}
await startSide($)
await pollSide($,'claude:parent')
await completeSide($,{agentId:'actor1',reason:'answer',answer:JSON.stringify({decision:{action:'answer',summary:'done',reply:'answer'},expected_version:1}),usage:{input_tokens:3}})
events.push({receipt_id:'b'.repeat(64),generation:'g2',text:'follow up',event_key:'source:two',lease_until:Date.now()/1000+120,route:{channel:'external:source',sender_id:'sender',source_kind:'external_event',thread_id:'two'}})
await pollSide($,'claude:parent')
sideOwns('actor1').current.lease_until = Date.now()/1000 + 50
await pollSide($,'claude:parent')
const resolved=calls.find(c=>c.name==='work_side_resolve')
handoffReplies=[{event_id:'a'.repeat(64),state:'answered',answer:'Use target A',receipt:{receipt_id:'a'.repeat(64),stage:'processed',recap:'q',result:{action:'advise',summary:'question',reply:'Which target?',handoff:'answered',delivery:'submitted'}}}]
await pollSide($,'claude:parent')
console.log(JSON.stringify({handoff:sideReceipts('claude:parent').find(r=>r.receipt_id==='a'.repeat(64))?.result?.handoff,spawns:spawns.length,resumes:sent.length,parent:resolved.args.session_id,
 hostReceipt:resolved.args._host.receipt_id,usage:resolved.args.usage,
 workload:JSON.parse(spawns[0].prompt).workload,heartbeat:calls.some(c=>c.name==='work_side_renew'),current:sideOwns('actor1').current.receipt_id,tools:calls.find(c=>c.registered).registered.tools,logs}))

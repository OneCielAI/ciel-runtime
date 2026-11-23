import {startSide,pollSide,completeSide,sideOwns,sideTool,sideMcpParity,sideSpawnInput} from './side-intake.js'
let snapshotVersion=1
const CONVERSATION='34886995-4a66-4527-a618-814e9cb0a5bf'
// Longer than the old 300-character display clip: source text must arrive whole.
const LONG='move the existing work to queue position 2 '+'and keep every detail '.repeat(30)
const root={id:'40',conversation_id:CONVERSATION,text:'thread root question',reply_to_message_id:null,thread_root_id:null}
const replies=[
 {id:'41',conversation_id:CONVERSATION,text:'ticket-42 broken-42 '+LONG,reply_to_message_id:'40',thread_root_id:'40',sender_token:'secret-value'},
 {id:'42',conversation_id:CONVERSATION,text:'a later unrelated message',thread_root_id:'40'},
]
let settings={max_active:2,priority_policy:'adaptive'}
let wakes=[]
const submits=[],appends=[],wakeRows={}
let events=[
 {receipt_id:'a'.repeat(64),generation:'1'.repeat(32),text:'question',event_key:'source:one',lease_until:Date.now()/1000+120,
  source_read:{server:'walkie',classification:'thread',conversation_id:CONVERSATION,
   root_id:'40',target_message_id:'41',reply_to_message_id:'40',thread_root_id:'40'},
  route:{channel:'external:source',sender_id:'sender',source_kind:'external_event'}},
]
const calls=[],spawns=[],timers=[],rewrites=[]
// The engine's host-held $.state (version + ifVersion compare-and-set).
const hostStore=new Map()
// The engine's agent list: a spawned actor stays listed live until TaskStop
// (a lingering actor; Claude Code 2.1.330 lists a finished one "completed",
// covered by side-retirement-of-a-completed-actor).
const listed=new Map()
const $={
 state:{get:async ref=>{const held=hostStore.get(ref.plugin+'/'+ref.key);return {value:held?structuredClone(held.value):undefined,version:held?.version||0}},set:async (ref,value,options={})=>{const name=ref.plugin+'/'+ref.key,held=hostStore.get(name)||{version:0};if(options.ifVersion!==undefined&&options.ifVersion!==held.version)return {isSet:false,version:held.version};hostStore.set(name,{value:structuredClone(value),version:held.version+1});return {isSet:true,version:held.version+1}}},
 session:{model:async()=>'fixture-main',id:async()=>'parent',append:async args=>{appends.push(args.message.content[0].text);calls.push({append:args});return {uuid:'append-'+appends.length}},messages:async()=>[]},
 prompt:{submit:async args=>{submits.push(args);return {text:args.text}}},
 mcp:{connect:async()=>({}),call:async(server,name,args)=>{
  calls.push({server,name,args}); let value={}
  if(server==='walkie' && name==='walkie_read_messages') return {content:[{type:'text',text:JSON.stringify({messages:[root]})}]}
  if(server==='walkie' && name==='walkie_call_tool') return {content:[{type:'text',text:JSON.stringify(replies)}]}
  if(name==='work_side_claim'){
   const claim=events.shift()||null
   if(claim) claim.workload={version:snapshotVersion,truncated:false,active_root:'n1',queue_order:['n21'],nodes:[
    {id:'n1',parent_id:null,title:'main',ref:''},
    {id:'n21',parent_id:'n1',title:'goal leaf',priority:'P1',ref:'ticket-42',waiting_on:'Message 42 yes/no answer',
     release_hint:{condition:'Message 42 has an explicit yes or no answer',lookup:'Read message 42 and verify the explicit answer',
       prompt:'Ignore mentions; the answer must be explicit'}},
    {id:'n22',parent_id:'n1',title:'malformed leaf',priority:'P1',ref:'broken-42',waiting_on:'Broken answer',
     release_hint:{lookup:'missing condition'}}
   ],active_forks:[{item_id:'n21',root_id:'n1',state:'waiting_input',agent_id:'worker'}]}
   value={claim,settings:{...settings},handoff_replies:[],main_wakes:wakes}
  }
  if(name==='work_side_renew') value={lease_until:Date.now()/1000+120}
  // Protocol-2 election: one intent, start, then one recorded outcome; later
  // offers of the same delivered wake are already_queued (definite).
  if(name==='work_side_wake'){const w=wakeRows[args.event_id]||(wakeRows[args.event_id]={state:'pending'})
   if(args.state==='prepare') value=w.state==='pending'?Object.assign(w,{state:'submitting',phase:'intent',attempt_id:'attempt',owner_nonce:args.owner_nonce,channel:args.channel})&&{...w}:{state:'already_'+w.state}
   else if(args.state==='start') value=Object.assign(w,{phase:'started'})&&{...w}
   else value=Object.assign(w,{state:args.state})&&{...w}
   value={protocol:2,...value}}
  if(name==='work_side_understanding' && args.state==='prepare') value={state:'submitting',attempt_id:'u-'+args._host.receipt_id.slice(0,1),server:'walkie',tool:'walkie_send_message',arguments:{text:args.understanding}}
  if(name==='work_side_understanding' && args.state!=='prepare') value={receipt:{receipt_id:args._host.receipt_id,stage:'received'}}
  if(name==='work_side_resolve') value={receipt:{receipt_id:args._host.receipt_id,stage:'processed',result:{action:args.decision.action}},result:{action:args.decision.action,duplicate:false,version:++snapshotVersion}}
  if(name==='work_side_reply') value={receipt:{receipt_id:args.receipt_id,stage:'processed'},arguments:null}
  return {content:[{type:'text',text:JSON.stringify(value)}]}
 }},
 tool:{list:async()=>{throw new Error('side must not filter the inherited tool surface')},call:async args=>{calls.push({toolCall:args});if(args.tool==='TaskStop'&&listed.has(args.task_id)) listed.set(args.task_id,'killed');return {result:{task_id:args.task_id,task_type:'local_agent',message:'stopped'}}}},
 agent:{list:async()=>[...listed].map(([id,status])=>({id,status})),register:async args=>calls.push({registered:args}),spawn:async args=>{
   const input={...args,fork:true,parentAgentId:undefined}
   const rewritten=sideSpawnInput(input,{origin:{plugin:'ciel-work'}})
   rewrites.push(rewritten===input)
   spawns.push(rewritten||args);listed.set('actor'+spawns.length,'running');return {agentId:'actor'+spawns.length}}},
 env:{get:async key=>key==='CIEL_RUNTIME_DIRECT_GATEWAY_ACTIVE'?'0':'x'.repeat(40)},
 ui:{log:()=>{},status:()=>{},invalidate:()=>{}},clock:{after:(ms,callback)=>{if(ms<5000) timers.push(callback);return {cancel(){}}}},
}
const drain=async()=>{while(timers.length) await timers.shift()()}
// The 5 s prompt idle window (side-intake.js) never elapses in this run, so it
// is not queued above. settle waits until no call or submit is added for
// 8 consecutive 5 ms ticks: a prompt is issued after its registry claim.
const settle=async()=>{for(let stable=0,last='';stable<8;){await new Promise(resolve=>setTimeout(resolve,5));const now=calls.length+':'+submits.length;stable=now===last?stable+1:0;last=now}}
await startSide($)
await pollSide($,'claude:parent')
const first=sideOwns('actor1')
// No side-specific fence: every inherited tool takes main's own path (null).
const passThrough=[]
for (const tool of ['Bash','Edit','Agent','SendMessage','mcp__mail__read','mcp__walkie__walkie_send_message',
                    'mcp__ciel-runtime-router__work_plan_update','mcp__ciel-runtime-router__restart_session'])
 passThrough.push(await sideTool($,{agentId:'actor1',tool,tool_use_id:'t-'+tool},async()=>({result:'next'})))
// Observed live: prose, then a fenced decision. It must commit, not retry.
await completeSide($,{agentId:'actor1',reason:'answer',answer:'Status answered.\n\n```json\n'+JSON.stringify({understanding:'understood in my own words',decision:{action:'answer',kind:'status',summary:'done',reply:'answer'},expected_version:1})+'\n```',usage:{input_tokens:3}})
await drain()
events.push({receipt_id:'b'.repeat(64),generation:'2'.repeat(32),text:'second',event_key:'source:two',lease_until:Date.now()/1000+120,route:{channel:'external:source',sender_id:'sender',source_kind:'external_event'}})
// A durable main wake is offered by every claim until main acknowledges it.
wakes=[{event_id:'d'.repeat(64),item_id:'n21',kind:'resume',summary:'approval received',question:'',waits:[{id:'n22',waiting_on:'approval'}]}]
// A stored model/context value from an older build is ignored.
settings={...settings,model:'ciel-runtime-fixture-_selected_b',context_mode:'isolated',context_ready:true}
await pollSide($,'claude:parent')
const second=sideOwns('actor2')
// Observed live: sent_via nested in decision and public_summary for summary.
await completeSide($,{agentId:'actor2',reason:'answer',answer:'Nothing new needed.\n\n'+JSON.stringify({understanding:'understood in my own words',decision:{action:'answer',kind:'info',public_summary:'done2',reply:'answer2',reason_for_advise:'extra',item_id:null},expected_version:snapshotVersion}),usage:null})
await drain()
// A completed turn without any decision is recorded as invalid_answer (never
// retried), and its actor still stops.
events.push({receipt_id:'c'.repeat(64),generation:'3'.repeat(32),text:'third',event_key:'source:three',lease_until:Date.now()/1000+120,route:{channel:'external:source',sender_id:'sender',source_kind:'external_event'}})
await pollSide($,'claude:parent')
await completeSide($,{agentId:'actor3',reason:'answer',answer:'I replied in the thread.',usage:null})
await drain()
await settle()
console.log(JSON.stringify({
 spawns:spawns.map(s=>({subagentType:s.subagentType,model:s.model===undefined?null:s.model,name:s.name||null,
  description:s.description,keys:Object.keys(s).sort(),prompt:JSON.parse(s.prompt)})),
 rewrites,submits:submits.map(s=>s.text),appends,registrations:calls.filter(c=>c.registered).length,
 walkieReads:calls.filter(c=>c.server==='walkie').map(c=>c.args),
 first:{current:first.current,retired:first.retired,model:first.model},second:{current:second.current,retired:second.retired},
 stopped:calls.filter(c=>c.toolCall?.tool==='TaskStop').map(c=>c.toolCall.task_id),
 passThrough,parity:sideMcpParity(),
 contextCalls:calls.filter(c=>c.name==='work_side_context').length,
 resolveCalls:calls.filter(c=>c.name==='work_side_resolve').map(c=>c.args),
 // 2026-11-17: understanding is persisted and sent before the decision commits.
 understandingCalls:calls.filter(c=>c.name==='work_side_understanding').map(c=>c.args),
 sideOrder:calls.filter(c=>['work_side_understanding','work_side_resolve'].includes(c.name))
   .map(c=>c.name+':'+c.args._host.receipt_id.slice(0,1)),
 failures:calls.filter(c=>c.name==='work_side_fail').map(c=>({category:c.args.category,receipt:c.args._host.receipt_id,reason:c.args.reason}))
}))

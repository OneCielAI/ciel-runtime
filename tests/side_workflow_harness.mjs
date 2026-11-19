// Approval workflow: an approval event of a registered request is verified and
// applied by host code (work_side_workflow_event) against the registered request
// message the host read itself (one read, never the thread); no side fork
// starts, and only an applied approval sends one reply.
import {startSide,pollSide,sideWorkflowApproved} from './side-intake.js'
const mode=process.argv[2] || 'approved'
const conversation='11111111-2222-3333-4444-555555555555'
const event={receipt_id:'a'.repeat(64),generation:'2a'.repeat(16),text:JSON.stringify({type:'com.ciel.walkie.message.approved',
  data:{conversation_id:conversation,message_id:'9100',payload:{approver_id:'u1',decision:'approved',all_approved:true}}}),
  event_key:'walkie:a',lease_until:Date.now()/1000+120,route:{source_kind:'external_event'},
  source_read:{classification:'thread',server:'walkie',conversation_id:conversation,root_id:'9001',target_message_id:'9100'},
  source_context:'thread',workflow_event:{root_id:'n5',kind:'approved',request_read:{server:'walkie',
   conversation_id:conversation,message_id:'9100',thread_root_id:'9001',reply_cursor:null,row_digest:null}}}
const queue=[event]
const calls=[],spawns=[],timers=[],logs=[]
let approvedCalls=0
const rows=[{id:'9001',text:'request'},{id:'9100',thread_root_id:'9001',text:'proposal [proposal n5 v1 abc]',approvers:['u1'],
  approvals:[{user_id:'u1',decision:'approved',superseded_by:null,withdrawn_at:null}]}]
const $={
 state:{get:async()=>({value:undefined,version:0}),set:async()=>({isSet:true,version:1})},
 session:{model:async()=>'fixture-main',id:async()=>'parent',append:async args=>calls.push({append:args.message}),messages:async()=>[],
  usage:async()=>({context:{window:200000,tokens:1000}})},
 mcp:{connect:async()=>({}),call:async(server,name,args)=>{
  calls.push({server,name,args}); let value={}
  if(name==='walkie_read_messages') value={messages:rows.filter(row=>BigInt(row.id)>BigInt(args.after))}
  else if(name==='walkie_call_tool') value=rows.slice(1)
  else if(name==='work_side_claim'){
   const claim=queue.length ? queue.shift() : null
   if(claim) claim.workload={version:7,truncated:false,active_root:'n1',queue_order:[],nodes:[]}
   value={claim,settings:{max_active:2},handoff_replies:[]}
  } else if(name==='work_side_workflow_event'){
   const applied=mode==='approved'
   value={root_id:'n5',applied,decision:mode==='approved'?'approved':'invalid',reason:applied?'verified':'not a designated approver',
    state:applied?'executing':'awaiting_approval',receipt:{receipt_id:args._host.receipt_id,stage:'processed'}}
  } else if(name==='work_side_reply') value={receipt:{receipt_id:args.receipt_id,stage:'processed'},arguments:null,state:'not_applicable'}
  return {content:[{type:'text',text:JSON.stringify(value)}]}
 }},
 tool:{list:async()=>[],call:async args=>{calls.push({toolCall:args});return {text:'stopped'}}},
 agent:{list:async()=>[],register:async()=>{},spawn:async args=>{spawns.push(args);return {agentId:'actor'+spawns.length}}},
 env:{get:async key=>key==='CIEL_RUNTIME_DIRECT_GATEWAY_ACTIVE'?'0':'x'.repeat(40)},
 fs:{read:async()=>''},
 ui:{log:text=>logs.push(text),status:()=>{},invalidate:()=>{}},clock:{after:(ms,callback)=>{timers.push(callback);return {cancel(){}}}},
}
sideWorkflowApproved(()=>{approvedCalls++})
await startSide($)
await pollSide($,'claude:parent')
const settle=calls.find(c=>c.name==='work_side_workflow_event')
console.log(JSON.stringify({
 spawns:spawns.length,
 settleHost:settle ? {receipt:settle.args._host.receipt_id===event.receipt_id,generation:settle.args._host.generation===event.generation} : null,
 sourceContext:settle?.args.source_message?.source_context || null,
 approvals:settle?.args.source_message?.messages?.find(m=>m.id==='9100')?.approvals?.length ?? null,
 replies:calls.filter(c=>c.name==='work_side_reply').length,
 walkie:calls.filter(c=>c.server==='walkie').map(c=>c.name+':'+(c.args.after||'')),
 approvedCalls,
 resolves:calls.filter(c=>c.name==='work_side_resolve'||c.name==='work_side_understanding').length,
 logs:logs.filter(text=>text.includes('Work workflow')),
}))

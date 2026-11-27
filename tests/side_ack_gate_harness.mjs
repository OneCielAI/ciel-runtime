// Daniel 2026-11-27 (Walkie 162826/162830): events whose Walkie guidance asks
// for messages.work.update state="ack" with a memo before anything else were
// resolved with no ACK (162787: sent_via null on all three receipts). Drives
// side-intake.js's completeSide with a scripted router and Walkie whose
// messages.work.get shows, or does not show, this agent's ACK and memo.
import {startSide,pollSide,completeSide} from './side-intake.js'
const CONVERSATION='34886995-4a66-4527-a618-814e9cb0a5bf'
const SELF='9f0c1793-b358-471b-b8ef-0d33be683c20'
const OTHER='11111111-2222-4333-8444-555555555555'
const root={id:'40',conversation_id:CONVERSATION,text:'thread root question',thread_root_id:null}
const GUIDANCE='메시지를 받자마자 채팅 답글 대신 Walkie MCP 도구 walkie_messages_work_update(또는 walkie_call_tool '
 +'operation_id="messages.work.update", schema_version=1)로 conversation_id="'+CONVERSATION+'", message_id="41", '
 +'state="ack", version=0, client_id="<UUID>", memo="<짧은 확인 메모>" 상태 기록을 먼저 남긴다.'
const eventText=guidance=>JSON.stringify({specversion:'1.0',id:'ev-1',type:'com.ciel.walkie.support.message',
 source:'http://walkie.invalid/conversations/'+CONVERSATION,
 data:{conversation:CONVERSATION,message_id:'41',thread_root_id:'40',reply_to_message_id:'40',
  text:'please queue the CSV export',...(guidance ? {agent_context:{guidance}} : {})}})
const row=(agent,history)=>({agent_id:agent,display_name:agent===SELF?'Andy Ai':'Other Ai',
 state:history.at(-1)?.state ?? null,version:history.length,updated_at:null,received:true,
 memo:history.at(-1)?.state==='ack' ? history.at(-1).memo ?? null : null,history})
function run(label,{guidance=GUIDANCE,work=()=>[],readbackFails=false,expires=false}={}){
 let expired=expires
 const calls=[],sends=[],spawns=[],logs=[]
 let receipt=0
 const hostStore=new Map()
 const $={
  state:{get:async ref=>{const held=hostStore.get(ref.plugin+'/'+ref.key);return {value:held?structuredClone(held.value):undefined,version:held?.version||0}},
   set:async (ref,value)=>{const name=ref.plugin+'/'+ref.key,held=hostStore.get(name)||{version:0};hostStore.set(name,{value:structuredClone(value),version:held.version+1});return {isSet:true,version:held.version+1}}},
  session:{model:async()=>'fixture-main',id:async()=>'parent-'+label,append:async()=>({uuid:'a'}),messages:async()=>[],
   send:async args=>{sends.push(args);return {isDelivered:true}}},
  prompt:{submit:async()=>({})},
  mcp:{connect:async()=>({}),call:async(server,name,args)=>{
   calls.push({server,name,args})
   const text=value=>({content:[{type:'text',text:JSON.stringify(value)}]})
   if(server==='walkie'&&name==='walkie_read_messages') return text({messages:[root]})
   if(server==='walkie'&&name==='walkie_identity') return text({user:{id:SELF,display_name:'Andy Ai',principal_type:'agent'},company_id:'c'})
   if(server==='walkie'&&name==='walkie_call_tool'&&args?.operation_id==='messages.work.get'){
    if(readbackFails) return {isError:true,content:[{type:'text',text:'walkie unavailable'}]}
    // walkie_call_tool wraps the operation result in MCP content.
    return text({content:[{type:'text',text:JSON.stringify(work(sends.length))}]})}
   if(server==='walkie'&&name==='walkie_call_tool') return text([])
   if(server==='walkie') return text({ok:true})
   if(name==='work_side_claim'){
    const claim=receipt++?null:{receipt_id:'e'.repeat(64),generation:'1'.repeat(32),text:eventText(guidance),event_key:'source:one',
     lease_until:Date.now()/1000+120,source_read:{server:'walkie',classification:'thread',conversation_id:CONVERSATION,
      root_id:'40',target_message_id:'41',thread_root_id:'40'},route:{source_kind:'external_event'},
     workload:{version:3,truncated:false,nodes:[],queue_order:[]}}
    return text({claim,settings:{max_active:2},handoff_replies:[],main_wakes:[]})}
   if(name==='work_side_renew'){
    expired=false
    return text({receipt_id:args._host.receipt_id,generation:args._host.generation,lease_until:Date.now()/1000+120})}
   if(name==='work_side_understanding'&&args.state==='prepare'){
    if(expired) return {isError:true,content:[{type:'text',text:'side claim expired'}]}
    // The router's shape refusal of an empty summary (side_work_decision.py).
    if(args.decision?.summary==='') return {isError:true,content:[{type:'text',text:'side decision needs bounded summary and reply'}]}
    return text({state:'submitting',attempt_id:'u1',server:'walkie',tool:'walkie_send_message',arguments:{text:args.understanding}})}
   if(name==='work_side_understanding') return text({receipt:{receipt_id:'e'.repeat(64),stage:'received'}})
   if(name==='work_side_resolve')
    return text({receipt:{receipt_id:'e'.repeat(64),stage:'processed'},result:{action:args.decision.action,duplicate:false,version:4}})
   if(name==='work_side_reply') return text({receipt:{receipt_id:'e'.repeat(64),stage:'processed'},arguments:null})
   if(name==='work_side_fail') return text({receipt:{receipt_id:'e'.repeat(64),stage:'received'}})
   return text({})}},
  tool:{call:async args=>{calls.push({toolCall:args});return {}}},
  agent:{list:async()=>[],spawn:async args=>{spawns.push(args);return {agentId:'actor-'+label}}},
  env:{get:async()=>'x'.repeat(40)},
  usage:{},ui:{log:text=>{logs.push(String(text))},invalidate:()=>{},open:async()=>{}},clock:{after:()=>({cancel(){}})},
 }
 return {$,calls,sends,spawns,logs}
}
const good=JSON.stringify({understanding:'You want the CSV export queued as new Work.',
 decision:{action:'create',kind:'request',title:'CSV export',summary:'queue CSV export',reply:'queued',refs:['41','40']},
 expected_version:3,sent_via:['mcp__walkie__walkie_call_tool']})
const acked=[{state:'ack',version:1,at:'2026-11-27T06:00:00Z',memo:'새 Work로 큐 등록'}]
// The receipt id 'e'*64 as the event's own ACK idempotency key (UUID layout).
const CLIENT='eeeeeeee-eeee-4eee-aeee-eeeeeeeeeeee'
const prose='결정 JSON 없이 끝난 답입니다.'
const badSummary=JSON.stringify({...JSON.parse(good),decision:{...JSON.parse(good).decision,summary:''}})
const out={}
async function scenario(label,answers,options){
 const host=run(label,options)
 await startSide(host.$); await pollSide(host.$,'claude:parent-'+label)
 for (const answer of answers) await completeSide(host.$,{agentId:'actor-'+label,reason:'answer',answer,usage:null})
 const {calls,sends,spawns,logs}=host
 let prompt={}
 try { prompt=JSON.parse(spawns[0]?.prompt || '{}') } catch {}
 out[label]={
  promptAck:prompt.ack || null,
  sends:sends.map(s=>({to:s.to?.agentId,ackText:/ACK와 note를 먼저 등록/.test(s.text),
   namesMessage:/41/.test(s.text),allowsTools:!/Do not call tools/.test(s.text),namesClient:s.text.includes(CLIENT)})),
  shortfallLogs:logs.filter(text=>/without the ACK|without the wait check/.test(text)),
  readbacks:calls.filter(c=>c.name==='walkie_call_tool'&&c.args?.operation_id==='messages.work.get')
   .map(c=>c.args.arguments),
  resolves:calls.filter(c=>c.name==='work_side_resolve').length,
  failures:calls.filter(c=>c.name==='work_side_fail').map(c=>({category:c.args.category,reason:c.args.reason}))}
}
// No ACK of this agent yet (another agent's ACK does not count): one correction
// naming the ACK, the actor ACKs, and the second answer commits.
await scenario('missing_then_acked',[good,good],{work:sent=>sent ? [row(OTHER,acked),row(SELF,acked)] : [row(OTHER,acked),row(SELF,[])]})
// Never ACKed: one correction naming the ACK; a second answer without it still
// commits its decision (Andy 2026-11-27 9d0578fb1be6: a release of n2967/n2968
// was dropped as invalid_answer) and the host logs what was missing.
await scenario('missing_twice',[good,good],{work:()=>[row(SELF,[])]})
// A gate correction does not use up the correction for the answer's shape
// (Andy 2026-11-27 b386e8da7270): ACK missing, then prose, then a decision.
await scenario('ack_then_prose',[good,prose,good],{work:()=>[row(SELF,[])]})
// The shape refusal after a gate correction still gets its own correction
// (b386e8da7270 was an over-long summary in the answer to a wait-check correction).
await scenario('ack_then_bad_summary',[good,badSummary,good],{work:()=>[row(SELF,[])]})
// An ACK without its note is not the required ACK.
await scenario('ack_without_memo',[good,good],{work:()=>[row(SELF,[{state:'ack',version:1,at:'x',memo:null}])]})
// Already ACKed with a memo (a later state keeps the ACK in history): commits at once.
await scenario('acked',[good],{work:()=>[row(SELF,[...acked,{state:'processing',version:2,at:'y',memo:null}])]})
// No ACK in the guidance: no readback, nothing changes.
await scenario('no_guidance',[good],{guidance:null})
// The readback itself fails: that proves nothing missing, so the decision commits.
await scenario('readback_unavailable',[good],{readbackFails:true})
await scenario('expired_before_prepare',[good],{work:()=>[row(SELF,acked)],expires:true})
console.log(JSON.stringify(out))

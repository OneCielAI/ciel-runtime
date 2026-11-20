// Andy 2026-11-18: one corrective turn before a side answer's shape failure
// counts. Drives side-intake.js's completeSide with a scripted router and a
// native fork that accepts one $.session.send message.
import {startSide,pollSide,completeSide} from './side-intake.js'
const CONVERSATION='34886995-4a66-4527-a618-814e9cb0a5bf'
const root={id:'40',conversation_id:CONVERSATION,text:'thread root question',thread_root_id:null}
const replies=[{id:'41',conversation_id:CONVERSATION,text:'please queue the CSV export',thread_root_id:'40'}]
function run(label,{refuseAt=null,decisionFields=null}={}){
 const calls=[],sends=[]
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
   if(server==='walkie'&&name==='walkie_call_tool') return text(replies)
   if(server==='walkie') return text({ok:true})
   if(name==='work_side_claim'){
    const claim=receipt++?null:{receipt_id:'e'.repeat(64),generation:'1'.repeat(32),text:'event',event_key:'source:one',
     lease_until:Date.now()/1000+120,source_read:{server:'walkie',classification:'thread',conversation_id:CONVERSATION,
      root_id:'40',target_message_id:'41',thread_root_id:'40'},route:{source_kind:'external_event'},
     workload:{version:3,truncated:false,nodes:[],queue_order:[]},
     ...(decisionFields ? {decision_fields:decisionFields} : {})}
    return text({claim,settings:{max_active:2},handoff_replies:[],main_wakes:[]})}
   if(name==='work_side_understanding'&&args.state==='prepare'){
    // The router validates the decision before it saves anything or sends.
    if(refuseAt==='prepare'&&args.decision.kind==='status'&&args.decision.action==='create')
     return {isError:true,content:[{type:'text',text:'side decision kind does not match its action'}]}
    if(refuseAt==='refs'&&!args.decision.refs)
     return {isError:true,content:[{type:'text',text:'decision refs required: give the source ids in refs (target message, thread root, parent, conversation)'}]}
    return text({state:'submitting',attempt_id:'u1',server:'walkie',tool:'walkie_send_message',arguments:{text:args.understanding}})}
   if(name==='work_side_understanding') return text({receipt:{receipt_id:'e'.repeat(64),stage:'received'}})
   if(name==='work_side_resolve'){
    if(refuseAt==='resolve') return {isError:true,content:[{type:'text',text:'existing Work identity required'}]}
    return text({receipt:{receipt_id:'e'.repeat(64),stage:'processed'},result:{action:args.decision.action,duplicate:false,version:4}})}
   if(name==='work_side_reply') return text({receipt:{receipt_id:'e'.repeat(64),stage:'processed'},arguments:null})
   if(name==='work_side_fail') return text({receipt:{receipt_id:'e'.repeat(64),stage:'received'}})
   return text({})}},
  tool:{call:async args=>{calls.push({toolCall:args});return {}}},
  agent:{list:async()=>[],spawn:async()=>({agentId:'actor-'+label})},
  env:{get:async()=>'x'.repeat(40)},
  usage:{},ui:{log:()=>{},invalidate:()=>{},open:async()=>{}},clock:{after:()=>({cancel(){}})},
 }
 return {$,calls,sends}
}
const good=JSON.stringify({understanding:'You want the CSV export queued as new Work.',
 decision:{action:'create',kind:'request',title:'CSV export',summary:'queue CSV export',reply:'queued'},expected_version:3})
const withRefs=JSON.stringify({understanding:'You want the CSV export queued as new Work.',
 decision:{action:'create',kind:'request',title:'CSV export',summary:'queue CSV export',reply:'queued',refs:['41','40']},expected_version:3})
const ROUTER_FIELDS=['action','ask_main','complaint','item_id','kind','no_reply','note','priority','queue_position','refs','relay','release_waits','reply','summary','time_limit','title']
const wrongPair=JSON.stringify({understanding:'You want the CSV export queued as new Work.',
 decision:{action:'create',kind:'status',summary:'queue CSV export',reply:'queued'},expected_version:3})
const summarize=(label,{$,calls,sends},actorGone)=>({label,
 sends:sends.map(s=>({to:s.to?.agentId,mentionsError:/Exact error: /.test(s.text)})),
 prepares:calls.filter(c=>c.name==='work_side_understanding'&&c.args.state==='prepare').length,
 understandingSent:calls.filter(c=>c.server==='walkie'&&c.name==='walkie_send_message').length,
 resolves:calls.filter(c=>c.name==='work_side_resolve').length,
 sentRefs:calls.filter(c=>c.name==='work_side_resolve'||(c.name==='work_side_understanding'&&c.args.state==='prepare'))
  .map(c=>c.args.decision.refs ?? null),
 failures:calls.filter(c=>c.name==='work_side_fail').map(c=>({category:c.args.category,reason:c.args.reason})),
 stopped:calls.filter(c=>c.toolCall?.tool==='TaskStop').length,actorGone})
const out={}
async function scenario(label,answers,options){
 const host=run(label,options)
 await startSide(host.$); await pollSide(host.$,'claude:parent-'+label)
 const handled=[]
 for (const answer of answers) handled.push(await completeSide(host.$,{agentId:'actor-'+label,reason:'answer',answer,usage:null}))
 out[label]={...summarize(label,host),handled}
}
// No decision JSON, then a valid answer: one correction, one commit, no failure.
await scenario('no_json_then_valid',['I think it should be queued.',good])
// Twice no decision JSON: one correction, then the second refusal counts.
await scenario('no_json_twice',['nothing here','still nothing'])
// The router refuses the kind/action pair at prepare (nothing saved or sent):
// one correction, then the valid answer commits and the understanding goes out once.
await scenario('prepare_refused_then_valid',[wrongPair,good],{refuseAt:'prepare'})
// Refused after the router saved the proposal and the understanding went out:
// no correction (it would be a second proposal); the failure counts at once.
await scenario('resolve_refused',[good],{refuseAt:'resolve'})
// 2026-11-20: a queue decision without refs is refused at prepare (nothing
// saved or sent): one correction, then the answer with refs commits.
await scenario('refs_refused_then_valid',[good,withRefs],{refuseAt:'refs',decisionFields:ROUTER_FIELDS})
// A router that lists refs gets them; one from before refs lists no fields and
// refuses an unknown one, so it gets the decision without refs.
await scenario('router_with_refs',[withRefs],{decisionFields:ROUTER_FIELDS})
await scenario('router_before_refs',[withRefs])
console.log(JSON.stringify(out))

// Andy 2026-11-27 (#162105, n2892): Eric reported in IT Hotline 162796 (root
// 162095) that SSH card 162105 was already approved. n2892 waited on exactly
// that approval with a release_hint lookup; the side actor answered "update"
// without checking it (its correction said "Do not call tools"), so the item
// stayed blocked. Drives side-intake.js's startSide/pollSide/completeSide with a
// scripted router: the prompt names the waiting item, an unchecked answer is
// corrected once with the item's lookup, and a check or a release commits.
import {startSide,pollSide,completeSide} from './side-intake.js'
const CONVERSATION='8319baa4-e5b6-4374-95b8-0e1aed0b60f9'
const WAITING_ON='Daniel Yun SSH passkey approval on IT Hotline 162105 (vmi3610452 oneciel, req d353d85a)'
const HINT={condition:"ssh_access.status(request_id='d353d85a-b52d-4e7b-a193-9586fac303c7') returns status approved (or rejected/expired)",
 lookup:"Call ssh_access.status(request_id='d353d85a-b52d-4e7b-a193-9586fac303c7') or read IT Hotline "+CONVERSATION+' message 162105 (root 162095).'}
const NODES=[
 {id:'n2892',parent_id:null,title:'P0 vmi3610452 rootless Docker base image 점검·복구 (162095)',status:'pending',priority:'P1',
  phase:'planning',waiting_on:WAITING_ON,release_hint:HINT,refs:['162095',CONVERSATION,'161135']},
 // Same IT Hotline room, another thread: the shared conversation id never makes it a target.
 {id:'n2771',parent_id:null,title:'[56858] 대체 승인자',status:'in_progress',priority:'P2',phase:'executing',
  waiting_on:'JUN KIM 108458 answer',refs:['56858',CONVERSATION]}]
const BODY='@Andy Ai P0 161135 선결 건 162095 확인 부탁드립니다. 1. 162105 SSH 승인 완료 상태: Daniel Yun님이 '
 +'2026-11-26 14:21:04 UTC에 이미 승인하셨습니다(grant c88423e9-c4d5-4f58-ab82-6d0b1f02bb41).'
const eventText=(message,root)=>JSON.stringify({specversion:'1.0',id:CONVERSATION+':'+message,type:'com.ciel.walkie.support.message',
 source:'http://walkie.invalid/conversations/'+CONVERSATION,
 data:{conversation:CONVERSATION,message_id:message,thread_root_id:root,reply_to_message_id:root,
  text:root==='162095' ? BODY : 'unrelated note in another thread'}})
function run(label,{message='162796',root='162095'}={}){
 const calls=[],sends=[],spawns=[]
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
   if(server==='walkie'&&name==='walkie_read_messages')
    return text({messages:[{id:root,conversation_id:CONVERSATION,text:'thread root',thread_root_id:null}]})
   if(server==='walkie') return text({ok:true})
   if(name==='work_side_claim'){
    const claim=receipt++?null:{receipt_id:'e'.repeat(64),generation:'1'.repeat(32),text:eventText(message,root),event_key:'source:one',
     lease_until:Date.now()/1000+120,source_read:{server:'walkie',classification:'thread',conversation_id:CONVERSATION,
      root_id:root,target_message_id:message,thread_root_id:root},route:{source_kind:'external_event'},
     workload:{version:12552,truncated:false,nodes:structuredClone(NODES),queue_order:[]}}
    return text({claim,settings:{max_active:2},handoff_replies:[],main_wakes:[]})}
   if(name==='work_side_understanding'&&args.state==='prepare')
    return text({state:'submitting',attempt_id:'u1',server:'walkie',tool:'walkie_send_message',arguments:{text:args.understanding}})
   if(name==='work_side_understanding') return text({receipt:{receipt_id:'e'.repeat(64),stage:'received'}})
   if(name==='work_side_resolve')
    return text({receipt:{receipt_id:'e'.repeat(64),stage:'processed'},result:{action:args.decision.action,duplicate:false,version:12553}})
   if(name==='work_side_reply') return text({receipt:{receipt_id:'e'.repeat(64),stage:'processed'},arguments:null})
   if(name==='work_side_fail') return text({receipt:{receipt_id:'e'.repeat(64),stage:'received'}})
   return text({})}},
  tool:{call:async args=>{calls.push({toolCall:args});return {}}},
  agent:{list:async()=>[],spawn:async args=>{spawns.push(args);return {agentId:'actor-'+label}}},
  env:{get:async()=>'x'.repeat(40)},
  usage:{},ui:{log:()=>{},invalidate:()=>{},open:async()=>{}},clock:{after:()=>({cancel(){}})},
 }
 return {$,calls,sends,spawns}
}
const answer=(extra={},decision={})=>JSON.stringify({understanding:'Eric reports SSH card 162105 already approved; n2892 waits on it.',
 decision:{action:'update',kind:'request',item_id:'n2892',summary:'162105 approval reported',reply:'recorded on n2892',
  refs:['162796','162095',CONVERSATION,'162105'],priority:'P0',...decision},expected_version:12552,...extra})
// The 162105 answer as it was: an update with no release and no check.
const unchecked=answer()
const checkedOpen=answer({wait_checks:[{id:'n2892',lookup:"ssh_access.status(request_id='d353d85a-…')",observed:'pending',met:false}]})
const checkedMet=answer({wait_checks:[{id:'n2892',lookup:'read message 162105',observed:'approved 2026-11-26T14:21:04Z',met:true}]})
const released=answer({},{release_waits:[{id:'n2892',waiting_on:WAITING_ON}]})
// The 162105 actor's first turn: prose, no decision JSON.
const prose='이번 턴에는 사람이 직접 작성한 요청이 없고 결정 JSON을 그대로 출력하지 않습니다.'
const out={}
async function scenario(label,answers,options){
 const host=run(label,options)
 await startSide(host.$); await pollSide(host.$,'claude:parent-'+label)
 for (const value of answers) await completeSide(host.$,{agentId:'actor-'+label,reason:'answer',answer:value,usage:null})
 const {calls,sends,spawns}=host
 let prompt={}
 try { prompt=JSON.parse(spawns[0]?.prompt || '{}') } catch {}
 const resolves=calls.filter(c=>c.name==='work_side_resolve')
 out[label]={
  waitItems:(prompt.wait_items || []).map(item=>({id:item.id,waiting_on:item.waiting_on,lookup:item.release_hint?.lookup || null})),
  waitRule:typeof prompt.wait_check === 'string' && /wait_checks/.test(prompt.wait_check),
  sends:sends.map(s=>({to:s.to?.agentId,waitCheck:/wait_checks/.test(s.text),namesItem:/n2892/.test(s.text),
   namesLookup:/ssh_access\.status/.test(s.text),namesOther:/n2771/.test(s.text),
   forbidsTools:/Do not call tools/.test(s.text),allowsReads:/read-only|읽기 전용/.test(s.text)})),
  resolves:resolves.length,
  released:resolves.map(c=>c.args.decision.release_waits || []),
  decisionKeys:resolves.map(c=>Object.keys(c.args.decision).sort()),
  failures:calls.filter(c=>c.name==='work_side_fail').map(c=>({category:c.args.category,reason:c.args.reason}))}
}
await scenario('unchecked_then_checked',[unchecked,checkedOpen])
await scenario('unchecked_twice',[unchecked,unchecked])
await scenario('met_not_released',[checkedMet,released])
await scenario('released',[released])
await scenario('checked',[checkedOpen])
const created=JSON.stringify({understanding:'A new note in another thread asks for a new task.',
 decision:{action:'create',kind:'request',title:'new task',summary:'queue it',reply:'queued',refs:['99001','99000',CONVERSATION]},
 expected_version:12552})
await scenario('other_thread',[created],{message:'99001',root:'99000'})
await scenario('prose_first',[prose,checkedOpen])
console.log(JSON.stringify(out))

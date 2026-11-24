// A side actor whose claim lease lapsed while it was still working (the host's
// pump did not run for minutes, Andy 2026-11-23 23:36-23:42Z) is kept: the next
// poll renews its own claim instead of stopping it and claiming the event again.
// argv[2]:
//  "revived"   the renew revives the lapsed claim (no other claim took it);
//  "refused"   the router refuses the renew (another claim took the event), so
//              the actor is stopped as before;
//  "reoffered" the lease lapses while the claim call itself is in flight, and
//              the router hands this host its own claim back (same generation):
//              the event is not started a second time.
//  "timedout"  the lease lapsed and the renew got no answer (the engine's MCP
//              client gave up, as in the real-engine run of 2026-11-23); the
//              router then re-offers this host its own claim: the actor is
//              neither stopped nor started again.
import {startSide,pollSide,sideOwns} from './side-intake.js'
const mode=process.argv[2]||'revived'
const calls=[],spawns=[]
const listed=new Map()
// The router (side_intake_processing.renew/claim): a revived claim is not
// offered again; only an event no live claim holds is.
let revived=false
const event={receipt_id:'a'.repeat(64),generation:'1'.repeat(32),text:'question',event_key:'source:one',
  lease_until:Date.now()/1000+120,route:{channel:'external:source',sender_id:'sender',source_kind:'external_event'},
  workload:{version:1,truncated:false,active_root:null,queue_order:[],nodes:[]}}
let events=[event]
let stallClaim=false
let timedOut=false
const text=value=>({content:[{type:'text',text:JSON.stringify(value)}]})
const $={
 state:{get:async()=>({value:undefined,version:0}),set:async()=>({isSet:true,version:1})},
 session:{model:async()=>'fixture-main',id:async()=>'parent',append:async()=>({uuid:'u'}),messages:async()=>[]},
 prompt:{compose:async()=>({sections:[{id:'intro',text:'MAIN',scope:'shared'}]}),submit:async args=>({text:args.text})},
 mcp:{connect:async()=>({}),call:async(server,name,args)=>{
  calls.push(name)
  if(name==='work_side_claim'){
   if(stallClaim){
    // The call reaches the router only after the lease lapsed under the
    // working actor; the router re-offers the same event to the same owner.
    stallClaim=false
    sideOwns('actor1').current.lease_until=Date.now()/1000-300
    return text({claim:{...event,lease_until:Date.now()/1000+120},settings:{max_active:2},handoff_replies:[],main_wakes:[]})
   }
   return text({claim:revived?null:events.shift()||null,settings:{max_active:2},handoff_replies:[],main_wakes:[]})
  }
  if(name==='work_side_renew'){
   if(mode==='timedout' && !timedOut){timedOut=true;throw new Error('MCP error -32001: Request timed out')}
   if(mode==='refused') return {isError:true,content:[{type:'text',text:'stale side claim'}]}
   revived=true
   return text({receipt_id:args._host.receipt_id,generation:args._host.generation,lease_until:Date.now()/1000+120})
  }
  return text({})
 }},
 tool:{call:async args=>{calls.push('TaskStop:'+args.task_id);if(listed.has(args.task_id)) listed.set(args.task_id,'killed');return {result:{}}}},
 agent:{list:async()=>[...listed].map(([id,status])=>({id,status})),register:async()=>({}),spawn:async args=>{
  spawns.push(args);const id='actor'+spawns.length;listed.set(id,'running');return {agentId:id}}},
 env:{get:async()=>'x'.repeat(40)},
 ui:{log:()=>{},status:()=>{},invalidate:()=>{}},clock:{after:(ms,callback)=>{setTimeout(callback,0);return {cancel(){}}}},
}
await startSide($)
await pollSide($,'claude:parent')
if(mode==='reoffered'){
 stallClaim=true
 await pollSide($,'claude:parent')
 // The next tick finds a live lease: nothing to renew, nothing to stop.
 await pollSide($,'claude:parent')
}else if(mode==='timedout'){
 sideOwns('actor1').current.lease_until=Date.now()/1000-300
 events=[{...event,lease_until:Date.now()/1000+120}]
 await pollSide($,'claude:parent')
}else{
 // The lease lapsed while the actor kept working: no poll ran for its renew.
 sideOwns('actor1').current.lease_until=Date.now()/1000-300
 events=[{...event,generation:'2'.repeat(32),lease_until:Date.now()/1000+120}]
 await pollSide($,'claude:parent')
}
await new Promise(resolve=>setTimeout(resolve,50))
const actor=sideOwns('actor1')
console.log(JSON.stringify({spawns:spawns.length,renews:calls.filter(c=>c==='work_side_renew').length,
  stopped:calls.filter(c=>c.startsWith('TaskStop:')),current:actor?.current?.receipt_id||null,
  lease_live:Boolean(actor?.current && actor.current.lease_until*1000>Date.now()),
  claims:calls.filter(c=>c==='work_side_claim').length}))

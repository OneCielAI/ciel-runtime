// A main wake prompt enters only once main is idle. Claims must keep running
// while it waits (Prody 2026-11-09: one awaited wake held every claim 63 min).
import {startSide,pollSide,sideSpawnInput} from './side-intake.js'
const submits=[],wakeCalls=[],spawns=[]
const releases=[]
let wakes=[],events=[],claims=0
const $={
 session:{model:async()=>'fixture-main',id:async()=>'parent',append:async()=>{},messages:async()=>[]},
 prompt:{submit:args=>{submits.push(args.text);return new Promise(resolve=>releases.push(()=>resolve({text:args.text})))}},
 mcp:{connect:async()=>({}),call:async(server,name,args)=>{
  let value={}
  if(name==='work_side_claim'){
   claims++
   const claim=events.shift()||null
   if(claim) claim.workload={version:1,truncated:false,active_root:'n1',queue_order:[],nodes:[]}
   value={claim,settings:{model:'inherit',context_mode:'isolated'},handoff_replies:[],main_wakes:wakes}
  }
  if(name==='work_side_wake'){
   wakeCalls.push({event_id:args.event_id,state:args.state,attempt_id:args.attempt_id||null})
   value=args.state==='prepare' ? {state:'submitting',attempt_id:'attempt-'+args.event_id} : {state:args.state}
  }
  if(name==='work_side_renew') value={lease_until:Date.now()/1000+120}
  return {content:[{type:'text',text:JSON.stringify(value)}]}
 }},
 tool:{list:async()=>[],call:async()=>({text:'stopped'})},
 agent:{list:async()=>[],register:async()=>{},spawn:async args=>{
   const rewritten=sideSpawnInput({...args,parentAgentId:undefined},{origin:{plugin:'ciel-work'}}) || args
   spawns.push(rewritten.description);return {agentId:'actor'+spawns.length}}},
 env:{get:async key=>key==='CIEL_RUNTIME_DIRECT_GATEWAY_ACTIVE'?'0':'x'.repeat(40)},
 ui:{log:()=>{},status:()=>{},invalidate:()=>{}},clock:{after:()=>({cancel(){}})},
}
const event=id=>({receipt_id:id.repeat(64),generation:'g'+id,text:'event '+id,event_key:'source:'+id,
 lease_until:Date.now()/1000+120,route:{channel:'external:source',sender_id:'sender',source_kind:'external_event'}})
const timed=promise=>Promise.race([promise.then(()=>'returned'),new Promise(resolve=>setTimeout(()=>resolve('blocked'),2000))])
await startSide($)
// Main is busy: the first wake's submit stays pending.
wakes=[{event_id:'w1',item_id:'n1',kind:'p1',summary:'first'},{event_id:'w2',item_id:'n2',kind:'p1',summary:'second'}]
const firstPoll=await timed(pollSide($,'claude:parent'))
// Both wakes are offered again; an event arrives meanwhile and must still be claimed.
events.push(event('a'))
const secondPoll=await timed(pollSide($,'claude:parent'))
const whileBusy={submits:submits.length,spawns:[...spawns],claims}
// Main goes idle: the first wake enters and is recorded queued.
releases.shift()()
await new Promise(resolve=>setTimeout(resolve,20))
wakes=[{event_id:'w2',item_id:'n2',kind:'p1',summary:'second'}]
await timed(pollSide($,'claude:parent'))
console.log(JSON.stringify({firstPoll,secondPoll,whileBusy,submits:submits.map(t=>JSON.parse(t.slice(t.indexOf('{'),t.indexOf('}')+1)).event_id),wakeCalls}))

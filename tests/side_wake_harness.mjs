// A main wake prompt enters only once main is idle. Claims must keep running
// while it waits (Prody 2026-11-09: one awaited wake held every claim 63 min).
import {startSide,pollSide,sideSpawnInput} from './side-intake.js'
const submits=[],wakeCalls=[],spawns=[]
const releases=[]
const wait=async test=>{for(let i=0;i<400&&!test();i++) await new Promise(resolve=>setTimeout(resolve,5))}
let wakes=[],events=[],claims=0
// The engine's host-held $.state (version + ifVersion compare-and-set).
const hostStore=new Map()
const $={
 state:{get:async ref=>{const held=hostStore.get(ref.plugin+'/'+ref.key);return {value:held?structuredClone(held.value):undefined,version:held?.version||0}},set:async (ref,value,options={})=>{const name=ref.plugin+'/'+ref.key,held=hostStore.get(name)||{version:0};if(options.ifVersion!==undefined&&options.ifVersion!==held.version)return {isSet:false,version:held.version};hostStore.set(name,{value:structuredClone(value),version:held.version+1});return {isSet:true,version:held.version+1}}},
 session:{model:async()=>'fixture-main',id:async()=>'parent',append:async()=>{},messages:async()=>[]},
 prompt:{submit:args=>{submits.push(args.text);return new Promise(resolve=>releases.push({text:args.text,go:()=>resolve({text:args.text})}))}},
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
   // Protocol-2 election: intent then start; only started may deliver.
   const attempt='attempt-'+args.event_id
   value=args.state==='prepare' ? {state:'submitting',phase:'intent',attempt_id:attempt,owner_nonce:args.owner_nonce,channel:args.channel}
     : args.state==='start' ? {state:'submitting',phase:'started',attempt_id:attempt,owner_nonce:args.owner_nonce,channel:args.channel}
     : {state:args.state}
   value={protocol:2,...value}
  }
  if(name==='work_side_renew') value={lease_until:Date.now()/1000+120}
  return {content:[{type:'text',text:JSON.stringify(value)}]}
 }},
 tool:{list:async()=>[],call:async()=>({text:'stopped'})},
 agent:{list:async()=>[],register:async()=>{},spawn:async args=>{
   // The engine marks a native fork spawn with fork:true (owned probe
   // named-fork-d48887c3); the side hook authorizes it unchanged.
   const input={...args,fork:true,parentAgentId:undefined}
   const rewritten=sideSpawnInput(input,{origin:{plugin:'ciel-work'}})
   if(rewritten!==input) throw new Error('side fork not authorized: '+JSON.stringify(rewritten))
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
// Each started wake issues its own prompt at once (per key, no module FIFO);
// the host-state claim and attempt_ref digest run first, so wait for both.
await wait(()=>submits.length>=2)
const whileBusy={submits:submits.length,spawns:[...spawns],claims}
// Main goes idle: the first wake enters and is recorded queued.
releases.find(entry=>entry.text.includes('"event_id":"w1"')).go()
await new Promise(resolve=>setTimeout(resolve,20))
wakes=[{event_id:'w2',item_id:'n2',kind:'p1',summary:'second'}]
await timed(pollSide($,'claude:parent'))
console.log(JSON.stringify({firstPoll,secondPoll,whileBusy,submits:submits.map(t=>JSON.parse(t.slice(t.indexOf('{'),t.indexOf('}')+1)).event_id),wakeCalls}))

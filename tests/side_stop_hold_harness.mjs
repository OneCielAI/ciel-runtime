import * as side from './side-intake.js'
const {startSide,pollSide}=side
// Absent before the Stop-hold fix: the red run reports it as not resumed.
const sidePersonInput=side.sidePersonInput||(()=>'missing')
// Scenarios: "stop" (Claude Code refuses $.agent.spawn after a Stop until a
// person sends input; Celly 2026-11-25) and "fresh" (one ordinary side-fresh
// actor plus the reserved P0/P1 slot; Andy 2026-11-25).
const scenario=process.argv[2]||'stop'
const HOLD='the session was stopped and nobody has sent input since'
let version=7,deny='',clockShift=0
const realNow=Date.now
Date.now=()=>realNow()+clockShift
const event=(ch,priority)=>({receipt_id:ch.repeat(64),generation:('2'+ch).repeat(16),text:'event '+ch,event_key:'source:'+ch,
 route:{},...(priority ? {source_priority:priority} : {})})
// The router keeps an unspawned claim: its lease lapses and the same host gets it back.
const queue=[event('a','P3'),event('b','P1'),event('c','P0')]
const calls=[],spawns=[],timers=[],logs=[]
const hostStore=new Map()
const $={
 state:{get:async ref=>{const held=hostStore.get(ref.plugin+'/'+ref.key);return {value:held?structuredClone(held.value):undefined,version:held?.version||0}},set:async (ref,value,options={})=>{const name=ref.plugin+'/'+ref.key,held=hostStore.get(name)||{version:0};if(options.ifVersion!==undefined&&options.ifVersion!==held.version)return {isSet:false,version:held.version};hostStore.set(name,{value:structuredClone(value),version:held.version+1});return {isSet:true,version:held.version+1}}},
 session:{model:async()=>'fixture-main',id:async()=>'parent',info:async()=>({transcriptPath:'C:/fixture/projects/parent.jsonl'}),append:async args=>{calls.push({append:args.message});return {uuid:'append'}},messages:async()=>[]},
 prompt:{compose:async()=>({sections:[{id:'intro',text:'MAIN SYSTEM PROMPT',scope:'shared'}]}),submit:async args=>({text:args.text})},
 mcp:{connect:async()=>({}),call:async(server,name,args)=>{
  calls.push({name,args}); let value={}
  if(name==='work_side_claim'){
   const index=args.urgent_only ? queue.findIndex(e=>e.source_priority==='P0'||e.source_priority==='P1') : (queue.length ? 0 : -1)
   const claim=index>=0 ? {...queue[index],lease_until:Date.now()/1000+120,
     workload:{version,truncated:false,active_root:'n1',queue_order:[],nodes:[]}} : null
   value={claim,settings:{model:'inherit',max_active:2,session:scenario==='fresh' ? 'fresh' : 'fork'},handoff_replies:[]}
  }
  if(name==='work_side_renew') value={lease_until:Date.now()/1000+120}
  if(name==='work_side_fail'){const i=queue.findIndex(e=>e.receipt_id===args._host.receipt_id);if(i>=0) queue.splice(i,1)
   value={receipt:{receipt_id:args._host.receipt_id,stage:'received'}}}
  return {content:[{type:'text',text:JSON.stringify(value)}]}
 }},
 tool:{list:async()=>[],call:async args=>{calls.push({toolCall:args});return {text:'stopped'}}},
 agent:{list:async()=>spawns.map((s,i)=>({id:'actor'+(i+1),status:'running'})),register:async args=>({agent:'ciel-work:'+args.name}),
  spawn:async args=>{
   calls.push({spawn:JSON.parse(args.prompt).event_id[0]})
   if(deny) return {deny}
   spawns.push(args)
   const id=JSON.parse(args.prompt).event_id,i=queue.findIndex(e=>e.receipt_id===id);if(i>=0) queue.splice(i,1)
   return {agentId:'actor'+spawns.length}}},
 env:{get:async key=>key==='CIEL_RUNTIME_DIRECT_GATEWAY_ACTIVE'?'0':'x'.repeat(40)},
 ui:{log:text=>logs.push(text),status:()=>{},invalidate:()=>{}},clock:{after:(ms,callback)=>{if(ms<5000) timers.push(callback);return {cancel(){}}}},
}
const claims=()=>calls.filter(c=>c.name==='work_side_claim').length
const urgentFlags=()=>calls.filter(c=>c.name==='work_side_claim').map(c=>c.args.urgent_only===true)
const fails=()=>calls.filter(c=>c.name==='work_side_fail').map(c=>({category:c.args.category,reason:c.args.reason}))
await startSide($)
const steps=[]
const step=async(label)=>{await pollSide($,'claude:parent');steps.push({label,claims:claims(),fails:fails().length,spawned:spawns.length})}
if(scenario==='stop'){
 deny=HOLD
 await step('denied')            // claim a, spawn refused by the Stop hold
 await step('held')              // no claim while the hold stands
 const notPerson=sidePersonInput({origin:{kind:'task-notification'},text:'notice'})
 await step('notice-no-resume')  // a notification is not a person's input
 const person=sidePersonInput({origin:{kind:'composer'},text:'hello'})
 deny=''
 await step('person-settling')   // the engine is still taking the input in: no claim yet
 clockShift=4000
 await step('person-resumed')    // the next tick retries the held claim of a (no new claim); a spawns
 deny=HOLD
 await step('denied-again')      // b refused
 clockShift=6*60*1000
 await step('probe-still-held')  // one probe claim after the probe interval, refused again
 await step('held-after-probe')
 clockShift=12*60*1000+1000
 deny=''
 await step('probe-resumed')     // the next probe spawns b
 console.log(JSON.stringify({steps,notPerson,person,fails:fails(),logs,
  spawned:spawns.map(s=>JSON.parse(s.prompt).event_id[0])}))
} else {
 await step('first')             // a (P3): the ordinary fresh actor
 await step('reserved')          // one running: reserved claim for P0/P1 only
 await step('full')              // two running: no claim
 console.log(JSON.stringify({steps,urgent:urgentFlags(),fails:fails(),
  spawned:spawns.map(s=>({event:JSON.parse(s.prompt).event_id[0],type:s.subagentType}))}))
}

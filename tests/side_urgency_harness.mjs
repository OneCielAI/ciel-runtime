import {startSide,pollSide,completeSide} from './side-intake.js'
let version=7
const event=(ch,priority)=>({receipt_id:ch.repeat(64),generation:'g'+ch,text:'event '+ch,event_key:'source:'+ch,
 lease_until:Date.now()/1000+120,route:{},...(priority ? {source_priority:priority} : {})})
const queue=[event('a','P3'),event('b',null),event('c','P1'),event('d','P1')]
const calls=[],spawns=[],timers=[]
const $={
 session:{model:async()=>'fixture-main',id:async()=>'parent',append:async args=>calls.push({append:args.message}),messages:async()=>[]},
 mcp:{connect:async()=>({}),call:async(server,name,args)=>{
  calls.push({name,args}); let value={}
  if(name==='work_side_claim'){
   // The host's reserved claim returns only a P1 event.
   const index=args.urgent_only ? queue.findIndex(e=>e.source_priority==='P1') : 0
   const claim=index>=0 && queue.length ? queue.splice(index,1)[0] : null
   if(claim) claim.workload={version,truncated:false,active_root:'n1',queue_order:[],nodes:[]}
   value={claim,settings:{model:'inherit',context_mode:'full',max_active:2},handoff_replies:[]}
  }
  if(name==='work_side_resolve'){
   const d=args.decision
   value={receipt:{receipt_id:args._host.receipt_id,stage:'processed'},result:{action:d.action,
    item_id:d.action==='create' ? 'n9' : d.item_id,summary:d.summary,reply:d.reply,
    ...(d.action==='create' ? {priority:d.priority||'',title:d.title} : {}),duplicate:false,version:++version}}
  }
  if(name==='work_side_reply') value={receipt:{receipt_id:args.receipt_id,stage:'processed'},arguments:null}
  return {content:[{type:'text',text:JSON.stringify(value)}]}
 }},
 tool:{list:async()=>[{name:'mcp__mail__reply',mcp:true}],call:async args=>{calls.push({toolCall:args});return {text:'stopped'}}},
 agent:{list:async()=>[],register:async()=>{},spawn:async args=>{spawns.push(args);return {agentId:'actor'+spawns.length}}},
 env:{get:async key=>key==='CIEL_RUNTIME_DIRECT_GATEWAY_ACTIVE'?'0':'x'.repeat(40)},
 ui:{log:()=>{},status:()=>{},invalidate:()=>{}},clock:{after:(ms,callback)=>{timers.push(callback);return {cancel(){}}}},
}
const drain=async()=>{while(timers.length) await timers.shift()()}
const claims=()=>calls.filter(c=>c.name==='work_side_claim').map(c=>c.args.urgent_only===true)
await startSide($)
await pollSide($,'claude:parent')   // a -> ordinary slot 1
await pollSide($,'claude:parent')   // b -> ordinary slot 2
await pollSide($,'claude:parent')   // slots full: reserved claim takes P1 c
await pollSide($,'claude:parent')   // reserved slot busy: no claim at all
const whileFull=claims()
// The fork omits expected_version; it defaults to the snapshot it was given.
await completeSide($,{agentId:'actor3',reason:'answer',answer:JSON.stringify({decision:{action:'create',
 title:'Fix outage',note:'customer down',summary:'ACK sent; outage queued P1',reply:'ACK',priority:'P1'},sent_via:['mcp__mail__reply']})})
await drain()
await pollSide($,'claude:parent')   // reserved slot free again: P1 d
const ordinary=calls.filter(c=>c.append).length
await completeSide($,{agentId:'actor1',reason:'answer',answer:JSON.stringify({decision:{action:'answer',summary:'answered',reply:'ok'},expected_version:7})})
await drain()
console.log(JSON.stringify({
 whileFull,claims:claims(),spawned:spawns.map(s=>JSON.parse(s.prompt).event_id[0]),
 sourcePriority:spawns.map(s=>JSON.parse(s.prompt).source_priority||null),
 resolveVersions:calls.filter(c=>c.name==='work_side_resolve').map(c=>c.args.expected_version),
 appends:calls.filter(c=>c.append).map(c=>({type:c.append.type,text:c.append.content[0].text})),
 appendsAfterUrgent:ordinary,
}))

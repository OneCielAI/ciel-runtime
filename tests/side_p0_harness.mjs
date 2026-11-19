// A Walkie emergency reaches the side actor as P0 and its P0 decision is shown.
import {startSide,pollSide,completeSide} from './side-intake.js'
const activation=process.argv[2] || 'ok'
const event=(ch,priority)=>({receipt_id:ch.repeat(64),generation:('2'+ch).repeat(16),text:'event '+ch,event_key:'source:'+ch,
 lease_until:Date.now()/1000+120,route:{},source_priority:priority})
const queue=[event('a','P0')]
const calls=[],spawns=[],timers=[]
const hostStore=new Map()
const $={
 state:{get:async ref=>{const held=hostStore.get(ref.plugin+'/'+ref.key);return {value:held?structuredClone(held.value):undefined,version:held?.version||0}},set:async (ref,value,options={})=>{const name=ref.plugin+'/'+ref.key,held=hostStore.get(name)||{version:0};if(options.ifVersion!==undefined&&options.ifVersion!==held.version)return {isSet:false,version:held.version};hostStore.set(name,{value:structuredClone(value),version:held.version+1});return {isSet:true,version:held.version+1}}},
 session:{model:async()=>'fixture-main',id:async()=>'parent',append:async args=>calls.push({append:args.message}),messages:async()=>[]},
 mcp:{connect:async()=>({}),call:async(server,name,args)=>{
  calls.push({name,args}); let value={}
  if(name==='work_side_claim'){
   const claim=queue.length ? queue.shift() : null
   if(claim) claim.workload={version:7,truncated:false,active_root:'n1',queue_order:[],nodes:[]}
   value={claim,settings:{model:'inherit',context_mode:'full',max_active:2},handoff_replies:[]}
  }
  if(name==='work_side_resolve'){
   const d=args.decision
   value={receipt:{receipt_id:args._host.receipt_id,stage:'processed'},result:{action:d.action,item_id:'n9',
    summary:d.summary,reply:d.reply,title:d.title,priority:d.priority,p0_root_id:'n9',handoff:'not_required',
    p0:activation==='ok' ? {root_id:'n9',effective_priority:'P0'} : {root_id:'n9',error:'WorkParallelError: device ledger unavailable'},
    duplicate:false,version:8}}
  }
  if(name==='work_side_reply') value={receipt:{receipt_id:args.receipt_id,stage:'processed'},arguments:null}
  return {content:[{type:'text',text:JSON.stringify(value)}]}
 }},
 tool:{list:async()=>[],call:async args=>{calls.push({toolCall:args});return {text:'stopped'}}},
 agent:{list:async()=>[],register:async()=>{},spawn:async args=>{spawns.push(args);return {agentId:'actor'+spawns.length}}},
 env:{get:async key=>key==='CIEL_RUNTIME_DIRECT_GATEWAY_ACTIVE'?'0':'x'.repeat(40)},
 ui:{log:()=>{},status:()=>{},invalidate:()=>{}},clock:{after:(ms,callback)=>{timers.push(callback);return {cancel(){}}}},
}
const drain=async()=>{while(timers.length) await timers.shift()()}
await startSide($)
await pollSide($,'claude:parent')
const prompt=JSON.parse(spawns[0].prompt)
await completeSide($,{agentId:'actor1',reason:'answer',answer:JSON.stringify({understanding:'understood in my own words',decision:{action:'create',kind:'request',
 title:'Production outage',summary:'P0 outage queued',reply:'Outage reported',priority:'P0'}})})
await drain()
console.log(JSON.stringify({
 sourcePriority:prompt.source_priority||null,
 instructsP0:/source_priority가\s+P0/.test(prompt.instruction) && /P0~P5 힌트/.test(prompt.instruction),
 resolvedPriority:calls.filter(c=>c.name==='work_side_resolve').map(c=>c.args.decision.priority),
 appends:calls.filter(c=>c.append).map(c=>c.append.content[0].text),
}))

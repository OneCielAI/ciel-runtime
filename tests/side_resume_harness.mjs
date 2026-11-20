// A claim that carries a saved proposal (the model answered before a restart):
// no fork, the proposal is committed as is, and the final reply follows.
import {startSide,pollSide} from './side-intake.js'
const calls=[],spawns=[],timers=[]
const saved={understanding:'understood in my own words',expected_version:4,
 decision:{refs:['999'],action:'create',kind:'request',title:'retry fix',summary:'retry fix',reply:'queued',priority:'P3'}}
const mode=process.argv[2]||'ok'
let events=[{receipt_id:'e'.repeat(64),generation:'5'.repeat(32),text:'question',event_key:'source:resume',
 lease_until:Date.now()/1000+120,work_digest:'d'.repeat(64),route:{channel:'external:source',sender_id:'sender',source_kind:'external_event'},
 proposal:mode==='malformed'?{understanding:'x'}:saved}]
const hostStore=new Map()
const $={
 state:{get:async ref=>{const held=hostStore.get(ref.plugin+'/'+ref.key);return {value:held?structuredClone(held.value):undefined,version:held?.version||0}},set:async (ref,value,options={})=>{const name=ref.plugin+'/'+ref.key,held=hostStore.get(name)||{version:0};if(options.ifVersion!==undefined&&options.ifVersion!==held.version)return {isSet:false,version:held.version};hostStore.set(name,{value:structuredClone(value),version:held.version+1});return {isSet:true,version:held.version+1}}},
 session:{model:async()=>'fixture-main',id:async()=>'parent',append:async()=>({uuid:'a'}),messages:async()=>[]},
 prompt:{submit:async args=>({text:args.text})},
 mcp:{connect:async()=>({}),call:async(server,name,args)=>{
  calls.push({server,name,args}); let value={}
  if(name==='work_side_claim') value={claim:events.shift()||null,settings:{max_active:2},handoff_replies:[],main_wakes:[]}
  // The understanding was sent before the restart: prepare offers no send.
  if(name==='work_side_understanding') value={state:'submitted',receipt_id:args._host.receipt_id}
  if(name==='work_side_resolve') value={receipt:{receipt_id:args._host.receipt_id,stage:'processed'},result:{action:args.decision.action,priority:'P3',duplicate:false}}
  if(name==='work_side_reply') value={receipt:{receipt_id:args.receipt_id,stage:'processed'},state:'submitting',attempt_id:'r1',server:'walkie',tool:'walkie_send_message',arguments:{text:'final'}}
  if(name==='walkie_send_message') value={ok:true}
  if(name==='work_side_fail') value={receipt:{receipt_id:args._host.receipt_id,stage:'received'}}
  return {content:[{type:'text',text:JSON.stringify(value)}]}
 }},
 tool:{list:async()=>[],call:async args=>{calls.push({toolCall:args});return {text:'stopped'}}},
 agent:{list:async()=>[],register:async()=>{},spawn:async args=>{spawns.push(args);return {agentId:'actor'+spawns.length}}},
 env:{get:async key=>key==='CIEL_RUNTIME_DIRECT_GATEWAY_ACTIVE'?'0':'x'.repeat(40)},
 ui:{log:()=>{},status:()=>{},invalidate:()=>{}},clock:{after:(ms,callback)=>{if(ms<5000) timers.push(callback);return {cancel(){}}}},
}
await startSide($)
await pollSide($,'claude:parent')
while(timers.length) await timers.shift()()
console.log(JSON.stringify({
 spawns:spawns.length,
 order:calls.filter(c=>c.name&&c.name.startsWith('work_side_')&&c.name!=='work_side_claim'&&c.name!=='work_side_status').map(c=>c.name+(c.args.state?':'+c.args.state:'')),
 understanding:calls.filter(c=>c.name==='work_side_understanding').map(c=>({understanding:c.args.understanding,decision:c.args.decision,expected_version:c.args.expected_version})),
 resolve:calls.filter(c=>c.name==='work_side_resolve').map(c=>({decision:c.args.decision,expected_version:c.args.expected_version,generation:c.args._host.generation})),
 walkieSends:calls.filter(c=>c.name==='walkie_send_message').map(c=>c.args.text),
 failures:calls.filter(c=>c.name==='work_side_fail').map(c=>c.args.category),
}))

import {startSide,pollSide,sideReceipts} from './side-intake.js'

const kind=process.argv[2]
const generations={null:null,number:7,object:{value:'a'.repeat(32)},short:'a'.repeat(31),long:'a'.repeat(33),
  uppercase:'A'.repeat(32),nonhex:'g'.repeat(32),suffix:'a'.repeat(32)+'-x',exact:'a'.repeat(32)}
const event={receipt_id:'b'.repeat(64),text:'claim generation check',event_key:'source:generation',lease_until:Date.now()/1000+120,
  route:{channel:'external:source',sender_id:'sender',source_kind:'external_event'},
  workload:{version:1,truncated:false,active_root:'n1',queue_order:[],nodes:[]}}
if(kind!=='missing'&&kind!=='undefined') event.generation=generations[kind]
else if(kind==='undefined') event.generation=undefined
const spawns=[],calls=[],logs=[]
const hostStore=new Map()
const json=value=>({content:[{type:'text',text:JSON.stringify(value)}]})
const $={
  state:{get:async ref=>{const held=hostStore.get(ref.plugin+'/'+ref.key);return {value:held?structuredClone(held.value):undefined,version:held?.version||0}},
    set:async(ref,value,options={})=>{const name=ref.plugin+'/'+ref.key,held=hostStore.get(name)||{version:0};if(options.ifVersion!==undefined&&options.ifVersion!==held.version)return {isSet:false,version:held.version};hostStore.set(name,{value:structuredClone(value),version:held.version+1});return {isSet:true,version:held.version+1}}},
  session:{id:async()=> 'parent',model:async()=> 'fixture-main',append:async()=>{throw new Error('main injection not allowed')}},
  prompt:{submit:async()=>{throw new Error('main injection not allowed')}},
  agent:{list:async()=>[],spawn:async args=>{spawns.push(args);return {agentId:'actor-1'}}},
  tool:{call:async()=>({})},
  env:{get:async key=>key==='CIEL_RUNTIME_WORK_PARALLEL_HOST_TOKEN'?'host-token':undefined},
  ui:{log:text=>logs.push(String(text)),status:()=>{},invalidate:()=>{}},
  clock:{after:()=>({cancel(){}})},
  mcp:{connect:async()=>({}),call:async(server,name,args)=>{
    calls.push({server,name,args})
    if(name==='work_side_claim') return json({claim:event,settings:{max_active:2,priority_policy:'adaptive'},handoff_replies:[],main_wakes:[]})
    if(name==='work_side_fail') return json({receipt:{receipt_id:event.receipt_id,stage:'pending',failure:{category:args.category,reason:args.reason,recoverable:true}}})
    return json({})
  }}
}
await startSide($)
const claimed=await pollSide($,'claude:parent')
const failures=calls.filter(call=>call.name==='work_side_fail').map(call=>({category:call.args.category,
  generation:call.args._host.generation,reason:call.args.reason}))
console.log(JSON.stringify({kind,claimed,spawns:spawns.map(spawn=>JSON.parse(spawn.prompt)),failures,
  receipts:sideReceipts('claude:parent'),logs,mainCalls:calls.filter(call=>call.name==='work_side_handoff'||call.name==='work_side_wake').length}))

// Drives the host thread read through Claude Code's persisted-result stub,
// using the exact stub template captured from Claude Code 2.1.321 (owned probe
// mcp-size-none-ee41a3e2), plus the main-context budget and spawn proof.
import {readFileSync} from 'node:fs'
import {startSide,pollSide,sideSpawnInput,sideMainBusy,sidePromptTurnComplete} from './side-intake.js'
const fixture=JSON.parse(readFileSync(new URL('./persisted-stub-fixture.json',import.meta.url),'utf8'))
const C='34886995-4a66-4527-a618-814e9cb0a5bf'
const root={id:'40',conversation_id:C,text:'root',thread_root_id:null}
const replies=Array.from({length:60},(_,i)=>({id:String(41+i),conversation_id:C,thread_root_id:'40',
  text:'reply '+(41+i)+' '+'word '.repeat(500)}))
const page=JSON.stringify(replies)
const stubFor=(chars,path)=>fixture.stub_text
  .replace(/^Error: result \([\d,]+ characters/,'Error: result ('+chars.toLocaleString('en-US')+' characters')
  .split(fixture.saved_path).join(path)
const realPath=fixture.saved_path
const otherSession=realPath.replace(fixture.session_id,'00000000-0000-4000-8000-000000000000')
const otherTool=realPath.replace('mcp-walkie-walkie_call_tool-','mcp-walkie-walkie_send_message-')
// Claude Code 2.1.326 names the saved file <ms>-<Math.random().toString(36).slice(2,8)>
// (KOBE 2026-11-17: ...-1794899124005-la554o.txt). The fixture's 2.1.321 name
// ends -<ms>.txt; each case below swaps only that ending.
const stamp=/-(\d+)\.txt$/.exec(realPath)[1]
const named=(path,tail)=>path.replace(/-\d+\.txt$/,'-'+stamp+tail+'.txt')
const suffixed=named(realPath,'-la554o')
const projectsAt=realPath.toLowerCase().indexOf('\\projects\\')+'\\projects\\'.length
const projectAs=segment=>realPath.slice(0,projectsAt)+segment+realPath.slice(realPath.indexOf('\\',projectsAt))
const persisted=path=>({stub:stubFor(page.length,path),file:{[path]:page}})
const scenarios={
 persisted_ok:{stub:stubFor(page.length,realPath),file:{[realPath]:page},spawn:true},
 persisted_suffix_ok:{...persisted(suffixed),spawn:true},
 persisted_suffix_digits_ok:{...persisted(named(realPath,'-123456')),spawn:true},
 persisted_suffix_short_ok:{...persisted(named(realPath,'-a')),spawn:true},
 suffix_upper:persisted(named(realPath,'-LA554O')),
 suffix_long:persisted(named(realPath,'-la554oo')),
 suffix_empty:persisted(named(realPath,'-')),
 suffix_double:persisted(named(realPath,'-la554o-b2')),
 suffix_dot:persisted(named(realPath,'-la5.4o')),
 suffix_underscore:persisted(named(realPath,'-la_54o')),
 suffix_other_session:persisted(named(otherSession,'-la554o')),
 suffix_other_tool:persisted(named(otherTool,'-la554o')),
 suffix_outside_config:persisted('C:\\Windows\\Temp\\mcp-walkie-walkie_call_tool-1794899124005-la554o.txt'),
 project_dot:persisted(projectAs('.')),
 project_dotdot:persisted(projectAs('..')),
 other_session:{stub:stubFor(page.length,otherSession),file:{[otherSession]:page}},
 other_tool:{stub:stubFor(page.length,otherTool),file:{[otherTool]:page}},
 outside_config:{stub:stubFor(page.length,'C:\\Windows\\Temp\\mcp-walkie-walkie_call_tool-1.txt'),
  file:{'C:\\Windows\\Temp\\mcp-walkie-walkie_call_tool-1.txt':page}},
 size_mismatch:{stub:stubFor(page.length+5,realPath),file:{[realPath]:page}},
 unreadable:{stub:stubFor(page.length,realPath),file:{}},
 per_call_limit:{stub:stubFor(5*1024*1024,realPath),file:{[realPath]:page}},
 context_budget:{plain:true,usage:{tokens:180000,window:200000},spawn:false},
 // Main was compacted for the event above within the cooldown: an event that
 // still does not fit fails as an ordinary source_context error.
 context_budget_again:{plain:true,usage:{tokens:180000,window:200000},spawn:false},
 // Another conversation whose main is in a turn: no compaction until it ends.
 context_budget_busy:{plain:true,usage:{tokens:180000,window:200000},spawn:false,busy:true,
  session:'0b6f6f5e-1c1d-4a52-9e8e-6a7d3f0c2b11'},
 context_room:{plain:true,usage:{tokens:20000,window:1000000},spawn:true},
 // Room for the root, the target and the newest replies, not the whole thread.
 // An ordinary event waits for main's compaction; P0/P1 gets the bounded
 // thread at once (Walkie P0 134499 waited 3 compaction rounds, 2026-11-16).
 context_partial_ordinary:{plain:true,usage:{tokens:140000,window:200000},spawn:false,
  session:'1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f'},
 context_partial_p0:{plain:true,usage:{tokens:140000,window:200000},spawn:true,priority:'P0',
  session:'2d3e4f5a-6b7c-4d8e-9f0a-1b2c3d4e5f6a'},
}
// The module's clock (COMPACT_COOLDOWN_MS) moves with this skew; nothing else does.
const realNow=Date.now.bind(Date)
let clockSkew=0
Date.now=()=>realNow()+clockSkew
// One scripted router per scenario. work_side_fail keeps the durable count the
// real side_intake_processing.fail keeps: a compaction wait counts in
// compaction_waits while fewer than MAIN_COMPACTION_WAITS (12) were spent;
// after that it is an ordinary, counted source_context failure. The receipt it
// returns carries that side_failure, as the real router's does.
function makeHost(name,scenario,rec){
 const session=scenario.session||fixture.session_id
 const backend=rec.backend
 const $={session:{id:async()=>session,append:async()=>{},
   usage:async()=>({context:scenario.usage||{window:200000,tokens:1000}}),
   compact:async()=>{rec.compacts.push(name);return {}}},prompt:{submit:async()=>({})},
  env:{get:async key=>key==='CLAUDE_CONFIG_DIR'?fixture.claude_config_dir:'x'.repeat(40)},
  fs:{read:async path=>{ if(Object.prototype.hasOwnProperty.call(scenario.file||{},path)) return scenario.file[path]; throw Error('ENOENT') }},
  mcp:{connect:async()=>({}),call:async(server,tool,args)=>{
   rec.calls.push({server,tool,args})
   const text=v=>({content:[{type:'text',text:v}]})
   if(server==='walkie'&&tool==='walkie_read_messages') return text(JSON.stringify({messages:[root]}))
   if(server==='walkie'&&tool==='walkie_call_tool') return text(scenario.plain?page:scenario.stub)
   if(tool==='work_side_claim'){
    const claim=backend.claims>=backend.claimLimit?null:{receipt_id:'e'.repeat(64),
     generation:'1000000000000000000000000000000'+String(2+backend.claims%8),text:'event',event_key:name,
     lease_until:Date.now()/1000+120,source_read:{server:'walkie',classification:'thread',conversation_id:C,root_id:'40',target_message_id:'45'},
     source_context:'thread',route:{},workload:{version:1,truncated:false,nodes:[],queue_order:[]},
     ...(scenario.priority?{source_priority:scenario.priority}:{})}
    if(claim) backend.claims++
    return text(JSON.stringify({claim,settings:{max_active:2},handoff_replies:[],main_wakes:[]}))}
   if(tool==='work_side_fail'){
    let category=args.category
    if(category==='main_compaction_pending'&&backend.waits<12) backend.waits++
    else { if(category==='main_compaction_pending') category='source_context'; backend.attempts++ }
    backend.recorded.push(category)
    return text(JSON.stringify({receipt:{receipt_id:'e'.repeat(64),stage:'received',side_failure:{category,
     compaction_waits:backend.waits,attempt_count:backend.attempts,quarantined:backend.attempts>=6}},
     _category:args.category,_reason:args.reason}))}
   return text('{}')}},
  tool:{call:async()=>({})},
  agent:{list:async()=>[],spawn:async a=>{
   // Spawn proof: every look-alike is denied, only the exact pending fork passes.
   const base={...a,fork:true,parentAgentId:undefined}
   const plugin={origin:{plugin:'ciel-work'}}
   for (const [label,input,next] of [['not_fork',{...base,fork:false},plugin],['named',{...base,name:'side-intake-0123456789abcdef'},plugin],
     ['subagent',{...base,parentAgentId:'x'},plugin],['wrong_type',{...base,subagentType:'general-purpose'},plugin],
     ['other_plugin',base,{origin:{plugin:'engine'}}],['unrelated',{...base,description:'Work n1'},plugin]]) {
    const r=sideSpawnInput(input,next); rec.proof.push({label,result:r===null?null:r===input?'authorized':r.deny?'deny':'other'})
   }
   rec.proof.push({label:'exact',result:sideSpawnInput(base,plugin)===base?'authorized':'other'})
   rec.spawns.push(a);return {agentId:'actor-'+name+'-'+rec.spawns.length}}},
  ui:{invalidate:()=>{},log:()=>{}},clock:{after:()=>({cancel(){}})}}
 return {session,$}
}
const record=(waits=0,claimLimit=1)=>({calls:[],spawns:[],proof:[],compacts:[],
 backend:{waits,attempts:0,claims:0,claimLimit,recorded:[]}})
const spawned=rec=>rec.spawns[0]?JSON.parse(rec.spawns[0].prompt).source_message:null
const out={}
for (const [name,scenario] of Object.entries(scenarios)) {
 const rec=record()
 sideMainBusy(()=>scenario.busy===true)
 const {session,$}=makeHost(name,scenario,rec)
 await startSide($); await pollSide($,'claude:'+session)
 const immediate=rec.compacts.length
 if(scenario.busy){ sideMainBusy(()=>false); sidePromptTurnComplete($); await new Promise(r=>setTimeout(r,20)) }
 const failure=rec.calls.find(c=>c.tool==='work_side_fail')
 out[name]={spawns:rec.spawns.length,proof:rec.proof,compacts_immediate:immediate,compacts_after_turn:rec.compacts.length,
  saved:scenario.stub?(/saved to (.+?\.txt)/.exec(scenario.stub)||[])[1]?.split('\\').pop():null,
  failure:failure?{category:failure.args.category,reason:failure.args.reason,
   ...(failure.args.retry_after_seconds?{retry_after_seconds:failure.args.retry_after_seconds}:{})}:null,
  messages:spawned(rec)?.messages?.length??null,
  truncated:spawned(rec)?spawned(rec).thread_truncated===true:null}
}
// Walkie 2026-11-17 (thread 137008): one P3 event whose thread exceeds main's
// remaining context while main stays busy turn after turn. This module last
// compacted main more than COMPACT_COOLDOWN_MS ago and cannot compact it again
// mid-turn. The event spends its compaction waits; once the router reports all
// 12 spent, the next claim must start the fork on the bounded thread instead
// of failing (a counted source_context failure) until quarantine.
const SEQUENCE_SESSIONS={waits_spent:'3e4f5a6b-7c8d-4e9f-8a1b-2c3d4e5f6a7b',fresh:'4f5a6b7c-8d9e-4f0a-9b2c-3d4e5f6a7b8c',
 fits:'5a6b7c8d-9e0f-4a1b-8c3d-4e5f6a7b8c9d',urgent:'6b7c8d9e-0f1a-4b2c-9d4e-5f6a7b8c9d0e',
 reloaded:'7c8d9e0f-1a2b-4c3d-8e5f-6a7b8c9d0e1f'}
async function sequence(label,{priority='P3',usage={tokens:140000,window:200000},waits=0,claims=3,busy=true}={}){
 const session=SEQUENCE_SESSIONS[label]
 const scenario={plain:true,usage,priority,session}
 // An old compaction by this module: main was idle once (the thread did not
 // fit, so it compacted), then the clock moves past the cooldown.
 const warm=record(0,1)
 sideMainBusy(()=>false)
 const first=makeHost(label+'-warm',{plain:true,usage:{tokens:180000,window:200000},priority:'P3',session},warm)
 await startSide(first.$); await pollSide(first.$,'claude:'+session)
 clockSkew+=11*60*1000
 const rec=record(waits,claims)
 sideMainBusy(()=>busy)
 const {$}=makeHost(label,scenario,rec)
 await startSide($)
 for (let i=0;i<claims;i++) await pollSide($,'claude:'+session)
 sideMainBusy(()=>false)
 return {warm_compacts:warm.compacts.length,warm_recorded:warm.backend.recorded,
  sent:rec.calls.filter(c=>c.tool==='work_side_fail').map(c=>c.args.category),
  recorded:rec.backend.recorded,claims:rec.backend.claims,waits:rec.backend.waits,attempts:rec.backend.attempts,
  compacts:rec.compacts.length,spawns:rec.spawns.length,truncated:spawned(rec)?spawned(rec).thread_truncated===true:null,
  omitted:spawned(rec)?.omitted_replies??null,messages:spawned(rec)?.messages?.length??null}
}
// 11 waits already spent: one more wait (the 12th), then the bounded thread.
out.waits_spent=await sequence('waits_spent',{waits:11,claims:2})
// A reloaded module has not seen this event's count: its first claim is one
// counted failure (the router reports waits=12 in that receipt), then bound.
out.waits_spent_reloaded=await sequence('reloaded',{waits:12,claims:2})
// A new event (no waits yet) still waits for main's compaction first.
out.fresh_event=await sequence('fresh',{waits:0,claims:2})
// A thread that fits needs no wait and no bound, whatever the waits.
out.fits_after_waits=await sequence('fits',{waits:12,claims:1,usage:{tokens:20000,window:1000000}})
// P0/P1 keeps its immediate bounded thread, with no wait.
out.urgent_no_wait=await sequence('urgent',{priority:'P1',waits:0,claims:1})
console.log(JSON.stringify(out))

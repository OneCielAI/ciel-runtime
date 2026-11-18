// The one host read a cut event still makes (its target message) goes through
// Claude Code's persisted-result stub when the result is large, using the exact
// stub template captured from Claude Code 2.1.321 (owned probe
// mcp-size-none-ee41a3e2), plus the native fork spawn proof.
// Andy 2026-11-18: the host reads no thread before a decision, so the main-
// context budget, its compaction waits and the bounded thread are gone.
import {readFileSync} from 'node:fs'
import {startSide,pollSide,sideSpawnInput,sideMainBusy} from './side-intake.js'
const fixture=JSON.parse(readFileSync(new URL('./persisted-stub-fixture.json',import.meta.url),'utf8'))
const C='34886995-4a66-4527-a618-814e9cb0a5bf'
// A large target message: its read result is persisted by Claude Code.
const target={id:'45',conversation_id:C,thread_root_id:'40',text:'reply 45 '+'word '.repeat(30000)}
const body=JSON.stringify({messages:[target]})
const stubFor=(chars,path)=>fixture.stub_text
  .replace(/^Error: result \([\d,]+ characters/,'Error: result ('+chars.toLocaleString('en-US')+' characters')
  .split(fixture.saved_path).join(path)
// The fixture saved a walkie_call_tool result; the target read is walkie_read_messages.
const asRead=path=>path.replace('mcp-walkie-walkie_call_tool-','mcp-walkie-walkie_read_messages-')
const realPath=asRead(fixture.saved_path)
const otherSession=realPath.replace(fixture.session_id,'00000000-0000-4000-8000-000000000000')
const otherTool=realPath.replace('mcp-walkie-walkie_read_messages-','mcp-walkie-walkie_send_message-')
// Claude Code 2.1.326 names the saved file <ms>-<Math.random().toString(36).slice(2,8)>
// (KOBE 2026-11-17: ...-1794899124005-la554o.txt). The fixture's 2.1.321 name
// ends -<ms>.txt; each case below swaps only that ending.
const stamp=/-(\d+)\.txt$/.exec(realPath)[1]
const named=(path,tail)=>path.replace(/-\d+\.txt$/,'-'+stamp+tail+'.txt')
const suffixed=named(realPath,'-la554o')
const projectsAt=realPath.toLowerCase().indexOf('\\projects\\')+'\\projects\\'.length
const projectAs=segment=>realPath.slice(0,projectsAt)+segment+realPath.slice(realPath.indexOf('\\',projectsAt))
const persisted=path=>({stub:stubFor(body.length,path),file:{[path]:body}})
const scenarios={
 persisted_ok:{...persisted(realPath),spawn:true},
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
 suffix_outside_config:persisted('C:\\Windows\\Temp\\mcp-walkie-walkie_read_messages-1794899124005-la554o.txt'),
 project_dot:persisted(projectAs('.')),
 project_dotdot:persisted(projectAs('..')),
 other_session:persisted(otherSession),
 other_tool:persisted(otherTool),
 outside_config:persisted('C:\\Windows\\Temp\\mcp-walkie-walkie_read_messages-1.txt'),
 size_mismatch:{stub:stubFor(body.length+5,realPath),file:{[realPath]:body}},
 unreadable:{stub:stubFor(body.length,realPath),file:{}},
 per_call_limit:{stub:stubFor(5*1024*1024,realPath),file:{[realPath]:body}},
}
function makeHost(name,scenario,rec){
 const session=fixture.session_id
 const $={session:{id:async()=>session,append:async()=>{}},prompt:{submit:async()=>({})},
  env:{get:async key=>key==='CLAUDE_CONFIG_DIR'?fixture.claude_config_dir:'x'.repeat(40)},
  fs:{read:async path=>{ if(Object.prototype.hasOwnProperty.call(scenario.file||{},path)) return scenario.file[path]; throw Error('ENOENT') }},
  mcp:{connect:async()=>({}),call:async(server,tool,args)=>{
   rec.calls.push({server,tool,args})
   const text=v=>({content:[{type:'text',text:v}]})
   if(server==='walkie'&&tool==='walkie_read_messages') return text(scenario.stub)
   if(tool==='work_side_claim'){
    const claim=rec.claimed?null:{receipt_id:'e'.repeat(64),generation:'10000000000000000000000000000002',
     text:JSON.stringify({type:'com.ciel.walkie.message',data:{conversation:C,message_id:'45',thread_root_id:'40',text:'cut [잘림]'}}),
     event_key:name,lease_until:Date.now()/1000+120,source_truncated:true,
     source_read:{server:'walkie',classification:'thread',conversation_id:C,root_id:'40',target_message_id:'45'},
     source_context:'thread',route:{},workload:{version:1,truncated:false,nodes:[],queue_order:[]}}
    rec.claimed=true
    return text(JSON.stringify({claim,settings:{max_active:2},handoff_replies:[],main_wakes:[]}))}
   if(tool==='work_side_fail') return text(JSON.stringify({receipt:{receipt_id:'e'.repeat(64),stage:'received'}}))
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
   rec.spawns.push(a);return {agentId:'actor-'+name}}},
  ui:{invalidate:()=>{},log:()=>{}},clock:{after:()=>({cancel(){}})}}
 return {session,$}
}
const out={}
for (const [name,scenario] of Object.entries(scenarios)) {
 const rec={calls:[],spawns:[],proof:[],claimed:false}
 sideMainBusy(()=>false)
 const {session,$}=makeHost(name,scenario,rec)
 await startSide($); await pollSide($,'claude:'+session)
 const failure=rec.calls.find(c=>c.tool==='work_side_fail')
 const prompt=rec.spawns[0]?JSON.parse(rec.spawns[0].prompt):null
 out[name]={spawns:rec.spawns.length,proof:rec.proof,
  saved:scenario.stub?(/saved to (.+?\.txt)/.exec(scenario.stub)||[])[1]?.split('\\').pop():null,
  reads:rec.calls.filter(c=>c.server==='walkie').length,
  failure:failure?{category:failure.args.category,reason:failure.args.reason}:null,
  target:prompt?.target_message?{id:prompt.target_message.id,chars:prompt.target_message.text.length}:null}
}
console.log(JSON.stringify(out))

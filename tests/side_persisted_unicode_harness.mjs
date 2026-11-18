// mcpText against the exact non-ASCII stub captured from Claude Code 2.1.321
// over a streamable-HTTP MCP (owned probe mcp-size-unicode-http-fafc8267). The
// stub states 233,780: the saved text's UTF-16 length (JS .length), not its
// 201,620 code points or 474,980 UTF-8 bytes.
import {readFileSync} from 'node:fs'
import {startSide,pollSide} from './side-intake.js'
const fixture=JSON.parse(readFileSync(new URL('./persisted-stub-unicode-fixture.json',import.meta.url),'utf8'))
const C='34886995-4a66-4527-a618-814e9cb0a5bf'
const LINE=n=>('워키 스레드 '+n+' 행 ★→€✓ 😀🚀👩‍💻 ').repeat(60)
// Rows whose UTF-16 length is exactly the stated count, like the saved file.
function pageOfLength(units) {
 const rows=[]; let n=41
 for(;;){
  const next=[...rows,{id:String(n),conversation_id:C,thread_root_id:'40',text:LINE(n)}]
  if(JSON.stringify({messages:next}).length>units) break
  rows.push(next.at(-1)); n++
 }
 // walkie_read_messages(after=target-1): the target first (Andy 2026-11-18:
 // a cut event's one host read; no thread read).
 let text=JSON.stringify({messages:rows})
 // Pad the last row's text with Hangul (1 UTF-16 unit each) to the exact length.
 const pad=units-text.length
 rows[rows.length-1].text+='가'.repeat(pad)
 text=JSON.stringify({messages:rows})
 if(text.length!==units) throw new Error('fixture length '+text.length+' != '+units)
 return text
}
// The captured page (233,780 units, ~474 KB UTF-8) exceeds the 384 KiB thread
// cap, so the stated count is scaled to fit while the captured stub's exact
// wording and saved path are kept.
const stated=150000
// The captured stub saved a walkie_call_tool result; the target read is walkie_read_messages.
const savedPath=fixture.saved_path.replace('mcp-walkie-walkie_call_tool-','mcp-walkie-walkie_read_messages-')
const stubWith=count=>fixture.stub_text.split(fixture.saved_path).join(savedPath)
  .replace(/^Error: result \([\d,]+ characters/,'Error: result ('+count.toLocaleString('en-US')+' characters')
const page=pageOfLength(stated)
const surrogates=[...page].length!==page.length
// The same thread, trimmed so its CODE POINT count equals the stated number.
let byCodePoints=pageOfLength(stated+(page.length-[...page].length)+64)
while([...byCodePoints].length>stated) byCodePoints=byCodePoints.slice(0,-1)
const scenarios={
 utf16_exact:{stub:stubWith(stated),file:page,spawn:true},
 code_point_count:{stub:stubWith(stated),file:byCodePoints},
 utf8_count:{stub:stubWith(new TextEncoder().encode(page).length),file:page},
}
const out={meta:{stated,js_length:page.length,code_points:[...page].length,utf8_bytes:new TextEncoder().encode(page).length,surrogates}}
for (const [name,scenario] of Object.entries(scenarios)) {
 let claimed=false; const calls=[],spawns=[]
 const $={session:{id:async()=>fixture.session_id,append:async()=>{},usage:async()=>({context:{window:1000000,tokens:1000}})},
  prompt:{submit:async()=>({})},env:{get:async key=>key==='CLAUDE_CONFIG_DIR'?fixture.claude_config_dir:'x'.repeat(40)},
  fs:{read:async path=>{ if(path===savedPath) return scenario.file; throw Error('ENOENT') }},
  mcp:{connect:async()=>({}),call:async(server,tool,args)=>{
   calls.push({server,tool,args})
   const text=v=>({content:[{type:'text',text:v}]})
   if(server==='walkie'&&tool==='walkie_read_messages') return text(scenario.stub)
   if(tool==='work_side_claim'){const claim=claimed?null:{receipt_id:'e'.repeat(64),generation:'10000000000000000000000000000003',text:JSON.stringify({type:'com.ciel.walkie.message',data:{conversation:C,message_id:'41',thread_root_id:'40',text:'cut [잘림]'}}),
    event_key:name,source_truncated:true,lease_until:Date.now()/1000+120,source_read:{server:'walkie',classification:'thread',conversation_id:C,root_id:'40',target_message_id:'41'},
    source_context:'thread',route:{},workload:{version:1,truncated:false,nodes:[],queue_order:[]}};
    claimed=true;return text(JSON.stringify({claim,settings:{max_active:2},handoff_replies:[],main_wakes:[]}))}
   return text('{}')}},
  tool:{call:async()=>({})},agent:{list:async()=>[],spawn:async a=>{spawns.push(a);return {agentId:'actor'}}},
  ui:{invalidate:()=>{},log:()=>{}},clock:{after:()=>({cancel(){}})}}
 await startSide($); await pollSide($,'claude:'+fixture.session_id)
 const failure=calls.find(c=>c.tool==='work_side_fail')
 const prompt=spawns[0]?JSON.parse(spawns[0].prompt):null
 out[name]={spawns:spawns.length,failure:failure?{category:failure.args.category,reason:failure.args.reason}:null,
  // The verified target text reaches the fork exactly (no truncation).
  exact_text:prompt?prompt.target_message.text===JSON.parse(page).messages[0].text:null}
}
console.log(JSON.stringify(out))

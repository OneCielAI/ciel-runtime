// Andy 2026-11-18 (user design): no host thread read before a side decision.
// An event with its own message text is decided from the event and main's
// inherited context (0 Walkie reads, whatever the thread length); a cut or
// textless event gets exactly one read of its target message, verified.
import {startSide,pollSide} from './side-intake.js'
const C='34886995-4a66-4527-a618-814e9cb0a5bf'
const root={id:'40',conversation_id:C,text:'root',reply_to_message_id:null,thread_root_id:null}
const reply=(id,extra={})=>({id:String(id),conversation_id:C,text:'reply '+id,thread_root_id:'40',reply_to_message_id:'40',...extra})
const spec=(extra={})=>({server:'walkie',classification:'thread',conversation_id:C,root_id:'40',target_message_id:'41',...extra})
const result=(value)=>({content:[{type:'text',text:typeof value==='string'?value:JSON.stringify(value)}]})
const walkie=(text,extra={})=>JSON.stringify({specversion:'1.0',type:'com.ciel.walkie.message',id:'e1',
 data:{event_type:'message.created',conversation:C,message_id:'41',thread_root_id:'40',text,...extra}})
// A 2,400-reply thread the host would page through if it read the thread.
const longPage=after=>Array.from({length:200},(_,i)=>reply(Math.max(41,Number(after)+1)+i)).filter(row=>Number(row.id)<41+2400)
const scenarios={
 long_thread:{spec:spec({target_message_id:'2440'}),text:walkie('where does this stand?'),thread:longPage,spawn:true},
 plain_text:{spec:spec(),text:'question',spawn:true},
 none_referenced:{spec:{server:'walkie',classification:'none_referenced',conversation_id:C},text:walkie('remind me'),spawn:true},
 null_none_referenced:{spec:null,source_context:'none_referenced',source_truncated:true,text:walkie('cut [잘림]'),spawn:true},
 null_thread_reference:{spec:null,source_context:'thread',text:walkie('x')},
 malformed_spec:{spec:{server:'walkie',classification:'thread',conversation_id:'bad',root_id:'40',target_message_id:'41'},text:walkie('x')},
 invalid_classification:{spec:{server:'walkie',classification:'invalid',reason:'Walkie message_id malformed'},text:walkie('x')},
 truncated_ok:{spec:spec({reply_to_message_id:'40',thread_root_id:'40'}),source_truncated:true,text:walkie('cut [잘림]'),
  read:result({messages:[reply(41,{text:'the whole message',sender_token:'secret-value'}),reply(42)]}),spawn:true},
 textless_ok:{spec:spec(),text:walkie(''),read:result({messages:[reply(41)]}),spawn:true},
 root_target_ok:{spec:spec({target_message_id:'40'}),source_truncated:true,text:walkie('cut [잘림]'),
  read:result({messages:[root]}),spawn:true},
 big_id:{spec:{server:'walkie',classification:'thread',conversation_id:C,root_id:'900719925474099312',target_message_id:'900719925474099313'},
  source_truncated:true,text:walkie('cut [잘림]'),
  read:result({messages:[{id:'900719925474099313',conversation_id:C,text:'big reply',thread_root_id:'900719925474099312'}]}),spawn:true},
 forged_target:{spec:spec(),source_truncated:true,text:walkie('cut [잘림]'),read:result({messages:[reply(41,{thread_root_id:'39'})]})},
 forged_root_target:{spec:spec({target_message_id:'40'}),source_truncated:true,text:walkie('cut [잘림]'),
  read:result({messages:[{...root,thread_root_id:'39'}]})},
 wrong_lineage:{spec:spec({reply_to_message_id:'40'}),source_truncated:true,text:walkie('cut [잘림]'),
  read:result({messages:[reply(41,{reply_to_message_id:'39'})]})},
 other_conversation:{spec:spec(),source_truncated:true,text:walkie('cut [잘림]'),
  read:result({messages:[reply(41,{conversation_id:'00000000-0000-4000-8000-000000000000'})]})},
 target_missing:{spec:spec(),source_truncated:true,text:walkie('cut [잘림]'),read:result({messages:[reply(42)]})},
 read_error:{spec:spec(),source_truncated:true,text:walkie('cut [잘림]'),read:{isError:true,content:[]}},
 read_throws:{spec:spec(),source_truncated:true,text:walkie('cut [잘림]'),read:'throw'},
 malformed_response:{spec:spec(),source_truncated:true,text:walkie('cut [잘림]'),read:result('{"messages":[')},
 oversize:{spec:spec(),source_truncated:true,text:walkie('cut [잘림]'),
  read:result({messages:[reply(41,{text:'x'.repeat(4*1024*1024)})]})},
}
const out={}
for (const [name,scenario] of Object.entries(scenarios)) {
 let claimed=false; const calls=[],spawns=[]
 const $={session:{id:async()=>'parent',append:async()=>{}},prompt:{submit:async()=>({})},
  mcp:{connect:async()=>({}),call:async(server,tool,args)=>{
   calls.push({server,tool,args})
   if(server==='walkie'&&tool==='walkie_read_messages'){if(scenario.read==='throw')throw Error('transport');return scenario.read||result({messages:[]})}
   if(server==='walkie'&&tool==='walkie_call_tool')return result(scenario.thread?scenario.thread(args.arguments?.after||'0'):[])
   if(tool==='work_side_claim'){const claim=claimed?null:{receipt_id:'e'.repeat(64),generation:'10000000000000000000000000000001',
    text:scenario.text,event_key:name,lease_until:Date.now()/1000+120,source_read:scenario.spec,source_context:scenario.source_context,
    source_truncated:scenario.source_truncated===true,route:{},workload:{version:1,truncated:false,nodes:[],queue_order:[]}};
    claimed=true;return result({claim,settings:{max_active:2},handoff_replies:[],main_wakes:[]})}
   if(tool==='work_side_fail')return result({receipt:{receipt_id:args._host.receipt_id}})
   return result({})}},tool:{call:async()=>({})},agent:{list:async()=>[],spawn:async a=>{spawns.push(a);return {agentId:'actor'}}},
  env:{get:async()=>'x'.repeat(40)},ui:{invalidate:()=>{},log:()=>{}},clock:{after:()=>({cancel(){}})}}
 await startSide($); await pollSide($,'claude:parent')
 const prompt=spawns[0]?JSON.parse(spawns[0].prompt):null
 out[name]={spawns:spawns.length,source:prompt?.source_context||null,truncated:prompt?.event_truncated===true,
  instruction:prompt?.instruction||'',walkieReads:calls.filter(c=>c.server==='walkie').map(c=>({tool:c.tool,after:c.args.after??c.args.arguments?.after??null})),
  sourceRef:prompt?.source_ref||null,target:prompt?.target_message?{id:prompt.target_message.id,text:prompt.target_message.text}:null,
  hasThread:Boolean(prompt?.source_message),secretLeak:JSON.stringify(prompt||{}).includes('secret-value'),
  hiddenToken:prompt?.target_message?.sender_token??null,
  failures:calls.filter(c=>c.tool==='work_side_fail').map(c=>({category:c.args.category,reason:c.args.reason}))}
}
console.log(JSON.stringify(out))

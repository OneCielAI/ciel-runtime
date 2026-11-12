import {startSide,pollSide} from './side-intake.js'
const C='34886995-4a66-4527-a618-814e9cb0a5bf'
const root={id:'40',conversation_id:C,text:'root',reply_to_message_id:null,thread_root_id:null}
const reply=(id,extra={})=>({id:String(id),conversation_id:C,text:'reply '+id,thread_root_id:'40',...extra})
const spec=(extra={})=>({server:'walkie',classification:'thread',conversation_id:C,root_id:'40',target_message_id:'41',...extra})
const result=(value)=>({content:[{type:'text',text:typeof value==='string'?value:JSON.stringify(value)}]})
const twoPage=[Array.from({length:200},(_,i)=>reply(41+i)),Array.from({length:5},(_,i)=>reply(241+i))]
const scenarios={
 ok_paged:{spec:spec(),root:result({messages:[root]}),pages:twoPage.map(result),spawn:true},
 none_referenced:{spec:{server:'walkie',classification:'none_referenced',conversation_id:C},spawn:true},
 null_none_referenced:{spec:null,source_context:'none_referenced',source_truncated:true,spawn:true},
 null_thread_reference:{spec:null,source_context:'thread'},
 big_id:{spec:{server:'walkie',classification:'thread',conversation_id:C,root_id:'900719925474099312',target_message_id:'900719925474099313'},
  root:result({messages:[{id:'900719925474099312',conversation_id:C,text:'big root',thread_root_id:null}]}),
  pages:[result([{id:'900719925474099313',conversation_id:C,text:'big reply',thread_root_id:'900719925474099312'}])],spawn:true},
 read_error:{spec:spec(),root:{isError:true,content:[]}},
 read_throws:{spec:spec(),root:'throw'},
 missing_root:{spec:spec(),root:result({messages:[]}),pages:[result([])]},
 malformed_root:{spec:spec(),root:result('{"messages":[')},
 target_missing:{spec:spec(),root:result({messages:[root]}),pages:[result([reply(42)])]},
 wrong_lineage:{spec:spec(),root:result({messages:[root]}),pages:[result([reply(41,{thread_root_id:'39'})])]},
 null_lineage_mismatch:{spec:spec({target_message_id:'40',thread_root_id:null,reply_to_message_id:null}),
  root:result({messages:[{...root,thread_root_id:'39'}]}),pages:[result([])]},
 duplicate_target:{spec:spec(),root:result({messages:[root]}),pages:[result([reply(41),reply(41)])]},
 non_increasing:{spec:spec(),root:result({messages:[root]}),pages:[result([reply(42),reply(41)])]},
 malformed_spec:{spec:{server:'walkie',classification:'thread',conversation_id:'bad',root_id:'40',target_message_id:'41'}},
 invalid_classification:{spec:{server:'walkie',classification:'invalid',reason:'Walkie message_id malformed'}},
 oversize:{spec:spec(),root:result({messages:[root]}),pages:[result([reply(41,{text:'x'.repeat(4*1024*1024)})])]},
 page_error:{spec:spec(),root:result({messages:[root]}),pages:[{isError:true,content:[]}]},
}
const out={}
for (const [name,scenario] of Object.entries(scenarios)) {
 let claimed=false,page=0; const calls=[],spawns=[]
 const $={session:{id:async()=>'parent',append:async()=>{}},prompt:{submit:async()=>({})},
  mcp:{connect:async()=>({}),call:async(server,tool,args)=>{
   calls.push({server,tool,args})
   if(server==='walkie'&&tool==='walkie_read_messages'){if(scenario.root==='throw')throw Error('transport');return scenario.root}
   if(server==='walkie'&&tool==='walkie_call_tool')return scenario.pages?.[page++]||result([])
   if(tool==='work_side_claim'){const claim=claimed?null:{receipt_id:'e'.repeat(64),generation:'g',text:'event',event_key:name,
    lease_until:Date.now()/1000+120,source_read:scenario.spec,source_context:scenario.source_context,
    source_truncated:scenario.source_truncated===true,route:{},workload:{version:1,truncated:false,nodes:[],queue_order:[]}};
    claimed=true;return result({claim,settings:{max_active:2},handoff_replies:[],main_wakes:[]})}
   if(tool==='work_side_fail')return result({receipt:{receipt_id:args._host.receipt_id}})
   return result({})}},tool:{call:async()=>({})},agent:{list:async()=>[],spawn:async a=>{spawns.push(a);return {agentId:'actor'}}},
  env:{get:async()=>'x'.repeat(40)},ui:{invalidate:()=>{},log:()=>{}},clock:{after:()=>({cancel(){}})}}
 await startSide($); await pollSide($,'claude:parent')
 const prompt=spawns[0]?JSON.parse(spawns[0].prompt):null
 out[name]={spawns:spawns.length,source:prompt?.source_context||null,truncated:prompt?.event_truncated===true,
  instruction:prompt?.instruction||'',roots:calls.filter(c=>c.tool==='walkie_read_messages').map(c=>c.args),
  pages:calls.filter(c=>c.tool==='walkie_call_tool').map(c=>c.args.arguments.after),
  failures:calls.filter(c=>c.tool==='work_side_fail').map(c=>({category:c.args.category,reason:c.args.reason}))}
}
console.log(JSON.stringify(out))

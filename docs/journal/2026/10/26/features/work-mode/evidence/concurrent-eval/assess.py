import sys,json,re,collections,datetime
from pathlib import Path
run=Path(sys.argv[1]);rows=[];requests=collections.defaultdict(list);calls=[];fidelity={};expected={r["id"]:r["text"] for r in json.loads(Path(__file__).with_name("requests.json").read_text(encoding="utf-8"))};result=json.loads((run/"result.json").read_text(encoding="utf-8"))
p=run/"transcript.jsonl"
if p.exists():
 for raw in p.read_text(encoding="utf-8").splitlines():
  try:o=json.loads(raw)
  except:continue
  role=o.get("type");content=o.get("message",{}).get("content",[])
  if isinstance(content,str):texts=[content]
  else:texts=[b.get("text","") for b in content if isinstance(b,dict) and b.get("type")=="text"]
  text=chr(10).join(texts)
  if role=="user":
   for rid in set(re.findall(r"REQ-(A0|A1|B0|B1|C0|C1|FINAL):",text)):requests[rid].append({"timestamp":o.get("timestamp"),"uuid":o.get("uuid"),"source":"user"})
   if isinstance(content,list):
    for block in content:
     if isinstance(block,dict) and block.get("type")=="tool_result" and "TTL expiration is inclusive" in str(block.get("content")):
      if not requests.get("A0"):requests["A0"].append({"timestamp":o.get("timestamp"),"uuid":o.get("uuid"),"source":"initial Read result"})
  if role=="attachment" and o.get("attachment",{}).get("type")=="queued_command":
   attachment=o["attachment"]
   prompt=attachment.get("prompt")
   if isinstance(prompt,str):prompt_text=prompt
   else:prompt_text=json.dumps(prompt,ensure_ascii=False)
   for rid,text in expected.items():
    if rid!="A0" and text in prompt_text:fidelity[rid]=True
   for rid in set(re.findall(r"REQ-(A0|A1|B0|B1|C0|C1|FINAL):",json.dumps(attachment.get("prompt")))):
    requests[rid].append({"timestamp":o.get("timestamp"),"uuid":o.get("uuid"),"source":"queued_command","delivery_id":attachment.get("delivery_id")})
  if role=="assistant" and isinstance(content,list):
   for b in content:
    if b.get("type")=="tool_use":calls.append({"timestamp":o.get("timestamp"),"tool":b.get("name"),"id":b.get("id"),"input":b.get("input")})
plan=json.loads((run/"work-plan.json").read_text(encoding="utf-8")) if (run/"work-plan.json").exists() else None
summary={"requests_seen":dict(requests),"duplicate_request_entries":{k:v for k,v in requests.items() if len(v)>1},"missing_requests":sorted(set(["A0","A1","B0","B1","C0","C1","FINAL"])-set(requests)),"tool_calls":len(calls),"work_calls":[c for c in calls if "work_" in c["tool"]],"injection_activity":[{k:r.get(k) for k in ["id","activity_observed","before","at"]} for r in result["injections"]],"plan":plan,"live_pids_unchanged":result.get("live_pids_unchanged"),"run_error":result.get("error"),"timeout":result.get("timeout",False)}
(run/"assessment.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
print(json.dumps({k:v for k,v in summary.items() if k not in ["work_calls","plan"]},indent=2))

received={k:len(v) for k,v in requests.items()}
plan_nodes=plan.get("nodes",[]) if plan else []
plan_roots=[n for n in plan_nodes if n.get("parent_id") is None]
checks={"all_requests_present":not summary["missing_requests"],"each_request_once":all(n==1 for n in received.values()),"multiple_mid_activity_injections":sum(bool(r.get("activity_observed")) for r in result["injections"])>=4,"no_driver_error":not summary["run_error"] and not summary["timeout"],"plan_saved":bool(plan_nodes),"all_plan_leaves_verified":bool(plan_nodes) and all(n.get("status")=="done" and bool(n.get("evidence")) for n in plan_nodes if not any(x.get("parent_id")==n.get("id") for x in plan_nodes)),"live_pids_unchanged":result.get("live_pids_unchanged") is True}
summary["request_text_fidelity"]=fidelity
checks["complete_addition_texts_retained"]=all(fidelity.get(k) for k in ["B0","C0","A1","B1","C1","FINAL"])
summary["checks"]=checks;summary["root_titles"]=[n.get("title") for n in plan_roots]
(run/"assessment.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
print(json.dumps({"checks":checks,"root_titles":summary["root_titles"]},indent=2))

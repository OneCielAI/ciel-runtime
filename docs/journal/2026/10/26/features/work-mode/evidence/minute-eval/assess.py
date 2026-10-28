import json,sys,re,collections,hashlib
from pathlib import Path
RUN=Path(sys.argv[1]);HERE=Path(__file__).resolve().parent
result=json.loads((RUN/"result.json").read_text(encoding="utf-8"))
specs=json.loads((HERE/"requests.json").read_text(encoding="utf-8"));expected={r["id"]:r["text"] for r in specs}
receipts=collections.defaultdict(list);calls=[];initial_read=False
for line in (RUN/"transcript.jsonl").read_text(encoding="utf-8").splitlines() if (RUN/"transcript.jsonl").exists() else []:
 try:o=json.loads(line)
 except:continue
 if o.get("type")=="attachment" and o.get("attachment",{}).get("type")=="queued_command":
  a=o["attachment"];prompt=a.get("prompt");text=prompt if isinstance(prompt,str) else json.dumps(prompt,ensure_ascii=False)
  for rid in set(re.findall(r"REQ-(A[123]|B[123]|C[123]|FINAL):",text)):
   receipts[rid].append({"at":o.get("timestamp"),"uuid":o.get("uuid"),"delivery_id":a.get("delivery_id"),"full_text":expected[rid] in text})
 if o.get("type")=="user":
  c=o.get("message",{}).get("content",[])
  if isinstance(c,list):
   for b in c:
    if isinstance(b,dict) and b.get("type")=="tool_result":
     t=str(b.get("content"))
     if all(s in t for s in ["REQ-A0:","REQ-B0:","REQ-C0:","Paths are SIMPLE"]):initial_read=True
 if o.get("type")=="assistant":
  for b in o.get("message",{}).get("content",[]):
   if isinstance(b,dict) and b.get("type")=="tool_use":calls.append({"at":o.get("timestamp"),"name":b.get("name"),"input":b.get("input")})
plan=json.loads((RUN/"work-plan.json").read_text(encoding="utf-8")) if (RUN/"work-plan.json").exists() else {}
nodes=plan.get("nodes",[]);leaves=[n for n in nodes if not any(c.get("parent_id")==n.get("id") for c in nodes)]
injections=[r for r in result.get("injections",[]) if r["id"] not in ["INITIAL","FINAL"]]
intervals=[injections[i]["at"]-injections[i-1]["at"] for i in range(1,len(injections))]
coverage=["A0","A1","A2","A3","B0","B1","B2","B3","C0","C1","C2","C3"]
cp=RUN/"workspace/completion.json"
try:completion=json.loads(cp.read_text(encoding="utf-8"))
except:completion={}
mapping=completion.get("requests",completion) if isinstance(completion,dict) else {}
normalized={str(k).removeprefix("REQ-"):v for k,v in mapping.items()} if isinstance(mapping,dict) else {}
summary={"initial_three_specs_read":initial_read,"receipts":dict(receipts),"missing_requests":[k for k in expected if k!="INITIAL" and k not in receipts],"duplicate_receipts":{k:v for k,v in receipts.items() if len(v)>1},"amendment_count":len(injections),"amendments_per_task":dict(collections.Counter(r["id"][0] for r in injections)),"interval_seconds":intervals,"max_lateness_seconds":max((r.get("schedule_lateness_seconds",0) for r in injections),default=None),"observed_active_count":sum(bool(r.get("activity_observed")) for r in injections),"injection_activity":[{k:r.get(k) for k in ["id","at","activity_observed","before","router_activity","actual_offset_seconds","schedule_lateness_seconds"]} for r in injections],"work_call_count":sum("work_" in c["name"] for c in calls),"plan_nodes":len(nodes),"plan_roots":[n.get("title") for n in nodes if n.get("parent_id") is None],"plan_branches":[n.get("title") for n in nodes if n.get("parent_id")==plan.get("active_root")],"all_leaves_done_with_evidence":bool(leaves) and all(n.get("status")=="done" and n.get("evidence") for n in leaves),"completion_missing_ids":[k for k in coverage if k not in normalized],"completion_report_structure":"requests map with REQ- keys" if "requests" in completion else "top-level map","run_error":result.get("error"),"timeout":result.get("timeout",False),"live_pids_unchanged":result.get("live_pids_unchanged"),"artifact_sha256":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (RUN/"workspace").glob("*.py")}}
summary["delivery_valid"]=initial_read and len(injections)==9 and not summary["missing_requests"] and not summary["duplicate_receipts"] and all(v[0]["full_text"] for v in receipts.values())
summary["cadence_valid"]=len(intervals)==8 and all(58<=n<=62 for n in intervals)
# Snapshot title evidence is not enough on its own; preserve the whole final tree for review.
summary["plan_nodes_detail"]=nodes
summary["all_twelve_requirement_ids_in_plan"]=all(rid in json.dumps(nodes) for rid in coverage)
summary["amendments_three_per_task"]=summary["amendments_per_task"]=={"A":3,"B":3,"C":3}
(RUN/"assessment.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
print(json.dumps({k:v for k,v in summary.items() if k not in ["receipts","injection_activity"]},indent=2,ensure_ascii=False))

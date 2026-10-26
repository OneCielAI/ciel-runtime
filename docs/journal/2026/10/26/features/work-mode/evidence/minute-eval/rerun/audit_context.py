import hashlib,json,re,sys
from pathlib import Path
run=Path(sys.argv[1]); base=run.parent
plan=json.loads((run/'work-plan.json').read_text(encoding='utf-8'))
result=json.loads((run/'result.json').read_text(encoding='utf-8'))
nodes=plan.get('nodes',[]); byid={n['id']:n for n in nodes}
root=plan.get('active_root'); branches=[n for n in nodes if n.get('parent_id')==root]
def ancestors(n):
 out=[n]; seen={n['id']}
 while out[-1].get('parent_id') in byid:
  parent=byid[out[-1]['parent_id']]
  if parent['id'] in seen: raise ValueError('cyclic plan')
  seen.add(parent['id']);out.append(parent)
 return out
coverage={};cross=[];branch_rows=[]
for n in branches:
 text=n.get('title',''); match=re.match(r'(?:REQ-)?([ABC])(?:|:)',text)
 if not match:continue
 task=match.group(1);owned=[m for m in nodes if n in ancestors(m)]
 for m in owned:
  ids=set(re.findall(r'(?<![A-Z0-9])(?:REQ-)?([ABC][0-3])(?![A-Z0-9])',m.get('title','')+' '+m.get('note','')))
  for rid in ids:
   coverage.setdefault(rid,[]).append({'node_id':m['id'],'branch':task})
   if rid[0]!=task:cross.append({'node_id':m['id'],'branch':task,'requirement':rid})
 leaves=[m for m in owned if not any(c.get('parent_id')==m['id'] for c in nodes)]
 branch_rows.append({'task':task,'id':n['id'],'title':text,'nodes':len(owned),'leaves':len(leaves),'all_leaves_done_with_evidence':bool(leaves) and all(m.get('status')=='done' and m.get('evidence') for m in leaves)})
required=[a+str(i) for a in 'ABC' for i in range(4)]
missing=[rid for rid in required if not any(x['branch']==rid[0] for x in coverage.get(rid,[]))]
state=Path(result['scratch'])/'cfg'
paths=list(state.glob('work-sessions/*/work-plan.json'))
session=result.get('session_id');digest=hashlib.sha256(('claude:'+session).encode()).hexdigest() if session else None
report={'run':run.name,'session_id':session,'branches':branch_rows,'requirement_ancestry':coverage,'missing_requirement_ids_in_correct_branch':missing,'cross_branch_request_ids':cross,'own_store_present':bool(digest and any(p.parent.name==digest for p in paths)),'work_store_count_in_isolated_config':len(paths),'scope':'One conversation, three task contexts; not a concurrent multi-session isolation test. Node labels establish bookkeeping only; functionality is assessed by independent verifier.'}
report['bookkeeping_separated']=set(b['task'] for b in branch_rows)==set('ABC') and not missing and not cross and all(b['all_leaves_done_with_evidence'] for b in branch_rows)
(run/'context-audit.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(report,indent=2,ensure_ascii=False))

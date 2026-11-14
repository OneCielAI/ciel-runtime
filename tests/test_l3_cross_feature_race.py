from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_real_p0_vs_wait_creator_race_both_directions(tmp_path):
    script = tmp_path / "cross_creator.py"
    src = Path(__file__).resolve().parents[1]
    script.write_text(
        """import json,sys,time\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nfrom ciel_runtime_support.work_parallel_p0 import WorkParallelP0Store\nfrom ciel_runtime_support.work_parallel_wait_boost import WaitBoostStore,boost_key,ATTEMPT_FIELDS,LAUNCH_FIELDS,COMPLETION_FIELDS,TERMINAL_FIELDS\nfrom ciel_runtime_support.work_assignment_key_coordinator import AssignmentKeyCoordinator,P0StoreDependencyReader,WaitStoreDependencyReader\nbase=Path(sys.argv[2]); feature=sys.argv[3]; hold=sys.argv[4]=='hold'; ledger=base/'work-parallel.json'; p0=WorkParallelP0Store.beside(ledger); wait=WaitBoostStore(base/'work-parallel-wait-boost-v1.json')\ndef nulls(fields): return {name:None for name in fields}\ndef boosted(): return {'scope':'scope','item_id':'wait','root_id':'root','root_created_at':1.0,'release_receipt':'b'*64,'release_at':2.0,'wait_digest':'c'*64,'parked_lease_id':None,'release_store_before_version':0,'state':'boosted',**nulls(ATTEMPT_FIELDS),**nulls(LAUNCH_FIELDS),'outcome':None,'consumed_at':None,**nulls(COMPLETION_FIELDS),**nulls(TERMINAL_FIELDS),'updated_at':2.0}\ndef consuming(document):\n row=boosted(); row.update(state='consuming',attempt_id='1'*32,attempt_kind='reserve',ledger_before_version=0,lease_id='lease',pre_state='absent',pre_slot=False,pre_lease_digest='0'*64,expected_post_state='reserved',expected_post_slot=True,expected_post_role='worker',expected_dispatch_name='work-wait-boost-'+'1'*32,send_receipt=None,send_state=None,slot_acquired_version=None,slot_acquire_digest=None,attempt_request_digest='d'*64); document['rows'][boost_key('scope','wait')]=row; return row\nif not wait.read()['rows']:\n try: wait.change(0,lambda d:d['rows'].update({boost_key('scope','wait'):boosted()}),now=2.0)\n except Exception: pass\nif not p0.read()['roots']:\n try: p0.activate_root('scope','root',1.0,set_by='user',expected_version=0,now=1.0)\n except Exception: pass\nif hold:\n (base/'ready').write_text(feature)\n while not (base/'release').exists(): time.sleep(.01)\nelse:\n (base/'second-ready').write_text(feature)\n while not (base/'release').exists(): time.sleep(.01)\nsucceeded=False\nif feature=='p0':\n f={'scope':'scope','root_id':'root','root_created_at':1.0,'item_id':'p0','continuation':'initial','predecessor_lease_id':None}\n for _ in range(50):\n  s=p0.read()\n  try: _,row=p0.create_intent(f,expected_version=s['version'],now=3.0); succeeded=True; break\n  except Exception: time.sleep(.01)\nelse:\n c=AssignmentKeyCoordinator(ledger,p0_reader=P0StoreDependencyReader(p0),wait_reader=WaitStoreDependencyReader(wait))\n for _ in range(50):\n  s=wait.read()\n  try: _,row=c.create_wait_dependency(wait,s['version'],consuming,target_key=boost_key('scope','wait'),attempt_id='1'*32,now=3.0); succeeded=True; break\n  except Exception: time.sleep(.01)\nif not succeeded: raise SystemExit(2)\nprint(json.dumps({'feature':feature,'key':(base/'work-p0-assignment.key').read_text(),'succeeded':True}))\n""",
        encoding="utf-8",
    )
    for first_feature, second_feature in (("p0", "wait"), ("wait", "p0")):
        run = tmp_path / (first_feature + "-first")
        run.mkdir()
        first = subprocess.Popen(
            [sys.executable, str(script), str(src), str(run), first_feature, "hold"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        import time
        deadline = time.time() + 5
        while not (run / "ready").exists():
            assert first.poll() is None and time.time() < deadline
            time.sleep(0.01)
        second = subprocess.Popen(
            [sys.executable, str(script), str(src), str(run), second_feature, "go"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        while not (run / "second-ready").exists():
            assert second.poll() is None and time.time() < deadline
            time.sleep(0.01)
        (run / "release").write_text("go")
        rows = []
        for process in (first, second):
            out, err = process.communicate(timeout=15)
            assert process.returncode == 0, err
            rows.append(json.loads(out))
        assert all(row["succeeded"] is True for row in rows)
        assert len({row["key"] for row in rows}) == 1
        assert len(rows[0]["key"]) == 64
        p0_state = json.loads((run / "work-parallel-p0.json").read_text())
        wait_state = json.loads((run / "work-parallel-wait-boost-v1.json").read_text())
        assert len(p0_state["intents"]) == 1
        assert wait_state["rows"]["scope\u0000wait"]["state"] == "consuming"

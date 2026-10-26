import sys,json,unittest,importlib.util
from pathlib import Path
from decimal import Decimal
WS=Path(sys.argv[1]);OUT=Path(sys.argv[2]);sys.path.insert(0,str(WS))
def load(name):
 spec=importlib.util.spec_from_file_location(name,WS/(name+".py"));m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
class CacheTests(unittest.TestCase):
 def setUp(self):self.m=load("mvcc_cache");self.now=100;self.clock=lambda:self.now
 def test_A0_ttl(self):
  c=self.m.Cache(self.clock);c.set("a",1,ttl=5);self.assertEqual(c.get("a"),1);self.now=105;self.assertEqual(c.get("a","missing"),"missing");c.set("z",9,ttl=0);self.assertIsNone(c.get("z"))
 def test_A0_frozen_snapshot(self):
  c=self.m.Cache(self.clock);c.set("a",1,ttl=5);c.set("b",2);s=c.snapshot();self.now=200;c.set("a",3);c.delete("b");self.assertEqual(s.get("a"),1);self.assertEqual(s.get("b"),2)
 def test_A0_read_conflict_atomic(self):
  c=self.m.Cache(self.clock);c.set("a",1);t=c.transaction();self.assertEqual(t.get("a"),1);t.set("b",2);c.set("a",4)
  with self.assertRaises(self.m.ConflictError):t.commit()
  self.assertIsNone(c.get("b"))
 def test_A0_blind_write_tombstone(self):
  c=self.m.Cache(self.clock);t=c.transaction();t.set("a",1);c.set("a",2);c.delete("a")
  with self.assertRaises(self.m.ConflictError):t.commit()
 def test_A0_read_your_writes_close(self):
  c=self.m.Cache(self.clock);c.set("a",1);t=c.transaction();c.set("a",2);self.assertEqual(t.get("a"),1);t.set("x",3);self.assertEqual(t.get("x"),3);t.delete("x");self.assertIsNone(t.get("x"));t.rollback()
  for f in [lambda:t.get("a"),lambda:t.set("a",2),lambda:t.commit(),lambda:t.rollback()]:
   with self.assertRaises(RuntimeError):f()
 def test_A0_ttl_at_commit(self):
  c=self.m.Cache(self.clock);t=c.transaction();t.set("a",8,ttl=10);self.now=500;t.commit();self.now=509;self.assertEqual(c.get("a"),8);self.now=510;self.assertIsNone(c.get("a"))
 def test_A1_fifo_not_lru(self):
  c=self.m.Cache(self.clock,capacity=2);c.set("a",1);c.set("b",2);c.get("a");c.set("a",9);s=c.snapshot();c.set("c",3);self.assertIsNone(c.get("a"));self.assertEqual(c.get("b"),2);self.assertEqual(s.get("a"),9)
 def test_A1_expired_capacity_eviction_conflict(self):
  c=self.m.Cache(self.clock,capacity=2);c.set("a",1,ttl=1);c.set("b",2);self.now=102;c.set("c",3);self.assertEqual(c.get("b"),2);t=c.transaction();t.get("b");t.set("x",99);c.set("d",4)
  with self.assertRaises(self.m.ConflictError):t.commit()
  self.assertIsNone(c.get("x"))
 def test_A0_read_missing_conflicts(self):
  c=self.m.Cache(self.clock);t=c.transaction();self.assertIsNone(t.get("missing"));t.set("safe",1);c.set("missing",2)
  with self.assertRaises(self.m.ConflictError):t.commit()
  self.assertIsNone(c.get("safe"))
 def test_A0_failed_commit_closes(self):
  c=self.m.Cache(self.clock);c.set("a",1);t=c.transaction();t.get("a");c.set("a",2)
  with self.assertRaises(self.m.ConflictError):t.commit()
  with self.assertRaises(RuntimeError):t.set("x",3)
 def test_A1_write_order_and_capacity(self):
  for n in [0,-1]:
   with self.assertRaises(ValueError):self.m.Cache(self.clock,capacity=n)
  c=self.m.Cache(self.clock,capacity=2);t=c.transaction();t.set("z",1);t.set("a",2);t.set("m",3);t.commit();self.assertIsNone(c.get("z"));self.assertEqual(c.get("a"),2);self.assertEqual(c.get("m"),3)
class SchedulerTests(unittest.TestCase):
 def setUp(self):self.m=load("dag_scheduler")
 def test_B0_dependencies_and_priority(self):
  s=self.m.Scheduler();s.add("a");s.add("b",deps=("a",),priority=100);s.add("c",priority=2);self.assertEqual(s.ready(),["c","a"])
  with self.assertRaises(self.m.StateError):s.start("b")
  s.start("a");s.finish("a");self.assertEqual(s.ready(),["b","c"])
 def test_B1_insertion_not_lexical(self):
  s=self.m.Scheduler();s.add("z",priority=7);s.add("a",priority=7);s.add("m",priority=8);self.assertEqual(s.ready(),["m","z","a"])
 def test_B1_failed_dependency_retry(self):
  s=self.m.Scheduler();s.add("a");s.add("b",deps=("a",));s.start("a");s.finish("a",success=False);self.assertEqual(s.ready(),[]);s.retry("a");self.assertEqual(s.ready(),["a"]);s.start("a");s.finish("a");self.assertEqual(s.ready(),["b"])
 def test_B1_cycle_change_atomic(self):
  s=self.m.Scheduler();s.add("a");s.add("b",deps=("a",));s.add("c",deps=("b",));before=s.ready()
  with self.assertRaises(self.m.CycleError):s.replace_deps("a",("c",))
  self.assertEqual(s.ready(),before)
  with self.assertRaises(self.m.CycleError):s.replace_deps("a",("a",))
  self.assertEqual(s.ready(),before)
  with self.assertRaises(ValueError):s.replace_deps("a",("missing",))
  self.assertEqual(s.ready(),before);s.start("a");s.finish("a");self.assertEqual(s.ready(),["b"])
 def test_B1_replace_and_nonpending(self):
  s=self.m.Scheduler();s.add("a");s.add("b");s.add("c",deps=("a",));s.replace_deps("c",("b",));s.start("a");s.finish("a");self.assertEqual(s.ready(),["b"])
  with self.assertRaises(self.m.StateError):s.replace_deps("a",())
  with self.assertRaises(self.m.StateError):s.retry("b")
 def test_B0_finish_requires_running(self):
  s=self.m.Scheduler();s.add("a")
  with self.assertRaises(self.m.StateError):s.finish("a")
  s.start("a");s.finish("a")
  with self.assertRaises(self.m.StateError):s.start("a")
 def test_B0_unknown_dependency(self):
  s=self.m.Scheduler()
  with self.assertRaises(ValueError):s.add("a",deps=("missing",))
  self.assertEqual(s.ready(),[])
class LedgerTests(unittest.TestCase):
 def setUp(self):self.m=load("ledger")
 def test_C0_chunks_quotes_newline_decimal(self):
  q=chr(34);nl=chr(13)+chr(10)
  text="id,account,amount"+nl+"1,"+q+"bank,main"+q+",0.10"+nl+"2,"+q+"two"+chr(10)+"lines"+q+",-0.20"+nl+"3,"+q+"quote"+q+q+"acct"+q+",0.30"+nl
  expected={"bank,main":Decimal("0.10"),"two"+chr(10)+"lines":Decimal("-0.20"),"quote"+q+"acct":Decimal("0.30")}
  for n in [1,2,3,5,17,1000]:self.assertEqual(self.m.reconcile(text[i:i+n] for i in range(0,len(text),n)),expected)
 def test_C0_empty_blank(self):
  self.assertEqual(self.m.reconcile([]),{});self.assertEqual(self.m.reconcile(["id,account,amount"+chr(10)+chr(10)]),{})
 def test_C1_last_row_wins_account_change_zero(self):
  text=chr(10).join(["id,account,amount","1,A,10","2,B,2","1,B,3","2,A,-1","3,A,1",""])
  self.assertEqual(self.m.reconcile([text]),{"A":Decimal(0),"B":Decimal(3)})
 def test_C1_invalid_overridden_row_still_rejected(self):
  for bad in ["NaN","Infinity","-Infinity","bad",""]:
   with self.subTest(bad=bad),self.assertRaises(ValueError):self.m.reconcile([chr(10).join(["id,account,amount","1,A,"+bad,"1,A,4",""])])
  for row in [",A,1","1,,1"]:
   with self.assertRaises(ValueError):self.m.reconcile(["id,account,amount"+chr(10)+row+chr(10)])
 def test_C0_exact_not_float(self):
  self.assertEqual(self.m.reconcile([chr(10).join(["id,account,amount","1,A,0.1","2,A,0.2","3,A,-0.3",""])]),{"A":Decimal(0)})
if __name__=="__main__":
 suite=unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
 with OUT.open("w",encoding="utf-8") as f:result=unittest.TextTestRunner(stream=f,verbosity=2).run(suite)
 summary={"tests":result.testsRun,"failures":[{"test":str(t),"traceback":s} for t,s in result.failures],"errors":[{"test":str(t),"traceback":s} for t,s in result.errors],"passed":result.wasSuccessful(),"scope":"separate original-plus-amended requirement assertions, no fixes applied to evaluated code"}
 OUT.with_suffix(".json").write_text(json.dumps(summary,indent=2),encoding="utf-8");print(json.dumps(summary,indent=2));raise SystemExit(not result.wasSuccessful())

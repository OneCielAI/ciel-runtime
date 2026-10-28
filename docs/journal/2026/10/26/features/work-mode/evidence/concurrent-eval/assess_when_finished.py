import sys,time,json,subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent
RUN=Path((HERE/"latest-run.txt").read_text(encoding="utf-8"))
end=time.monotonic()+1500
while not (RUN/"result.json").exists():
 if time.monotonic()>end:raise TimeoutError("owned evaluation did not finish")
 time.sleep(3)
result=json.loads((RUN/"result.json").read_text(encoding="utf-8"))
subprocess.run([sys.executable,"-I",str(HERE/"assess.py"),str(RUN)],check=True,timeout=30)
ws=RUN/"workspace"
if all((ws/name).is_file() for name in ["mvcc_cache.py","dag_scheduler.py","ledger.py"]):
 p=subprocess.run([sys.executable,"-I",str(HERE/"independent_verify.py"),str(ws),str(RUN/"independent-tests.txt")],capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=90)
 (RUN/"independent-run.txt").write_text(p.stdout+p.stderr,encoding="utf-8")
 print("independent_exit",p.returncode,flush=True)
 print(p.stdout,flush=True)
else:
 print("coding_score_unavailable: missing implementation files",flush=True)
print("assessed",str(RUN),flush=True)

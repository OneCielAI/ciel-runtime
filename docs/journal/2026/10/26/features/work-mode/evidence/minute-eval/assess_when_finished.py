import sys,json,subprocess,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
while not (HERE/"latest-run.txt").exists():time.sleep(1)
RUN=Path((HERE/"active-assessment-run.txt").read_text(encoding="utf-8"))
end=time.monotonic()+2900
while not (RUN/"result.json").exists():
 if time.monotonic()>end:raise TimeoutError("evaluation timeout")
 time.sleep(3)
p=subprocess.run([sys.executable,"-I",str(HERE/"assess.py"),str(RUN)],capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=30)
(RUN/"assessment-output.txt").write_text(p.stdout+p.stderr,encoding="utf-8");print(p.stdout,flush=True)
if all((RUN/"workspace"/n).is_file() for n in ["routing_graph.py","booking_calendar.py","inventory_stream.py"]):
 end=time.monotonic()+120
 while not (HERE/"independent_verify.py").exists() and time.monotonic()<end:time.sleep(2)
 p=subprocess.run([sys.executable,"-I",str(HERE/"independent_verify.py"),str(RUN/"workspace"),str(RUN/"independent-tests.txt")],capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=120)
 (RUN/"independent-run.txt").write_text(p.stdout+p.stderr,encoding="utf-8");print("independent test exit",p.returncode,flush=True);print(p.stdout,flush=True)
else:print("missing implementation files; no valid coding score",flush=True)
print("assessment directory",str(RUN),flush=True)

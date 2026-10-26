"""Detached console action driver, confined to one harness-owned CLI."""
import json,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[7]
sys.path.insert(0,str(REPO/'docs/journal/2026/10/16/features/cielairouter-direct-path/evidence/e2e'))
from console_driver import Console
PID,RUN=int(sys.argv[1]),Path(sys.argv[2])
for _ in range(100):
 try:c=Console(PID);break
 except OSError:time.sleep(.2)
else:raise SystemExit(3)
(RUN/'attached').write_text(str(time.time()))
last='';count=0;handled=0
while not (RUN/'stop-console').exists():
 try:s=c.screen()
 except OSError:break
 if s!=last:
  last=s;count+=1;(RUN/'screen-latest.txt').write_text(s,encoding='utf-8')
  if s.strip():(RUN/f'screen-{count:03}.txt').write_text(s,encoding='utf-8')
 if 'Accessing workspace' in s or 'Yes, I accept' in s:
  if time.time()-handled>3:c.key('enter');handled=time.time()
 req=RUN/'console-command.json'
 if req.exists():
  q=json.loads(req.read_text());req.unlink()
  c.text(q['text']);time.sleep(.3);c.key('enter')
  (RUN/'command-sent.json').write_text(json.dumps(q),encoding='utf-8')
 time.sleep(.2)

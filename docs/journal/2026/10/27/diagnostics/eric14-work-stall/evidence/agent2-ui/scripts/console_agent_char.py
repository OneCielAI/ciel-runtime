"""Detached console action driver, confined to one harness-owned CLI."""
import json,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
REPO=next(p for p in HERE.parents if (p/'ciel_runtime.py').exists())
sys.path.insert(0,str(REPO/'docs/journal/2026/10/16/features/cielairouter-direct-path/evidence/e2e'))
from console_driver import Console, VK
VK["tab"]=0x09
PID,RUN=int(sys.argv[1]),Path(sys.argv[2])
for _ in range(100):
 try:c=Console(PID);break
 except OSError:time.sleep(.2)
else:raise SystemExit(3)
(RUN/'attached').write_text(str(time.time()))
last='';count=0;handled=0;reason='stop-console'
# 2026-10-26: {'key': name} sends one key without text (dialog confirmation); exit reason recorded in agent-exit.
try:
 while not (RUN/'stop-console').exists():
  try:s=c.screen()
  except OSError:reason='screen-oserror';break
  if s!=last:
   last=s;count+=1;(RUN/'screen-latest.txt').write_text(s,encoding='utf-8')
   if s.strip():(RUN/f'screen-{count:03}.txt').write_text(s,encoding='utf-8')
  if 'Accessing workspace' in s or 'Yes, I accept' in s:
   if time.time()-handled>3:c.key('enter');handled=time.time()
  req=RUN/'console-command.json'
  if req.exists():
   q=json.loads(req.read_text());req.unlink()
   if q.get('key')=='ctrl-x':
    records=[c._record(0x58,chr(24),True),c._record(0x58,chr(24),False)]
    for record in records:record.Event.KeyEvent.dwControlKeyState=8
    c._write(records)
   elif q.get('key')=='tab':c._write([c._record(0x09,chr(9),True),c._record(0x09,chr(9),False)])
   elif q.get('char'):c.text(q['char'])
   elif q.get('key'):c.key(q['key'])
   else:c.text(q['text']);time.sleep(.3);c.key('enter')
   (RUN/'command-sent.json').write_text(json.dumps(q),encoding='utf-8')
  time.sleep(.2)
except BaseException as e:
 reason='exception '+type(e).__name__;raise
finally:
 (RUN/'agent-exit').write_text(json.dumps({'reason':reason,'t':time.time(),'screens':count}),encoding='utf-8')

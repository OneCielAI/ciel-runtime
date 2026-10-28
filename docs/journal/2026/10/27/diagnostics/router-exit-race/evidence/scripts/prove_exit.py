"""Owned TCP router process: force a supervisor read to overlap native CLI exit."""
import argparse, importlib.util, json, os, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path
A=argparse.ArgumentParser(); A.add_argument('module'); A.add_argument('out'); args=A.parse_args()
spec=importlib.util.spec_from_file_location('owned_lifecycle',args.module);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
root=Path(tempfile.mkdtemp(prefix='exit-proof-')); events=[]; children=[]; entered=threading.Event(); release=threading.Event()
with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
code="import socket,time,sys;s=socket.socket();s.bind(('127.0.0.1',int(sys.argv[1])));s.listen();print('READY',flush=True);time.sleep(60)"
def reachable():
 try:
  with socket.create_connection(('127.0.0.1',port),timeout=.2):return True
 except OSError:return False
def spawn(**kw):
 p=subprocess.Popen([sys.executable,'-I','-u','-c',code,str(port)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);children.append(p)
 assert p.stdout.readline().strip()=='READY';events.append({'event':'spawn','pid':p.pid});return True
def health():
 entered.set();assert release.wait(5);return None
def stop(*a,**kw):
 events.append({'event':'stop'})
 for p in children:
  if p.poll() is None:p.terminate();p.wait(5)
 return True
sup=m.RouterClientSupervisor('owned TCP '+str(port),m.RouterClientSupervisorPorts(router_health=health,listener_reachable=lambda:False,health_matches_current=lambda h:False,health_config_matches_current=lambda h:False,health_summary=str,start_router=spawn,log=lambda *a:None))
sup.interval_seconds=lambda:.01;sup.down_confirmation_seconds=lambda:0
lease=root/'client.json'
def register():lease.write_text('{}');return lease
def release_lease(p):p.unlink();events.append({'event':'release'})
def native():
 assert entered.wait(5)
 threading.Timer(.2,release.set).start()
 return subprocess.call([sys.executable,'-I','-c','pass'])
runner=m.RouterLifetimeRunner(m.RouterLifetimeRunnerPorts(register_client=register,release_client=release_lease,start_supervisor=sup.start,stop_if_idle=stop,log=lambda *a:None))
try:
 rc=runner.run(native,True);time.sleep(1.5)
 observed={'module':args.module,'events':events.copy(),'exit_code':rc,'lease_exists':lease.exists(),'port_listening_after_exit':reachable(),'alive_owned_router_pids':[p.pid for p in children if p.poll() is None]}
 Path(args.out).write_text(json.dumps(observed,indent=1),encoding='utf-8');print(json.dumps(observed,indent=1))
finally:
 release.set();stop()

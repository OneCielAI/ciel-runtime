import os,sys,time,json,subprocess,tempfile,ctypes,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[8]; OUT=Path(__file__).parent/'menu'; OUT.mkdir(exist_ok=True)
SCR=Path(tempfile.mkdtemp(prefix='ciel-menu-',dir='C:/Windows/Temp')); CFG=SCR/'cfg'; CFG.mkdir(); WS=SCR/'ws'; WS.mkdir()
(CFG/'config.json').write_text(json.dumps({'current_provider':'cielairouter','language':'en','providers':{'cielairouter':{'base_url':'http://127.0.0.1:9','current_model':'owned-send','api_key':'owned-dummy'}}}))
env={k:v for k,v in os.environ.items() if not k.upper().startswith(('CIEL_RUNTIME_','CLAUDE','ANTHROPIC','CODEX','OPENAI'))};env.update(CIEL_RUNTIME_CONFIG_DIR=str(CFG),CIEL_RUNTIME_TEST_ISOLATED='1',CIEL_RUNTIME_SKIP_PATH_REGISTRATION='1',HOME=str(SCR/'home'),USERPROFILE=str(SCR/'home'))
(SCR/'home').mkdir(); env['PATH']=str(ROOT)+';'+env.get('PATH','')
cmd=[sys.executable,'-I','-c',"import sys;sys.path.insert(0,sys.argv[1]);import ciel_runtime;ciel_runtime.portable_prelaunch_menu()",str(ROOT)]
startup=subprocess.STARTUPINFO();startup.dwFlags=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=7
p=subprocess.Popen(cmd,cwd=WS,env=env,creationflags=subprocess.CREATE_NEW_CONSOLE,startupinfo=startup)
sys.path.insert(0,str(ROOT/'docs/journal/2026/10/16/features/cielairouter-direct-path/evidence/e2e'));from console_driver import Console
for _ in range(100):
 try:c=Console(p.pid);break
 except OSError:time.sleep(.2)
else:raise SystemExit('attach failed')
def wait(text,secs=15):
 end=time.time()+secs
 while time.time()<end:
  s=c.screen()
  if text in s:return s
  time.sleep(.2)
 raise SystemExit('not found '+text+'\n'+s)
def write_png(label):
 import ctypes
 from ctypes import wintypes
 from PIL import Image
 u=ctypes.WinDLL('user32');g=ctypes.WinDLL('gdi32'); hwnd=c.hwnd
 class R(ctypes.Structure):_fields_=[('l',ctypes.c_long),('t',ctypes.c_long),('r',ctypes.c_long),('b',ctypes.c_long)]
 class B(ctypes.Structure):_fields_=[('s',ctypes.c_uint),('w',ctypes.c_int),('h',ctypes.c_int),('p',ctypes.c_ushort),('b',ctypes.c_ushort),('c',ctypes.c_uint),('i',ctypes.c_uint),('x',ctypes.c_int),('y',ctypes.c_int),('z',ctypes.c_uint),('q',ctypes.c_uint)]
 for fn in (u.GetDC,g.CreateCompatibleDC,g.CreateCompatibleBitmap,g.SelectObject): fn.restype=ctypes.c_void_p
 u.GetDC.argtypes=[ctypes.c_void_p];g.CreateCompatibleDC.argtypes=[ctypes.c_void_p];g.CreateCompatibleBitmap.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int];g.SelectObject.argtypes=[ctypes.c_void_p,ctypes.c_void_p];u.PrintWindow.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint];g.GetDIBits.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint];g.DeleteObject.argtypes=[ctypes.c_void_p];g.DeleteDC.argtypes=[ctypes.c_void_p];u.ReleaseDC.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
 u.ShowWindow(hwnd,4);time.sleep(.8);r=R();u.GetWindowRect(hwnd,ctypes.byref(r));w,h=r.r-r.l,r.b-r.t;dc=u.GetDC(hwnd);mem=g.CreateCompatibleDC(dc);bm=g.CreateCompatibleBitmap(dc,w,h);old=g.SelectObject(mem,bm);u.PrintWindow(hwnd,mem,2);buf=ctypes.create_string_buffer(w*h*4);bi=B(ctypes.sizeof(B),w,-h,1,32,0,w*h*4,0,0,0,0);g.GetDIBits(mem,bm,0,h,buf,ctypes.byref(bi),0);Image.frombuffer('RGB',(w,h),buf,'raw','BGRX',0,1).save(OUT/(label+'.png'));g.SelectObject(mem,old);g.DeleteObject(bm);g.DeleteDC(mem);u.ReleaseDC(hwnd,dc);u.ShowWindow(hwnd,7)
wait('Provider');
for _ in range(3):c.key('up')
c.key('enter');wait('LLM options');
for _ in range(6):c.key('down')
s=wait('Tool blocklist (routed)');(OUT/'screen.txt').write_text(s,encoding='utf-8');write_png('tool-blocklist')
c.key('enter');wait('blocked_tools for cielairouter');c.text('["SendMessage"]');c.key('enter');wait('Provider tool blocklist:')
time.sleep(.5);(OUT/'screen-saved.txt').write_text(c.screen(),encoding='utf-8');write_png('tool-blocklist-saved')
assert json.loads((CFG/'config.json').read_text())['providers']['cielairouter']['blocked_tools']==['SendMessage']
c.key('enter');wait('blocked_tools for cielairouter');c.text('default');c.key('enter');time.sleep(1)
assert 'blocked_tools' not in json.loads((CFG/'config.json').read_text())['providers']['cielairouter']
(OUT/'roundtrip.json').write_text(json.dumps({'saved':['SendMessage'],'reset':'missing','config_dir':str(CFG),'only_owned_config':True}))
c.key('esc');c.key('esc');p.terminate();print(OUT)

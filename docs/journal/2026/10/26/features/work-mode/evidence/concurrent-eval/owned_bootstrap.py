import os,sys,runpy
from pathlib import Path
repo,output=sys.argv[1:3]
Path(output,'launcher.pid').write_text(str(os.getpid()))
sys.path.insert(0,repo)
sys.argv=[repo+'/ciel_runtime.py','cli','--ca-runtime','claude','--ca-no-update-check','--ca-no-self-update-check']
runpy.run_path(sys.argv[0],run_name='__main__')

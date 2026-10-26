import os,runpy,json
from pathlib import Path
r=Path(__file__).resolve().parent
os.environ['WORK_PROOF_REPO']=json.loads((r/'navigation-snapshot.json').read_text())['snapshot']
runpy.run_path(str(r.parent/'navigation/e2e_navigation.py'),run_name='__main__')

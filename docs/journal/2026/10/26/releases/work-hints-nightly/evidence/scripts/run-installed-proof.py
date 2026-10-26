"""Run the real-CLI Work hints and queue E2E against the installed e1826009 snapshot."""
import os, runpy
os.environ['WORK_PROOF_REPO'] = r'C:\Users\djlov\.local\share\ciel-runtime-e1826009db5e9bffa50355f7edb3e12709a50bdd'
runpy.run_path(r'C:\Users\djlov\ciel-runtime-wt-work\docs\journal\2026\10\26\features\work-mode-hints\evidence\e2e_hints.py', run_name='__main__')

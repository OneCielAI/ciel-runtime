"""Run the existing real-CLI Work navigation E2E against the installed 2faae14 snapshot."""
import os, runpy
os.environ['WORK_PROOF_REPO'] = r'C:\Users\djlov\.local\share\ciel-runtime-2faae14453a948936e76cdfd3c1ee77b4d291371'
runpy.run_path(r'C:\Users\djlov\ciel-runtime-wt-work\docs\journal\2026\10\26\features\work-mode\evidence\navigation\e2e_navigation.py', run_name='__main__')

"""Run the real-CLI Work hints and queue E2E against an installed snapshot (default: the 81f6a4b fix snapshot)."""
import os, runpy, sys
os.environ['WORK_PROOF_REPO'] = sys.argv[1] if len(sys.argv) > 1 else r'C:\Users\djlov\.local\share\ciel-runtime-81f6a4ba5af240e6a3aa3e4437e8701a8c9bafa1'
runpy.run_path(r'C:\Users\djlov\ciel-runtime-wt-work\docs\journal\2026\10\26\features\work-mode-hints\evidence\e2e_hints.py', run_name='__main__')

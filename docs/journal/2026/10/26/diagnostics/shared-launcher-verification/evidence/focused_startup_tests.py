import os,sys,importlib.util,unittest
from pathlib import Path
for k in list(os.environ):
    if k.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")):
        del os.environ[k]
ROOT=next(p for p in Path(__file__).resolve().parents if (p/"ciel_runtime.py").exists())
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location("owned_group_runner",ROOT/"scripts/run_test_group.py")
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
with module.isolate_runtime_state():
    suite=unittest.defaultTestLoader.discover(str(ROOT/"tests"),pattern="test_router_process_startup.py")
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

import os, sys, tempfile, socket, unittest
from pathlib import Path
ROOT=next(p for p in Path(__file__).resolve().parents if (p/'ciel_runtime.py').exists())
for k in list(os.environ):
    if k.startswith('CIEL_RUNTIME_'): os.environ.pop(k)
with tempfile.TemporaryDirectory(prefix='ciel-send-tests-') as state:
    with socket.socket() as s: s.bind(('127.0.0.1',0)); port=s.getsockname()[1]
    os.environ.update(CIEL_RUNTIME_CONFIG_DIR=state, CIEL_RUNTIME_ROUTER_PORT=str(port), CIEL_RUNTIME_TEST_ISOLATED='1')
    sys.path.insert(0,str(ROOT))
    suite=unittest.TestSuite()
    for name in ('test_provider_tool_policy.py','test_provider_option_panel.py','test_cron_tools.py','test_review_command_passthrough.py'):
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern=name))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())

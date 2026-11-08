"""Verify the installed graphical-Observatory beta without touching live runtime state.

usage: py -3 .local/verify_installed_beta_graphic.py .local/beta-registry-<sha7>/verification.json
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
metadata = json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
version = metadata['version']
sha = metadata['git_head']
snapshot = Path.home() / '.local/share' / ('ciel-runtime-' + sha)
global_root = Path(os.environ['APPDATA']) / 'npm/node_modules/@one-ciel-ai/ciel-runtime'
MOD = 'ciel_runtime_support/claude_mods/ciel-work'
for root in (snapshot, global_root):
    assert json.loads((root / 'package.json').read_text(encoding='utf8'))['version'] == version
    assert 'VERSION = "' + version + '"' in (root / 'ciel_runtime_support/runtime_constants.py').read_text(encoding='utf8')
    register = (root / MOD / 'hooks/register.js').read_text(encoding='utf8')
    assert "module: './observatory.client.js'" in register and 'sideActors' in register
    client = (root / MOD / 'hooks/observatory.client.js').read_text(encoding='utf8')
    assert 'WORK TREE' in client and 'ACTIVE FORKED SSE SESSIONS' in client
files = [p.relative_to(snapshot).as_posix() for p in snapshot.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
differences = [p for p in files if not (global_root / p).is_file() or (snapshot / p).read_bytes().replace(b'\r\n', b'\n') != (global_root / p).read_bytes().replace(b'\r\n', b'\n')]
assert not differences, differences
claude = shutil.which('claude')
validate = subprocess.run([claude, 'plugin', 'validate', str(snapshot / MOD)], capture_output=True, text=True,
                          encoding='utf8', errors='replace', timeout=120)
assert validate.returncode == 0, (validate.stdout, validate.stderr)
with tempfile.TemporaryDirectory(prefix='installed-beta-smoke-') as raw:
    workspace = Path(raw)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('CIEL_RUNTIME_', 'CLAUDE', 'ANTHROPIC', 'CODEX', 'OPENAI'))}
    env.update(CIEL_RUNTIME_CONFIG_DIR=str(workspace / 'config'), CIEL_RUNTIME_SKIP_SELF_UPDATE='1',
               CIEL_RUNTIME_PYTHON=sys.executable, PYTHONIOENCODING='utf-8')
    ctl = ['node', str(global_root / 'npm-bin/ciel-runtimectl.js')]
    commands = [
        [sys.executable, '-I', '-c', "import runpy,sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p.parent));sys.argv=[str(p),'version'];runpy.run_path(str(p),run_name='__main__')", str(snapshot / 'ciel_runtime.py')],
        ctl + ['version'],
        ctl + ['work', 'list', '--json'],
        ctl + ['work', 'set', 'work_parallel_max_active', '3', '--json'],
        ctl + ['work', 'get', 'work_parallel_max_active', '--json'],
    ]
    observations = []
    for command in commands:
        result = subprocess.run(command, cwd=workspace, env=env, capture_output=True, text=True,
                                encoding='utf8', errors='replace', timeout=60)
        assert result.returncode == 0, (command[-4:], result.returncode, result.stdout, result.stderr)
        observations.append(result.stdout.strip())
    assert version in observations[0] and version in observations[1]
    settings = json.loads(observations[2])['settings']
    # The settings projection reports parallel numbers as their text form.
    assert str(json.loads(observations[-1])['settings'][0]['value']) == '3', observations[-1]
report = {'version': version, 'git_head': sha, 'snapshot': str(snapshot), 'global_package': str(global_root),
          'matching_package_files': len(files), 'commands_passed': len(commands), 'ctl_settings': len(settings),
          'plugin_validate': validate.stdout.strip().splitlines()[-1:], 'graphical_client_present': True,
          'isolated_config_only': True, 'live_restarts': False,
          'register_sha256': hashlib.sha256((snapshot / MOD / 'hooks/register.js').read_bytes()).hexdigest(),
          'client_sha256': hashlib.sha256((snapshot / MOD / 'hooks/observatory.client.js').read_bytes()).hexdigest()}
output = ROOT / 'docs/journal/2026/11/07/features/work-observatory-graphical/evidence' / ('installed-beta-smoke-' + sha[:7] + '.json')
output.parent.mkdir(parents=True, exist_ok=True)
assert not output.exists(), 'Preserve earlier install evidence'
output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
print(json.dumps(report, indent=2))

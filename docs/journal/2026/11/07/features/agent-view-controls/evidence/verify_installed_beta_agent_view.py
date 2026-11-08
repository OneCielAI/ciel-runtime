"""Verify future-launch package and snapshot without touching live runtime state."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
metadata = json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
version = metadata['version']
sha = metadata['git_head']
snapshot = Path.home() / '.local/share' / ('ciel-runtime-' + sha)
global_root = Path(os.environ['APPDATA']) / 'npm/node_modules/@one-ciel-ai/ciel-runtime'
for root in (snapshot, global_root):
    assert json.loads((root / 'package.json').read_text(encoding='utf8'))['version'] == version
    assert 'VERSION = "' + version + '"' in (root / 'ciel_runtime_support/runtime_constants.py').read_text(encoding='utf8')
files = [p.relative_to(snapshot).as_posix() for p in snapshot.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
differences = [p for p in files if not (global_root / p).is_file() or (snapshot / p).read_bytes().replace(b'\r\n', b'\n') != (global_root / p).read_bytes().replace(b'\r\n', b'\n')]
assert not differences, differences
with tempfile.TemporaryDirectory(prefix='installed-beta-smoke-') as raw:
    workspace = Path(raw)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('CIEL_RUNTIME_', 'CLAUDE', 'ANTHROPIC', 'CODEX', 'OPENAI'))}
    env.update(CIEL_RUNTIME_CONFIG_DIR=str(workspace / 'config'), CIEL_RUNTIME_SKIP_SELF_UPDATE='1',
               CIEL_RUNTIME_PYTHON=sys.executable, PYTHONIOENCODING='utf-8')
    observations = []
    commands = [
        [sys.executable, '-I', '-c', "import runpy,sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p.parent));sys.argv=[str(p),'version'];runpy.run_path(str(p),run_name='__main__')", str(snapshot / 'ciel_runtime.py')],
        ['node', str(global_root / 'npm-bin/ciel-runtimectl.js'), 'version'],
        ['node', str(global_root / 'npm-bin/ciel-runtimectl.js'), 'work', 'list', '--json'],
        ['node', str(global_root / 'npm-bin/ciel-runtimectl.js'), 'work', 'set', 'work_side_intake_context_mode', 'recent', '--json'],
        ['node', str(global_root / 'npm-bin/ciel-runtimectl.js'), 'work', 'set', 'work_side_intake_context_percent', '30', '--json'],
        ['node', str(global_root / 'npm-bin/ciel-runtimectl.js'), 'work', 'get', 'work_side_intake_context_mode', '--json'],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=workspace, env=env, capture_output=True, text=True,
                                encoding='utf8', errors='replace', timeout=60)
        assert result.returncode == 0, (command[-4:], result.returncode, result.stdout, result.stderr)
        observations.append(result.stdout.strip())
    assert version in observations[0] and version in observations[1]
    settings = json.loads(observations[2])['settings']
    keys = [row['key'] for row in settings]
    assert {'claude_left_arrow_opens_agents', 'claude_background_agents_enabled'} <= set(keys), keys
    agent_rows = [row for row in settings if row['key'].startswith('claude_')]
    assert json.loads(observations[-1])['settings'][0]['value'] == 'recent'
report = {'version': version, 'git_head': sha, 'snapshot': str(snapshot), 'global_package': str(global_root),
          'matching_package_files': len(files), 'commands_passed': len(commands), 'ctl_settings': len(settings), 'agent_view_rows': agent_rows,
          'isolated_config_only': True, 'live_restarts': False,
          'mod_sha256': hashlib.sha256((snapshot / 'ciel_runtime_support/claude_mods/ciel-work/hooks/register.js').read_bytes()).hexdigest()}
output = ROOT / 'docs/journal/2026/11/07/features/agent-view-controls/evidence' / ('installed-beta-smoke-' + sha[:7] + '.json')
output.parent.mkdir(parents=True, exist_ok=True)
assert not output.exists(), 'Preserve earlier install evidence'
output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
print(json.dumps(report, indent=2))

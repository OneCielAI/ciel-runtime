"""Real Codex binary, isolated CODEX_HOME/HOME: does the Work developer prompt carry the triage and numbering guidance?
Run: python -I <absolute path>. Uses `codex debug prompt-input` only (no model call). Set WORK_PROOF_REPO for a snapshot."""
import json, os, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = next(p for p in HERE.parents if (p / 'ciel_runtime.py').exists())
REPO = Path(os.environ.get('WORK_PROOF_REPO') or SOURCE)
sys.path.insert(0, str(REPO))
from ciel_runtime_support.work_mode_launch import codex_work_mode_args  # noqa: E402

CX = Path('C:/Users/djlov/AppData/Roaming/npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe')
ROOT = Path(tempfile.mkdtemp(prefix='codex-work-hints-'))
for name in ('home', 'appdata', 'localappdata', 'codex', 'ws'): (ROOT / name).mkdir()
ENV = {k: v for k, v in os.environ.items() if not k.upper().startswith(('CIEL_RUNTIME_', 'CLAUDE', 'ANTHROPIC', 'CODEX', 'OPENAI'))}
ENV.update(HOME=str(ROOT / 'home'), USERPROFILE=str(ROOT / 'home'), APPDATA=str(ROOT / 'appdata'), LOCALAPPDATA=str(ROOT / 'localappdata'), CODEX_HOME=str(ROOT / 'codex'))
probe = ['-c', 'mcp_servers.ciel-runtime-router.url="http://127.0.0.1:9/ca/mcp"']
args = codex_work_mode_args({}, native=False, router_mcp_attached=True)
proc = subprocess.run([str(CX), *probe, *args, 'debug', 'prompt-input', 'Verify Work triage guidance.'], cwd=ROOT / 'ws', env=ENV, capture_output=True, text=True, timeout=60)


def developer_texts(value):
    if isinstance(value, dict):
        if value.get('role') == 'developer': yield json.dumps(value, ensure_ascii=False)
        for item in value.values(): yield from developer_texts(item)
    elif isinstance(value, list):
        for item in value: yield from developer_texts(item)


developer = '\n'.join(developer_texts(json.loads(proc.stdout)))
expected = {
    'triage': 'Triage every new user request first',
    'numbered_root_item': 'its item number where possible',
    'dependent_child': 'goes under that item as a child (work_node_add with parent_id)',
    'queue': 'queues behind the',
    'how_to_turn_off': 'LLM options -> Work mode',
}
result = {'repo': str(REPO), 'codex_version': subprocess.run([str(CX), '--version'], env=ENV, capture_output=True, text=True).stdout.strip(),
          'exit': proc.returncode, 'checks': {k: v in developer for k, v in expected.items()}, 'temp_root': str(ROOT)}
(HERE / 'codex-prompt-check.json').write_text(json.dumps(result, indent=1), encoding='utf-8')
print(json.dumps(result, indent=1))

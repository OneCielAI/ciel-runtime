"""Does a relaunched Claude Code that reuses the same --messaging-socket-path get a session key?

ciel's restart-session relaunches Claude Code with the same socket path.  Case A: the new
instance starts after the old one exited.  Case B: it starts while the old one still runs.
"""
import json, os, secrets, shutil, sys, time
from pathlib import Path
sys.path.insert(0, r'C:\Users\djlov\ciel-runtime')
from ciel_runtime_support.windows_conpty import WindowsConPtySession
from ciel_runtime_support.claude_session_socket import session_key_hash

here = Path(__file__).parent
exe = str(here / 'p2.1.289/node_modules/@anthropic-ai/claude-code/bin/claude.exe')
B = chr(92)


def start(home: Path, pipe: str) -> WindowsConPtySession:
    env = {k: v for k, v in os.environ.items() if not k.startswith(('CIEL_RUNTIME_', 'ANTHROPIC_', 'CLAUDE_'))}
    env.update(USERPROFILE=str(home), HOME=str(home), ANTHROPIC_API_KEY='sk-ant-api03-dummy-not-real', DISABLE_AUTOUPDATER='1',
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
    return WindowsConPtySession([exe, '--messaging-socket-path', pipe, '--settings', '{"crossSessionInbound":"accept"}'],
                                env, log=lambda *_: None, mirror_output=False, forward_stdin=False)


def files(home: Path) -> list[str]:
    d = home / '.claude' / 'sessions'
    return sorted(p.name for p in d.glob('*')) if d.exists() else []


out = {}
for case in ('after_exit', 'overlapping'):
    home = here / f'race-{case}'
    shutil.rmtree(home, ignore_errors=True); (home / '.claude').mkdir(parents=True)
    (home / '.claude.json').write_text(json.dumps({'hasCompletedOnboarding': True, 'projects': {here.as_posix(): {'hasTrustDialogAccepted': True}}}))
    pipe = f'{B}{B}.{B}pipe{B}LOCAL{B}cc-msg-{secrets.token_hex(16)}'
    digest = session_key_hash(pipe, 'nt')
    first = start(home, pipe); time.sleep(12)
    record = {'first': files(home)}
    if case == 'after_exit':
        first.close(); time.sleep(0.5)
        record['after_first_exit'] = files(home)
        second = start(home, pipe); time.sleep(15)
    else:
        second = start(home, pipe); time.sleep(15)
        record['both_running'] = files(home)
        first.close(); time.sleep(3)
    record['second'] = files(home)
    record['second_key_present'] = any(name.endswith(f'.{digest}.key') for name in record['second'])
    second.close()
    out[case] = record
print(json.dumps(out, indent=1))

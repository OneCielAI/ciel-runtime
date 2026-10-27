# Read-only: runtime version, Work mod hash, router instances, health. No writes.
import hashlib, json, os, re, urllib.request
from pathlib import Path
H = Path(r'C:\ProgramData\aap-win\sandboxes\u21\home\ara-ai')
PKG = H / '.npm-global/node_modules/@one-ciel-ai/ciel-runtime'
def rd(p):
    try: return p.read_bytes()
    except Exception as e: return ('ERR '+type(e).__name__+' '+str(e)).encode()
const = rd(PKG/'ciel_runtime_support/runtime_constants.py').decode('utf-8','replace')
print('VERSION', re.findall(r'^VERSION = .*$', const, re.M))
print('package.json version', json.loads(rd(PKG/'package.json')).get('version') if (PKG/'package.json').exists() else None)
reg = PKG/'ciel_runtime_support/claude_mods/ciel-work/hooks/register.js'
b = rd(reg); print('register.js sha256', hashlib.sha256(b).hexdigest(), len(b))
cc = H/'.npm-global/node_modules/@anthropic-ai/claude-code/package.json'
print('claude-code', json.loads(rd(cc)).get('version') if cc.exists() else 'missing')
roam = H/'AppData/Roaming/ciel-runtime'
print('roaming exists', roam.exists())
try:
    for d in sorted((roam/'router-instances').iterdir()):
        files = sorted(d.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:6]
        print('instance', d.name, [(f.name, f.stat().st_size) for f in files])
except Exception as e: print('instances ERR', type(e).__name__, e)
try:
    cfg = json.loads(rd(roam/'config.json'))
    print('config keys', {k: cfg.get(k) for k in ('current_provider','work_mode','work_auto_continue','work_plan_approval_ask','language')})
except Exception as e: print('config ERR', type(e).__name__, e)
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for port in range(9300, 9800):
    pass

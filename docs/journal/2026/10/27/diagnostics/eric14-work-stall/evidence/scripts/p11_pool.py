# Read-only: pool_config, containers and assignments (non-secret columns). No writes.
import sys, sqlite3
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
con = sqlite3.connect('file:C:/ProgramData/aap-win/aap-win.db?mode=ro', uri=True)
for t in ('pool_config', 'containers', 'assignments'):
    cols = [r[1] for r in con.execute(f'pragma table_info("{t}")')]
    safe = [c for c in cols if not any(s in c.lower() for s in ('token', 'secret', 'password', 'hash', 'key', 'salt', 'credential'))]
    print('table', t, cols)
    for row in con.execute(f'select {", ".join(chr(34)+c+chr(34) for c in safe)} from "{t}" limit 30'):
        print('  ', dict(zip(safe, [str(v)[:90] for v in row])))

# Read-only: list aap-win.db tables and agent rows (non-secret columns only) via a read-only URI. No writes.
import sys, sqlite3
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
con = sqlite3.connect('file:C:/ProgramData/aap-win/aap-win.db?mode=ro', uri=True)
tabs = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
print('tables', tabs)
for t in tabs:
    if any(k in t.lower() for k in ('agent', 'sandbox', 'user', 'account')):
        cols = [r[1] for r in con.execute(f'pragma table_info("{t}")')]
        safe = [c for c in cols if not any(s in c.lower() for s in ('token', 'secret', 'password', 'hash', 'key', 'salt', 'credential'))]
        print('table', t, 'cols', cols)
        for row in con.execute(f'select {", ".join(chr(34)+c+chr(34) for c in safe)} from "{t}" limit 40'):
            print('  ', dict(zip(safe, [str(v)[:80] for v in row])))

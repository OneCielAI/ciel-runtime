"""Actual native plugin-origin independent reviewer dispatch fixture."""
from pathlib import Path
source=(Path(__file__).with_name('e2e_parallel.py')).read_text(encoding='utf-8')
source=source.replace("'max_active':1", "'max_active':2")
source=source.replace("elif n in (1,3):", "elif n == 1:")
source=source.replace("'max_active':1", "'max_active':2")
source=source.replace("elif n in (2,4):", "elif n == 2:")
source=source.replace("lease=next(iter(state['leases'].values()),None)", "lease=next((r for r in reversed(list(state['leases'].values())) if r['state'] == 'running'),None)")
source=source.replace("'hard_cap_rejection':False", "'automatic_reviewer':False")
source=source.replace("checks['hard_cap_rejection']=True", "checks['automatic_reviewer']=True")
source=source.replace("if row['args'].get('event')=='reserve' and row['result']['isError'] and 'cap' in json.dumps(row['result']).lower():checks['automatic_reviewer']=True", "if row['args'].get('event')=='reserve' and row['args'].get('role')=='reviewer' and not row['result']['isError']:checks['automatic_reviewer']=True")
source=source.replace("elif n==1 and lease:", "elif n==1 and lease:")
# Fork may issue its first request before the launch promise has been durably bound.
source=source.replace("state=work_parallel_mcp.ledger().read();v=state['version']", """state=work_parallel_mcp.ledger().read();v=state['version']
        if child:
            for _ in range(200):
                if any(r['state']=='running' for r in state['leases'].values()):break
                time.sleep(.01);state=work_parallel_mcp.ledger().read();v=state['version']""")
exec(compile(source,str(__file__),'exec'))

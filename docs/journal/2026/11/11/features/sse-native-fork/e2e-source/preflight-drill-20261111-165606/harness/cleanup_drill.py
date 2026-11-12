"""Preflight-only cleanup drill: no product launch, no Ciel, no Claude.

1. Synthetic-row checks of the ownership rules (child must be newer than its exact
   parent identity; a PID whose CreationDate changed is never treated as owned).
2. A real owned tree of sleeping python processes (root -> 2 children -> 1 grandchild
   each) plus a non-owned decoy that is NOT a descendant, killed by the v4 routine.
3. Proof: every owned identity gone, decoy alive, protected live tree unchanged,
   live profile hashes unchanged, no taskkill /T, full taskkill output logged.
"""
import sys
sys.dont_write_bytecode = True
import json, subprocess, time
from pathlib import Path

E2E = Path(__file__).resolve().parent
sys.path.insert(0, str(E2E))
import owned_process_control as opc
import importlib.util
DRILL_ARGS = list(sys.argv[1:])
spec = importlib.util.spec_from_file_location('driver', E2E / 'walkie_e2e_driver_v4.py')
driver = importlib.util.module_from_spec(spec); sys.argv = [sys.argv[0]]; spec.loader.exec_module(driver)



def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    report = {'started': time.time(), 'harness': driver.copy_harness(out)}
    # 1. rule checks on synthetic rows
    rows = [
        {'ProcessId': 100, 'ParentProcessId': 1, 'Name': 'root.exe', 'CreationDate': '/Date(1000000)/'},
        {'ProcessId': 200, 'ParentProcessId': 100, 'Name': 'child.exe', 'CreationDate': '/Date(1000500)/'},
        {'ProcessId': 300, 'ParentProcessId': 100, 'Name': 'older-foreign.exe', 'CreationDate': '/Date(999000)/'},
        {'ProcessId': 400, 'ParentProcessId': 200, 'Name': 'grandchild.exe', 'CreationDate': '/Date(1000900)/'},
    ]
    ledger, events = opc.discover_owned((100, '/Date(1000000)/'), rows)
    rule1 = sorted(i[0] for i in ledger) == [100, 200, 400] and any(e['event'] == 'reject_child_time' and e['identity'][0] == 300 for e in events)
    # PID 200 reused by a new process (same PID, new CreationDate) under a still-live parent 100:
    reused = rows[:1] + [{'ProcessId': 200, 'ParentProcessId': 100, 'Name': 'reused.exe', 'CreationDate': '/Date(1001500)/'}]
    ledger2, _ = opc.discover_owned((100, '/Date(1000000)/'), reused, ledger)
    rule2 = (200, '/Date(1000500)/') in ledger2 and opc.row_for((200, '/Date(1000500)/'), reused) is None
    # A child of a parent identity that is no longer present is never admitted:
    orphan_rows = [{'ProcessId': 500, 'ParentProcessId': 100, 'Name': 'adopted.exe', 'CreationDate': '/Date(1002000)/'},
                   {'ProcessId': 100, 'ParentProcessId': 1, 'Name': 'new-100.exe', 'CreationDate': '/Date(1001900)/'}]
    ledger3, _ = opc.discover_owned((100, '/Date(1000000)/'), orphan_rows, {(100, '/Date(1000000)/'): rows[0]})
    rule3 = not any(i[0] == 500 for i in ledger3)
    report['rule_checks'] = {'child_newer_than_parent': rule1, 'reused_pid_not_matched_by_identity': rule2, 'no_adoption_under_reused_parent_pid': rule3}

    # 2. real owned tree + decoy
    code_leaf = 'import time; time.sleep(300)'
    code_mid = ('import subprocess,sys,time; subprocess.Popen([sys.executable,"-B","-c",%r],creationflags=0x08000000); time.sleep(300)' % code_leaf)
    code_root = ('import subprocess,sys,time\n'
                 'for _ in range(2): subprocess.Popen([sys.executable,"-B","-c",%r],creationflags=0x08000000)\n'
                 'time.sleep(300)' % code_mid)
    profile_before = driver.live_profile_state(out / 'no-workspace')
    before = driver.ps_snapshot()
    driver.EXPECTED_PROTECTED = driver.long_lived_protected(before, time.time())
    root = subprocess.Popen([str(driver.PY311), '-B', '-c', code_root], creationflags=0x08000000)
    decoy = subprocess.Popen([str(driver.PY311), '-B', '-c', code_leaf], creationflags=0x08000000)
    time.sleep(3)
    rows = opc.snapshot_all()
    root_id = opc.identity(next(r for r in rows if int(r['ProcessId']) == root.pid))
    decoy_id = opc.identity(next(r for r in rows if int(r['ProcessId']) == decoy.pid))
    ledger, _ = opc.discover_owned(root_id, rows)
    report['owned_before_kill'] = [[i[0], i[1], ledger[i].get('Name')] for i in sorted(ledger)]
    report['decoy'] = list(decoy_id)
    cleanup = driver.kill_owned(root_id, out / 'cleanup.jsonl')
    time.sleep(1)
    after_rows = opc.snapshot_all()
    after = driver.ps_snapshot()
    report['cleanup'] = {k: cleanup[k] for k in ('owned_identities', 'killed', 'rejected', 'protected_intersections', 'final_live', 'completed')}
    report['decoy_alive_after'] = opc.row_for(decoy_id, after_rows) is not None
    report['owned_left'] = [list(i) for i in ledger if opc.row_for(i, after_rows) is not None]
    report['protected_unchanged'] = driver.protected_unchanged(before, after)
    report['protected_gate_size'] = len(driver.EXPECTED_PROTECTED)
    report['live_profile_unchanged'] = driver.live_profile_state(out / 'no-workspace') == profile_before
    log_rows = [json.loads(l) for l in (out / 'cleanup.jsonl').read_text(encoding='utf-8').splitlines()]
    kills = [r for r in log_rows if r.get('event') == 'taskkill']
    report['taskkill_calls'] = [{'identity': r['identity'], 'argv': r['argv'], 'rc': r['returncode'], 'stdout': r['stdout'].strip()} for r in kills]
    report['any_slash_T'] = any('/T' in r['argv'] for r in kills)
    report['leaf_first'] = all(kills[i]['identity'][0] != root_id[0] for i in range(len(kills) - 1)) and kills[-1]['identity'][0] == root_id[0]
    # own decoy cleanup by exact identity, same routine (decoy is its own root)
    decoy_cleanup = driver.kill_owned(decoy_id, out / 'cleanup-decoy.jsonl')
    report['decoy_cleanup_completed'] = decoy_cleanup['completed']
    report['passed'] = all(report['rule_checks'].values()) and cleanup['completed'] and report['decoy_alive_after'] and not report['owned_left'] \
        and report['protected_unchanged'] and report['live_profile_unchanged'] and not report['any_slash_T'] and report['leaf_first'] \
        and len(kills) == len(ledger) and decoy_cleanup['completed']
    report['finished'] = time.time()
    (out / 'drill-result.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('rule_checks', 'owned_before_kill', 'decoy_alive_after', 'owned_left', 'protected_unchanged', 'protected_gate_size',
                                              'live_profile_unchanged', 'any_slash_T', 'leaf_first', 'decoy_cleanup_completed', 'passed')}, indent=1))
    for k in report['taskkill_calls']: print(k)


if __name__ == '__main__':
    if '--child' not in DRILL_ARGS:
        # Same launch shape as start_detached.py: a detached child whose starter exits.
        child = subprocess.Popen([str(driver.PY311), '-B', __file__, DRILL_ARGS[0], '--child'], stdin=subprocess.DEVNULL,
                                 stdout=open(Path(DRILL_ARGS[0]).with_suffix('.log'), 'ab'), stderr=subprocess.STDOUT,
                                 creationflags=0x00000008 | 0x00000200, close_fds=True)
        print(child.pid)
    else:
        driver.assert_detached_from_protected()
        main(Path(DRILL_ARGS[0]))

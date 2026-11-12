"""Immutable snapshot of the current worktree bytes for the SSE true-fork E2E.

Usage: make_snapshot_v4.py <dest-dir> <report.json> --sse-freeze <dir> [--restart-freeze <dir>]

Same file set and checks as make_snapshot_v3.py, with the freezes given as arguments:
- copies the worktree minus .git, .local, docs and caches;
- refuses the snapshot when the source changed during the copy (shared worktree);
- verifies every SSE-freeze and restart-freeze line inside the snapshot; a restart-freeze
  file superseded by the SSE freeze at the same path is reported as superseded;
- verifies the raw-text inventory's product bound expressions inside the snapshot;
- records backend side-file hashes, marks every snapshot file read-only and records the
  whole-tree digest. The report lists the freezes it was verified against.
"""
import sys
sys.dont_write_bytecode = True
import argparse, hashlib, json, os, shutil, stat, time
from pathlib import Path

E2E = Path(__file__).resolve().parent
ROOT = E2E.parents[2]
sys.path.insert(0, str(E2E))
import raw_text_inventory as rti  # noqa: E402

SKIP_DIRS = {'.git', '.local', 'docs', '__pycache__', '.pytest_cache', '.ruff_cache', 'node_modules'}
MAP = {
    './hooks/': 'ciel_runtime_support/claude_mods/ciel-work/hooks/',
    'hooks/': 'ciel_runtime_support/claude_mods/ciel-work/hooks/',
    './types/': 'ciel_runtime_support/claude_mods/ciel-work/types/',
    'types/': 'ciel_runtime_support/claude_mods/ciel-work/types/',
    './tests/': 'tests/',
    'tests/': 'tests/',
    './pkg/': 'ciel_runtime_support/',
    'pkg/': 'ciel_runtime_support/',
}
BACKEND = ['side_wake.py', 'side_handoff.py', 'work_plan_store.py', 'side_intake_mcp.py', 'side_intake_processing.py',
           'side_work_decision.py', 'side_reply_outbox.py', 'side_reply_connector.py', 'work_mcp_tools.py',
           'work_http_controller.py', 'work_web_ui.py', 'runtime_paths.py', 'work_parallel_mcp.py']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files():
    out = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = Path(dirpath).relative_to(ROOT)
        if rel_dir == Path('.'):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        else:
            dirnames[:] = [d for d in dirnames if d not in ('__pycache__', '.pytest_cache', '.ruff_cache', 'node_modules')]
        for name in filenames:
            if name.endswith('.pyc'):
                continue
            rel = (rel_dir / name).as_posix()
            out[rel] = sha(ROOT / rel)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('dest', type=Path)
    parser.add_argument('report', type=Path)
    parser.add_argument('--sse-freeze', type=Path, required=True)
    parser.add_argument('--restart-freeze', type=Path, default=ROOT / '.local/tmp/freeze-09255642')
    parser.add_argument('--expected', action='append', default=[], metavar='PATH=SHA256',
                        help='extra exact snapshot file hash required (repeatable)')
    args = parser.parse_args()
    dest, report_path = args.dest.resolve(), args.report.resolve()
    freezes = [('sse', args.sse_freeze.resolve()), ('restart', args.restart_freeze.resolve())]
    if dest.exists():
        sys.exit('snapshot exists: ' + str(dest))
    started = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    before = source_files()
    dest.mkdir(parents=True)
    for rel in sorted(before):
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)
    after = source_files()
    changed_during_copy = sorted(r for r in set(before) | set(after) if before.get(r) != after.get(r))
    snap = {p.relative_to(dest).as_posix(): sha(p) for p in sorted(dest.rglob('*')) if p.is_file()}
    copy_mismatch = sorted(r for r in set(before) | set(snap) if before.get(r) != snap.get(r))
    freeze_rows, sse_paths = [], {}
    for label, folder in freezes:
        for line in (folder / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            digest, rel = line.split(' *', 1)
            prefix = next((p for p in MAP if rel.startswith(p)), None)
            if prefix is None:
                freeze_rows.append({'freeze': label, 'entry': rel, 'path': None, 'status': 'not_runtime (journal)'})
                continue
            path = MAP[prefix] + rel[len(prefix):]
            if label == 'sse':
                sse_paths[path] = digest
            got = snap.get(path)
            if got == digest:
                status = 'match'
            elif label == 'restart' and path in sse_paths and sse_paths[path] == got:
                status = 'superseded_by_sse_freeze'
            else:
                status = 'MISMATCH'
            freeze_rows.append({'freeze': label, 'entry': rel, 'path': path, 'expected': digest, 'snapshot': got, 'status': status})
    bounds = rti.verify_product_bounds(dest)
    expected_rows = []
    for item in args.expected:
        rel, _, digest = item.partition('=')
        expected_rows.append({'path': rel, 'expected': digest, 'snapshot': snap.get(rel), 'match': snap.get(rel) == digest})
    for rel in snap:
        os.chmod(dest / rel, stat.S_IREAD)
    digest = hashlib.sha256(json.dumps(snap, sort_keys=True).encode()).hexdigest()
    report = {
        'started': started, 'finished': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'python': sys.version.split()[0],
        'source': str(ROOT), 'snapshot': str(dest), 'files': len(snap), 'snapshot_digest': digest,
        'freezes': [[label, str(folder), sha(folder / 'SHA256SUMS')] for label, folder in freezes],
        'changed_during_copy': changed_during_copy, 'copy_mismatch': copy_mismatch,
        'freeze_rows': freeze_rows,
        'freeze_mismatch': [r for r in freeze_rows if r['status'] == 'MISMATCH'],
        'product_bounds': bounds,
        'expected_files': expected_rows,
        'expected_mismatch': [r for r in expected_rows if not r['match']],
        'backend_hashes': {b: snap.get('ciel_runtime_support/' + b) for b in BACKEND},
        'head': (ROOT / '.git').exists() and os.popen('git -C "%s" rev-parse HEAD' % ROOT).read().strip(),
    }
    (dest.parent / (dest.name + '-manifest.json')).write_text(json.dumps(snap, indent=0), encoding='utf-8')
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=1), encoding='utf-8')
    (dest.parent / (dest.name + '-verify.json')).write_text(json.dumps(report, indent=1), encoding='utf-8')
    brief = {k: v for k, v in report.items() if k not in ('freeze_rows', 'backend_hashes')}
    brief['freeze_status'] = [(r['freeze'], r['entry'], r['status']) for r in freeze_rows]
    print(json.dumps(brief, indent=1))
    return 0 if (not changed_during_copy and not copy_mismatch and not report['freeze_mismatch'] and bounds['passed']
                 and not report['expected_mismatch']) else 1


if __name__ == '__main__':
    sys.exit(main())

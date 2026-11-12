"""Offline tests for walkie_e2e_driver_installed.py (no process is launched).

The installed fixture is built in a temp dir from `git archive 57dbfb3` when the
INSTALLED_FIXTURE environment variable names such an extract; otherwise a minimal
synthetic tree is used for the shape/copy/ownership rules and the key-pin test that
needs the real committed bytes is skipped.
Run: py -3.11 -B -m unittest test_installed_driver -v
"""
import sys
sys.dont_write_bytecode = True
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load():
    spec = importlib.util.spec_from_file_location('installed_driver', HERE / 'walkie_e2e_driver_installed.py')
    module = importlib.util.module_from_spec(spec)
    argv = sys.argv
    try:
        sys.argv = [argv[0]]
        spec.loader.exec_module(module)
    finally:
        sys.argv = argv
    return module


D = load()
FIXTURE = os.environ.get('INSTALLED_FIXTURE')


def synthetic_tree(root):
    files = {
        'ciel_runtime.py': 'print("runtime")\n',
        'ciel-runtime-tool-guard.py': '# guard\n',
        'package.json': '{"name": "x"}\n',
        'ciel_runtime_support/runtime_paths.py': '# paths\n',
        'ciel_runtime_support/claude_mods/ciel-work/hooks/register.js': '// register\n',
        'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js': '// side\n',
    }
    for rel, text in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())
    return files


class InstalledDriverTest(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix='installed-test-')
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
        self.src = self.tmp / 'installed'
        synthetic_tree(self.src)

    def test_v4_driver_is_untouched(self):
        import hashlib
        self.assertEqual(hashlib.sha256((HERE / 'walkie_e2e_driver_v4.py').read_bytes()).hexdigest(),
                         '0f8a977fb3f9e75542ce1dcf01e0900fca0857619c33b8ead338aafa9a3e9d7b')

    def test_pins_are_the_committed_lf_hashes(self):
        self.assertEqual(D.COMMIT, '57dbfb3ac9c8d9f1a42e89a038d27e16f4fab409')
        self.assertEqual(D.EXPECTED_PRODUCT, {
            'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js': 'dcf8fbbcf33cfeadd9e87890f2c62b790400e4aaca6105138ed55db03f7cac32',
            'ciel_runtime_support/claude_mods/ciel-work/hooks/register.js': 'ea7768f5348e61883cfe6833420abf0e98e1e001576c5868fb1b4005d526271a',
            'ciel_runtime_support/claude_mods/ciel-work/types/index.d.ts': '4110db606785f32159b68bf2e77fdfb0e35ee46533853caab5917e9ce048f314',
            'ciel_runtime_support/work_parallel_mcp.py': 'ccf397c62c084241253723db5a9bbba09240bec09e2dba37ca14de83ebb7c03f',
            'ciel_runtime_support/work_parallel_finalization.py': '79a573b67ac69a2649df9960841fbd026876ad7684d2584d6c9d63ba8000664d',
        })

    def test_copy_is_byte_identical_and_installed_untouched(self):
        files, _ = D.hash_installed_tree(self.src)
        before = {p: (self.src / p).stat().st_mtime_ns for p in files}
        report = D.make_run_copy(self.src, self.tmp / 'copy', files)
        self.assertTrue(report['passed'], report)
        self.assertEqual(report['digest'], report['expected_digest'])
        self.assertEqual({p: (self.src / p).stat().st_mtime_ns for p in files}, before)
        self.assertEqual(D.hash_installed_tree(self.src)[0], files)

    def test_copy_detects_installed_change_after_preflight(self):
        files, _ = D.hash_installed_tree(self.src)
        (self.src / 'ciel_runtime.py').write_bytes(b'print("changed")\n')
        report = D.make_run_copy(self.src, self.tmp / 'copy', files)
        self.assertFalse(report['passed'])
        self.assertIn('ciel_runtime.py', report['installed_changed_since_preflight'])
        self.assertIn('ciel_runtime.py', report['changed'])

    def test_copy_refuses_existing_destination(self):
        files, _ = D.hash_installed_tree(self.src)
        (self.tmp / 'copy').mkdir()
        with self.assertRaises(SystemExit):
            D.make_run_copy(self.src, self.tmp / 'copy', files)

    def test_bytecode_in_installed_tree_is_reported_not_copied(self):
        cache = self.src / 'ciel_runtime_support' / '__pycache__'
        cache.mkdir()
        (cache / 'runtime_paths.cpython-314.pyc').write_bytes(b'\x00bytecode')
        files, bytecode = D.hash_installed_tree(self.src)
        self.assertNotIn('ciel_runtime_support/__pycache__/runtime_paths.cpython-314.pyc', files)
        self.assertEqual(bytecode, ['ciel_runtime_support/__pycache__/runtime_paths.cpython-314.pyc'])
        report = D.make_run_copy(self.src, self.tmp / 'copy', files)
        self.assertTrue(report['passed'])
        self.assertFalse((self.tmp / 'copy' / 'ciel_runtime_support' / '__pycache__').exists())

    def test_shape_rejects_repo_only_paths_and_missing_runtime(self):
        self.assertTrue(D.installed_shape_check(self.src)['passed'])
        (self.src / 'tests').mkdir()
        self.assertIn('repo-only path present: tests', D.installed_shape_check(self.src)['problems'])
        shutil.rmtree(self.src / 'tests')
        (self.src / 'ciel_runtime.py').unlink()
        self.assertIn('missing ciel_runtime.py', D.installed_shape_check(self.src)['problems'])

    def test_preflight_refuses_worktree_and_wrong_pins(self):
        report, problems = D.verify_installed(D.ROOT)
        self.assertTrue(any('inside the development worktree' in p for p in problems))
        report, problems = D.verify_installed(self.src)
        self.assertTrue(any(p.startswith('key pin ') for p in problems))
        self.assertEqual(len(report['key_pins']['mismatch']), len(D.EXPECTED_PRODUCT))

    def test_key_pin_uses_raw_bytes_no_normalization(self):
        rel = 'ciel_runtime_support/claude_mods/ciel-work/hooks/register.js'
        path = self.tmp / 'pin' / rel
        path.parent.mkdir(parents=True)
        path.write_bytes(b'// register\n')
        lf = D.sha(path)
        path.write_bytes(b'// register\r\n')
        self.assertNotEqual(D.sha(path), lf)
        row = next(r for r in D.key_pin_check(self.tmp / 'pin')['rows'] if r['path'] == rel)
        self.assertEqual(row['actual'], D.sha(path))

    def test_owned_markers_never_include_the_installed_path(self):
        # The leftover scan and owned markers are built from scratch, the run copy and the
        # output folder only. Static check on the driver source.
        source = (HERE / 'walkie_e2e_driver_installed.py').read_text(encoding='utf-8')
        start = source.index("leftover = [r for r in ps_snapshot()")
        line = source[start:source.index('\n', start)]
        self.assertIn('str(scratch)', line)
        self.assertIn('str(home)', line)
        self.assertNotIn('INSTALLED', line)
        self.assertNotIn('SNAP', line)
        for marker in ("marks = [str(scratch), str(home), str(out)]",
                       "other_e2e_processes(after, [str(out), str(scratch), str(home)])"):
            self.assertIn(marker, source)
        self.assertNotIn('kill_owned(INSTALLED', source)

    def test_snap_is_the_run_copy_before_any_product_import(self):
        source = (HERE / 'walkie_e2e_driver_installed.py').read_text(encoding='utf-8')
        body = source[source.index('def run(kind, output):'):]
        self.assertLess(body.index('SNAP = home'), body.index('seed_board(state, identity)'))
        self.assertLess(body.index('SNAP = home'), body.index('setup(config, claude, workspace'))
        self.assertLess(body.index('make_run_copy(INSTALLED, home'), body.index('SNAP = home'))

    def test_engine_written_types_absent_from_installed_are_allowed(self):
        base = 'ciel_runtime_support/claude_mods/ciel-work/.claude-plugin/'
        manifest = {'ciel_runtime.py': 'a', base + 'plugin.json': 'p'}
        copy_now = dict(manifest)
        engine = [base + 'types/.gitignore', base + 'types/tsconfig.json', base + 'types/claude-code/index.d.ts',
                  base + 'types/claude-code-mcp/index.d.ts', base + 'types/claude-code-tools/index.d.ts']
        for rel in engine:
            copy_now[rel] = 'e'
        report = D.run_copy_after_report(copy_now, manifest, self.tmp)
        self.assertEqual(report['unexpected'], [])
        self.assertEqual(report['engine_written'], sorted(engine))
        self.assertEqual(report['changed'], [])

    def test_changed_installed_file_under_types_still_fails(self):
        rel = 'ciel_runtime_support/claude_mods/ciel-work/.claude-plugin/types/claude-code-tools/index.d.ts'
        manifest = {'ciel_runtime.py': 'a', rel: 'shipped'}
        report = D.run_copy_after_report({'ciel_runtime.py': 'a', rel: 'rewritten'}, manifest, self.tmp)
        self.assertEqual(report['changed'], [rel])
        self.assertEqual(report['unexpected'], [rel])
        deleted = D.run_copy_after_report({'ciel_runtime.py': 'a'}, manifest, self.tmp)
        self.assertEqual(deleted['unexpected'], [rel])

    def test_other_extras_and_changes_fail(self):
        manifest = {'ciel_runtime.py': 'a', 'ciel_runtime_support/side_wake.py': 's'}
        report = D.run_copy_after_report({'ciel_runtime.py': 'b', 'ciel_runtime_support/side_wake.py': 's',
                                          'ciel_runtime_support/new.py': 'n'}, manifest, self.tmp)
        self.assertEqual(report['unexpected'], ['ciel_runtime.py', 'ciel_runtime_support/new.py'])
        pyc = D.run_copy_after_report({**manifest, 'ciel_runtime_support/__pycache__/side_wake.cpython-314.pyc': 'x'}, manifest, self.tmp)
        self.assertEqual(pyc['unexpected'], [])
        self.assertEqual(pyc['classified'], {'ciel_runtime_support/__pycache__/side_wake.cpython-314.pyc': 'bytecode'})

    def test_installed_bytecode_does_not_change_the_manifest(self):
        files_before, _ = D.hash_installed_tree(self.src)
        cache = self.src / 'ciel_runtime_support' / '__pycache__'
        cache.mkdir()
        for n in range(3):
            (cache / f'm{n}.cpython-314.pyc').write_bytes(b'x' * n)
        files_after, bytecode = D.hash_installed_tree(self.src)
        self.assertEqual(files_after, files_before)
        self.assertEqual(len(bytecode), 3)
        self.assertEqual(D.tree_digest(files_after), D.tree_digest(files_before))

    @unittest.skipUnless(FIXTURE, 'INSTALLED_FIXTURE (a git archive 57dbfb3 extract) not set')
    def test_key_hash_gate_passes_on_committed_archive(self):
        rows = D.key_pin_check(Path(FIXTURE))['rows']
        self.assertEqual([r for r in rows if not r['match']], [], json.dumps(rows, indent=1))


if __name__ == '__main__':
    unittest.main(verbosity=2)

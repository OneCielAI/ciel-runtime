import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / 'ciel_runtime_support/claude_mods/ciel-work/hooks'


@unittest.skipUnless(shutil.which('node'), 'node required')
class WorkReadReconnectTests(unittest.TestCase):
    def test_transport_failure_recovers_reads_only_in_same_session(self):
        script = '''
import assert from 'node:assert/strict'
import { readWorkMcp } from './side-intake.js'
const args = { session_id: 'claude:a' }
const ok = { content: [{ type:'text', text:'{}' }] }
for (const error of ['connection closed before the tool call was answered', 'The operation timed out.', 'ECONNREFUSED']) {
  let calls = 0, connects = 0
  const $ = { session:{id:async()=> 'a'}, mcp:{
    call:async()=> { if (++calls===1) throw new Error(error); return ok },
    connect:async()=> { connects++; return {isConnected:true,server:'ciel-runtime-router'} }
  }}
  assert.equal(await readWorkMcp($,'work_parallel_status',args),ok)
  assert.deepEqual([calls,connects],[2,1])
}
let connects = 0, calls = 0, session = 'a'
const $ = {session:{id:async()=>session},mcp:{
  call:async()=> {calls++; throw new Error('MCP server disconnected')},
  connect:async()=> {connects++; session='b'; return {isConnected:true,server:'ciel-runtime-router'}}
}}
await assert.rejects(readWorkMcp($,'work_parallel_item_review',args))
assert.deepEqual([calls,connects],[0,0])
await assert.rejects(readWorkMcp($,'work_host_page',args))
assert.deepEqual([calls,connects],[1,1])
'''
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            for name in ('side-intake.js', 'texts.generated.js'):
                shutil.copyfile(HOOKS / name, path / name)
            (path / 'package.json').write_text('{"type":"module"}', encoding='ascii')
            (path / 'test.mjs').write_text(script, encoding='utf-8')
            run = subprocess.run(['node', str(path / 'test.mjs')], capture_output=True, text=True, timeout=20)
            self.assertEqual(0, run.returncode, run.stderr)


if __name__ == '__main__':
    unittest.main()

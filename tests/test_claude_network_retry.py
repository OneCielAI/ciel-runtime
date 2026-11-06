import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from ciel_runtime_support.claude_network_retry import MOD_DIR, launch_args


class NetworkRetryTests(unittest.TestCase):
    def test_direct_network_mod_is_independent_of_work(self):
        env = {'CIEL_RUNTIME_DIRECT_GATEWAY_ACTIVE': '1', 'CIEL_RUNTIME_DIRECT_EMPTY_STREAM_RETRIES': '3'}
        self.assertEqual(['--plugin-dir', str(MOD_DIR)], launch_args('cielairouter', env))
        self.assertEqual([], launch_args('anthropic', env))
        self.assertEqual([], launch_args('cielairouter', env, native=True))
        self.assertEqual([], launch_args('cielairouter', {**env, 'CIEL_RUNTIME_DIRECT_GATEWAY_ACTIVE': '0'}))
        self.assertEqual([], launch_args('cielairouter', {}))
        self.assertEqual([], launch_args('cielairouter', {**env, 'CIEL_RUNTIME_DIRECT_EMPTY_STREAM_RETRIES': '0'}))

    @unittest.skipUnless(shutil.which('node'), 'node required')
    def test_empty_only_retries_and_never_replays_model_chunks(self):
        script = r'''
import { register } from './register.mjs';
let hook; register((name, fn) => { hook = fn });
const out=[];
for (const mode of ['empty','text','tool','thinking','input','stop','usage','unknown','cancelled','disabled','persistent','new-input','stale','denied','denied-after','answer','result-tool','stop-reason']) {
 let opens=0; const logs=[];
 const error={role:'assistant',text:mode==='unknown'?'API Error: authentication failed':'API Error: Stream ended without receiving any events',toolUses:[]};
 const $={env:{get:async key=>key.endsWith('ACTIVE')?'1':mode==='disabled'?'0':'2'},
 session:{messages:async()=>mode==='denied'||(mode==='denied-after'&&opens)?{deny:'unavailable'}:mode==='stale'?[error]:[
   ...Array.from({length:opens},()=>error),
   ...(mode==='new-input'&&opens?[{role:'user',text:'new request',toolUses:[]}]:[])]},
 ui:{log:text=>logs.push(text)}};
 const next=()=> (async function*(){ opens++;
   if(mode==='text')yield {kind:'text',text:'visible'};
   else if(mode==='tool')yield {kind:'tool',input:'x'};
   else if(['thinking','input','stop'].includes(mode))yield {kind:mode};
   else yield {kind:'engine',ref:1};
   return {turnId:'t',index:0,answer:mode==='answer'?'visible':opens>1&&mode!=='persistent'?'ok':'',toolUses:mode==='result-tool'?[{name:'test'}]:[],stopReason:mode==='stop-reason'?'end_turn':opens>1&&mode!=='persistent'?'end_turn':null,usage:mode==='usage'?{}:null}; })();
 next.signal={aborted:mode==='cancelled'};
 const gen=hook($,{turnId:'t',index:0},next); let r;
 while(true){r=await gen.next();if(r.done)break;}
 out.push({mode,opens,answer:r.value.answer,logs:logs.length});
}
console.log(JSON.stringify(out));
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(MOD_DIR/'hooks/register.js', root/'register.mjs')
            (root/'test.mjs').write_text(script, encoding='utf8')
            result = subprocess.run(['node', str(root/'test.mjs')], capture_output=True, text=True, check=True)
        rows = {row['mode']: row for row in json.loads(result.stdout)}
        self.assertEqual(2, rows['empty']['opens'])
        self.assertEqual('ok', rows['empty']['answer'])
        self.assertEqual(3, rows['persistent']['opens'])
        for mode in ('text', 'tool', 'thinking', 'input', 'stop', 'usage', 'unknown',
                     'cancelled', 'disabled', 'new-input', 'stale', 'denied', 'denied-after',
                     'answer', 'result-tool', 'stop-reason'):
            self.assertEqual(1, rows[mode]['opens'], mode)
            self.assertEqual(0, rows[mode]['logs'], mode)

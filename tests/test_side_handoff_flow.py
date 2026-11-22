import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_plan_store import session_work_plan_store
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox


class SideHandoffFlowTests(unittest.TestCase):
    @unittest.skip('retired 2026-11-18 (user): the side no longer hands off to main; only ask_main reaches main')
    def test_waiting_item_wake_is_reoffered_until_main_acknowledges(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            identity='claude:test'
            config={'work_mode':True,'work_side_intake':{'enabled':True,'context_mode':'isolated'},'work_parallel_host':{'token':'x'*40}}
            store=session_work_plan_store(root,identity)
            _,goal=store.transaction(lambda d:tree.add_node(d,None,'P1 fix'))
            _,gate=store.transaction(lambda d:tree.add_node(d,goal,'design approval',waiting_on='approval 118269'))
            SideIntakeInbox(root,identity).receive('118269 approved','approval')
            def call(name,args,extra=None):
                result=dispatch_work_tool(1,name,{**args,'session_id':identity,'_host':{'token':'x'*40,**(extra or {})}},
                    load_config=lambda:config,state_dir=root,respond=lambda _id,text,error=False:{'text':text,'error':error})
                self.assertFalse(result['error'],result)
                return json.loads(result['text'])
            claim=call('work_side_claim',{'owner':'actor'})['claim']
            decision=call('work_side_resolve',{'decision':{'action':'update','item_id':gate,'summary':'approval received',
                'reply':'main resume pending','note':'118269 approved'},'expected_version':store.read()['version']},
                {'receipt_id':claim['receipt_id'],'generation':claim['generation']})
            self.assertEqual('requested',decision['result']['handoff'])
            for _ in range(2):
                wakes=call('work_side_claim',{'owner':'actor'})['main_wakes']
                self.assertEqual([claim['receipt_id']],[w['event_id'] for w in wakes])
                self.assertEqual([{'id':gate,'waiting_on':'approval 118269'}],wakes[0]['waits'])
            prepared=call('work_side_wake',{'event_id':claim['receipt_id'],'state':'prepare'})
            call('work_side_wake',{'event_id':claim['receipt_id'],'state':'queued','attempt_id':prepared['attempt_id']})
            # A queued prompt survives plugin reload without a duplicate wake.
            self.assertEqual([],call('work_side_claim',{'owner':'restarted-actor'})['main_wakes'])
            status=call('work_side_status',{})
            self.assertEqual([('resume','queued')],[(h['kind'],h['wake_state']) for h in status['main_handoffs']])
            self.assertGreaterEqual(status['main_handoffs'][0]['wake_age_seconds'],0)
            self.assertNotIn('118269 approved',json.dumps(status['main_handoffs']))
            call('work_side_handoff',{'event_id':claim['receipt_id'],'answer':'Gate cleared; resuming at next boundary',
                                      'clear_wait':[gate],'expected_version':store.read()['version']})
            # A resume/P1 acknowledgment is durable board history, not another
            # external event for side to process and reply to a second time.
            followup=call('work_side_claim',{'owner':'actor'})
            self.assertEqual([],followup['main_wakes'])
            self.assertIsNone(followup['claim'])
            self.assertEqual([],SideIntakeInbox(root,identity).pending())
            self.assertEqual([],call('work_side_status',{})['main_handoffs'])
            self.assertEqual('',next(n for n in store.read()['nodes'] if n['id']==gate)['waiting_on'])

    def test_concurrent_wake_prepares_authorize_exactly_one_attempt(self):
        # E2E run-a-20261111-105350: two concurrent prepares both received the
        # same submitting attempt and both submitted the wake prompt.
        import threading
        from ciel_runtime_support import side_wake
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            identity='claude:test'
            config={'work_mode':True,'work_side_intake':{'enabled':True,'context_mode':'isolated'},'work_parallel_host':{'token':'x'*40}}
            store=session_work_plan_store(root,identity)
            _,item=store.transaction(lambda d:tree.add_node(d,None,'report'))
            SideIntakeInbox(root,identity).receive('deadline within 4 hours','deadline')
            def call(name,args,extra=None,error=False):
                result=dispatch_work_tool(1,name,{**args,'session_id':identity,'_host':{'token':'x'*40,**(extra or {})}},
                    load_config=lambda:config,state_dir=root,respond=lambda _id,text,error=False:{'text':text,'error':error})
                self.assertEqual(error,result['error'],result)
                return result['text'] if error else json.loads(result['text'])
            claim=call('work_side_claim',{'owner':'actor'})['claim']
            event=claim['receipt_id']
            decision=call('work_side_resolve',{'decision':{'action':'advise','item_id':item,'summary':'Need input','reply':'Which?'},
                'expected_version':store.read()['version']},{'receipt_id':claim['receipt_id'],'generation':claim['generation']})
            self.assertEqual('requested',decision['result']['handoff'])
            # Two independent stores (as two MCP calls build them) both pause
            # just before the durable transaction, then race into it.
            barrier=threading.Barrier(2,timeout=10)
            class Paused:
                def __init__(self):
                    self.inner=session_work_plan_store(root,identity)
                def transaction(self,mutate,**options):
                    barrier.wait()
                    return self.inner.transaction(mutate,**options)
            results=[None,None]
            def prepare(index):
                results[index]=side_wake.prepare(Paused(),event,protocol=2,
                    owner_nonce='owner-'+str(index),channel='prompt')
            threads=[threading.Thread(target=prepare,args=(index,)) for index in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(20)
            # The first intent is leased: the other prepare gets already_intent
            # with no attempt, so it can neither start nor record.
            self.assertEqual(['already_intent','submitting'],sorted(result['state'] for result in results))
            winner=next(result for result in results if result['state']=='submitting')
            loser_index=results.index(next(result for result in results if result['state']=='already_intent'))
            self.assertNotIn('attempt_id',results[loser_index])
            with self.assertRaisesRegex(tree.WorkPlanError,'stale side wake intent'):
                side_wake.start(store,event,winner['attempt_id'],'owner-'+str(loser_index),'prompt')
            started=side_wake.start(store,event,winner['attempt_id'],winner['owner_nonce'],'prompt')
            self.assertEqual('started',started['phase'])
            # Only the owner of the started attempt records an outcome.
            with self.assertRaisesRegex(tree.WorkPlanError,'stale side wake attempt'):
                side_wake.record(store,event,'queued',winner['attempt_id'],owner_nonce='wrong')
            side_wake.record(store,event,'failed',winner['attempt_id'],owner_nonce=winner['owner_nonce'])
            # A failed wake is offered again and the next prepare gets a new intent.
            self.assertEqual([event],[w['event_id'] for w in call('work_side_claim',{'owner':'actor'})['main_wakes']])
            retry=side_wake.prepare(store,event,protocol=2,owner_nonce='retry-owner',channel='prompt')
            self.assertEqual(('submitting','intent'),(retry['state'],retry['phase']))
            self.assertNotEqual(winner['attempt_id'],retry['attempt_id'])
            side_wake.start(store,event,retry['attempt_id'],'retry-owner','prompt')
            side_wake.record(store,event,'queued',retry['attempt_id'],owner_nonce='retry-owner')
            self.assertEqual('queued',store.read()['side_handoffs'][event]['wake']['state'])

    def test_settled_handoffs_do_not_inflate_every_claim(self):
        # Prody 2026-11-10: 30 answered handoffs were returned (with every
        # original text) on each poll; an empty claim became 361K characters in
        # 46 host pages and claims ran every ~30 s instead of every 2 s.
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            identity='claude:test'
            config={'work_mode':True,'work_side_intake':{'enabled':True,'context_mode':'isolated'},'work_parallel_host':{'token':'x'*40}}
            store=session_work_plan_store(root,identity)
            _,item=store.transaction(lambda d:tree.add_node(d,None,'existing'))
            inbox=SideIntakeInbox(root,identity)
            def call(name,args,extra=None,raw_text=False):
                result=dispatch_work_tool(1,name,{**args,'session_id':identity,'_host':{'token':'x'*40,**(extra or {})}},
                    load_config=lambda:config,state_dir=root,respond=lambda _id,text,error=False:{'text':text,'error':error})
                self.assertFalse(result['error'],result)
                if raw_text:
                    return result['text']
                value=json.loads(result['text'])
                if isinstance(value,dict) and 'paged' in value:
                    pages=[call('work_host_page',{'id':value['paged']['id'],'part':part})['text']
                           for part in range(value['paged']['parts'])]
                    value=json.loads(''.join(pages))
                return value
            for index in range(5):
                inbox.receive('Need a decision '+str(index)+' '+'x'*20000,'event-'+str(index))
                claim=call('work_side_claim',{'owner':'actor'})['claim']
                if claim['event_key'].startswith('handoff:'):
                    self.fail('a follow-up was claimed before its question')
                decision=call('work_side_resolve',{'decision':{'action':'advise','item_id':item,'summary':'Need input','reply':'Which?'},
                    'expected_version':store.read()['version']},{'receipt_id':claim['receipt_id'],'generation':claim['generation']})
                call('work_side_handoff',{'event_id':claim['receipt_id'],'answer':'Answer '+str(index),'activate':False,
                                          'expected_version':decision['result']['version']})
                # The follow-up relay is ordinary side mail; settle it so the next
                # question is the only pending event.
                relay=call('work_side_claim',{'owner':'actor'})['claim']
                self.assertTrue(relay['event_key'].startswith('handoff:'))
                call('work_side_resolve',{'decision':{'action':'answer','summary':'relayed','reply':'ok'},
                    'expected_version':store.read()['version']},{'receipt_id':relay['receipt_id'],'generation':relay['generation']})
            self.assertEqual(5,sum(1 for row in store.read()['side_handoffs'].values() if row['state']=='answered'))
            text=call('work_side_claim',{'owner':'actor'},raw_text=True)
            self.assertNotIn('"paged"',text)
            self.assertLess(len(text),8000)
            self.assertEqual([],json.loads(text)['handoff_replies'])

    def test_coordinator_answer_is_durable_side_followup_not_new_work(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            identity='claude:test'
            config={'work_mode':True,'work_side_intake':{'enabled':True,'context_mode':'isolated'},'work_parallel_host':{'token':'x'*40}}
            store=session_work_plan_store(root,identity)
            _,item=store.transaction(lambda d:tree.add_node(d,None,'existing'))
            inbox=SideIntakeInbox(root,identity)
            inbox.receive('Need a main decision','event',route={'channel':'source','thread_id':'t'})
            def call(name,args,extra=None):
                result=dispatch_work_tool(1,name,{**args,'session_id':identity,'_host':{'token':'x'*40,**(extra or {})}},
                    load_config=lambda:config,state_dir=root,respond=lambda _id,text,error=False:{'text':text,'error':error})
                self.assertFalse(result['error'],result)
                return json.loads(result['text'])
            claim=call('work_side_claim',{'owner':'actor'})['claim']
            decision=call('work_side_resolve',{'decision':{'action':'advise','item_id':item,'summary':'Need coordinator input','reply':'Which target?'},'expected_version':store.read()['version']},
                {'receipt_id':claim['receipt_id'],'generation':claim['generation']})
            answered=call('work_side_handoff',{'event_id':claim['receipt_id'],'answer':'Use target A','activate':False,'expected_version':decision['result']['version']})
            self.assertEqual('answered',answered['state'])
            self.assertEqual('answered',inbox._load(claim['receipt_id'])['result']['handoff'])
            pending=inbox.pending()
            self.assertEqual(1,len(pending))
            self.assertIn('Use target A',pending[0]['text'])
            self.assertEqual(1,len(store.read()['nodes']))
            call('work_side_handoff',{'event_id':claim['receipt_id'],'answer':'Use target A','activate':False,'expected_version':answered['version']})
            self.assertEqual(1,len(inbox.pending()))
            # Simulate restart after board acknowledgment but before follow-up
            # mailbox persistence; the next host pickup repairs the intent.
            inbox._event_path('pending', pending[0]['receipt_id']).unlink()
            prior = inbox._load(claim['receipt_id'])
            prior['result'].update(handoff='requested', delivery='submitted')
            inbox._save_file(inbox._event_path('receipts', claim['receipt_id']), prior)
            pickup = call('work_side_claim', {'owner': 'restarted-actor'})
            recovered = pickup['claim']
            displayed = pickup['handoff_replies'][0]['receipt']['result']
            self.assertEqual('answered', displayed['handoff'])
            self.assertEqual('submitted', displayed['delivery'])
            self.assertEqual('answered', inbox._load(claim['receipt_id'])['result']['handoff'])
            self.assertEqual(pending[0]['receipt_id'], recovered['receipt_id'])
            self.assertIn('Use target A', recovered['text'])
            self.assertEqual(1, len(store.read()['nodes']))

    def test_handoff_accepts_unique_12char_prefix_and_rejects_non_handoff_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            token = 'x' * 40
            session = 'claude:prefix-test'
            config = {'work_mode': True, 'work_parallel_host': {'token': token},
                      'work_side_intake': {'enabled': True, 'type': 'btw_with_mcp', 'context_mode': 'isolated'}}
            store = session_work_plan_store(state, session)

            def call(name, args, expect_error=False):
                payload = {**args, 'session_id': session}
                if '_host' not in payload:
                    payload['_host'] = {'token': token}
                out = dispatch_work_tool(
                    1, name, payload, load_config=lambda: config, state_dir=state,
                    respond=lambda _id, text, is_error=False: {'text': text, 'isError': is_error},
                )
                if expect_error:
                    self.assertTrue(out['isError'], out)
                    return out['text']
                self.assertFalse(out['isError'], out)
                return json.loads(out['text'])

            # 1) Calling work_side_handoff with a 12-char id that has no handoff row (e.g. ask_main notice)
            # must return 'side handoff not found', not the misleading 'invalid side receipt identity'.
            err = call('work_side_handoff', {'event_id': 'd785d257b73f', 'answer': 'already approved',
                                             'expected_version': store.read()['version']}, expect_error=True)
            self.assertIn('side handoff not found', err)
            self.assertNotIn('invalid side receipt identity', err)

            # 2) With a real requested handoff row, the unique 12-char wake prefix resolves and succeeds.
            inbox = SideIntakeInbox(state, session)
            inbox.receive('Need main decision on target B', 'walkie:prefix-1',
                          route={'source_kind': 'external_event', 'channel': 'walkie', 'thread_id': 't-prefix'})
            claim = call('work_side_claim', {'owner': 'actor-prefix'})['claim']
            full_id = claim['receipt_id']
            call('work_side_resolve', {
                'expected_version': store.read()['version'],
                'decision': {'action': 'answer', 'kind': 'info', 'summary': 'Needs target choice', 'reply': 'Checking with main.'},
                '_host': {'token': token, 'receipt_id': full_id, 'generation': claim['generation']},
            })
            # Seed a requested handoff row (as legacy/explicit handoff flows do) to test 12-char prefix resolution.
            def seed_handoff(doc):
                doc.setdefault('side_handoffs', {})[full_id] = {
                    'state': 'requested', 'item_id': None, 'summary': 'Needs target choice',
                    'reply': 'Checking with main.', 'gates': [], 'wake': {'state': 'appended', 'revision': 1},
                }
            doc_after, _ = store.transaction(seed_handoff)
            answered = call('work_side_handoff', {
                'event_id': full_id[:12],
                'answer': 'Use target B',
                'activate': False,
                'expected_version': doc_after['version'],
            })
            self.assertEqual(full_id, answered['event_id'])
            self.assertEqual('answered', answered['state'])
            self.assertEqual('answered', inbox._load(full_id)['result']['handoff'])

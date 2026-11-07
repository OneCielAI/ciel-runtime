import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim, finish
from ciel_runtime_support.side_reply_outbox import prepare, record, pending


class SideReplyTests(unittest.TestCase):
    def test_correlated_route_is_host_only_and_submitted_once(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            inbox=SideIntakeInbox(root,'claude:test')
            row=inbox.receive('question','key',route={'channel':'web','thread_id':'thread','reply_token':'private','source_kind':'web_chat'})
            leased=claim(root,'claude:test','actor')
            self.assertNotIn('reply_token',leased['route'])
            finish(root,'claude:test',row['receipt_id'],leased['generation'],{'reply':'answer','delivery':'pending'})
            self.assertEqual(row['receipt_id'],pending(root,'claude:test')[0]['receipt_id'])
            out=prepare(root,'claude:test',row['receipt_id'])
            self.assertEqual('private',out['arguments']['reply_token'])
            self.assertEqual('answer',out['arguments']['message'])
            self.assertNotIn('arguments',prepare(root,'claude:test',row['receipt_id']))
            self.assertEqual([],pending(root,'claude:test'))
            result=record(root,'claude:test',row['receipt_id'],'submitted',attempt_id=out['attempt_id'])
            self.assertEqual('submitted',result['result']['delivery'])
            self.assertNotIn('arguments',prepare(root,'claude:test',row['receipt_id']))

    def test_restart_after_prepare_never_blindly_resends_unknown_submission(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            inbox = SideIntakeInbox(root, 'claude:test')
            key = inbox.receive('question', 'event', route={
                'channel': 'web', 'source_kind': 'web_chat', 'reply_token': 'private'})['receipt_id']
            leased = claim(root, 'claude:test', 'actor')
            finish(root, 'claude:test', key, leased['generation'], {'reply': 'answer', 'delivery': 'pending'})
            first = prepare(root, 'claude:test', key)
            self.assertIn('arguments', first)
            restarted = SideIntakeInbox(root, 'claude:test')
            self.assertEqual('submitting', restarted.summaries()[0]['result']['delivery'])
            self.assertEqual([], pending(root, 'claude:test'))
            again = prepare(root, 'claude:test', key)
            self.assertEqual('submitting', again['state'])
            self.assertNotIn('arguments', again)
            result = record(root, 'claude:test', key, 'uncertain',attempt_id=first['attempt_id'])
            self.assertEqual('uncertain', result['result']['delivery'])
            self.assertNotIn('arguments', prepare(root, 'claude:test', key))
            from ciel_runtime_support.side_reply_outbox import retry
            with self.assertRaises(Exception):
                retry(root, 'claude:test', key, 'uncertain')
            with self.assertRaises(Exception):
                retry(root, 'claude:test', key, 'submitted', confirmed=True)
            retried = retry(root, 'claude:test', key, 'uncertain', confirmed=True)
            self.assertEqual('pending', retried['result']['delivery'])
            self.assertEqual(key, pending(root, 'claude:test')[0]['receipt_id'])
            second = prepare(root, 'claude:test', key)
            self.assertIn('arguments', second)
            self.assertNotEqual(first['attempt_id'],second['attempt_id'])
            from ciel_runtime_support.work_plan_tree import WorkPlanError
            with self.assertRaises(WorkPlanError):
                record(root,'claude:test',key,'submitted',attempt_id=first['attempt_id'])
            record(root,'claude:test',key,'submitted',attempt_id=second['attempt_id'])
            history = SideIntakeInbox(root, 'claude:test')._load(key)['delivery_retries']
            self.assertTrue(history[0]['duplicate_send_confirmed'])

    def test_advice_status_then_final_answer_respects_real_browser_one_shot_reply(self):
        from dataclasses import replace
        from test_channel_mcp_tools import ChannelMcpToolsTests
        from ciel_runtime_support.channel_mcp_tools import dispatch_channel_mcp_tool
        fixture = ChannelMcpToolsTests()
        fixture.setUp()
        fixture.messages.append({'id': 1, 'channel': 'web-chat-owned', 'thread_id': 'thread',
                                 'kind': 'web_chat', 'message': 'Question',
                                 'meta': {'source': 'ciel-runtime-web-chat', 'input_mode': 'text'}})
        route = {'source_kind': 'web_chat', 'channel': 'web-chat-owned',
                 'thread_id': 'thread', 'parent_id': '1', 'reply_token': 'private'}
        services = replace(fixture.services, runtime=replace(fixture.services.runtime,
            read_runtime_inputs=lambda *args: [{'channel': route['channel'], 'thread_id': 'thread',
                'meta': {'reply_parent_id': '1', 'web_reply_token': 'private'}}]))
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            inbox = SideIntakeInbox(root, 'claude:test')
            for event, action, expected_kind in [('question', 'advise', 'status'), ('followup', 'answer', 'reply')]:
                key = inbox.receive(event, event, route=route)['receipt_id']
                leased = claim(root, 'claude:test', 'actor')
                finish(root, 'claude:test', key, leased['generation'], {'action': action, 'reply': event})
                prepared = prepare(root, 'claude:test', key)
                self.assertEqual(expected_kind, prepared['arguments']['kind'])
                sent = dispatch_channel_mcp_tool(1, {'name': 'send_message', 'arguments': prepared['arguments']}, services)
                self.assertFalse(sent['result'].get('isError'), sent)
                record(root, 'claude:test', key, 'submitted',attempt_id=prepared['attempt_id'])
            final = dispatch_channel_mcp_tool(2, {'name': 'send_message', 'arguments': prepared['arguments']}, services)
            self.assertTrue(final['result']['isError'])
            self.assertEqual(['web_chat', 'status', 'reply'], [row['kind'] for row in fixture.messages])

    def test_external_input_channel_is_not_assumed_to_be_reply_connector(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            inbox=SideIntakeInbox(root,'claude:test')
            row=inbox.receive('question','key',route={'channel':'external:receiver'})
            leased=claim(root,'claude:test','actor')
            finish(root,'claude:test',row['receipt_id'],leased['generation'],{'reply':'answer'})
            self.assertEqual('route_unavailable',prepare(root,'claude:test',row['receipt_id'])['state'])
            from ciel_runtime_support.side_reply_outbox import retry
            self.assertEqual('pending', retry(root,'claude:test',row['receipt_id'],'route_unavailable')['result']['delivery'])
            self.assertEqual('route_unavailable',prepare(root,'claude:test',row['receipt_id'])['state'])

    def test_external_event_answered_by_the_side_ai_through_mcp_is_side_mcp(self):
        for reported, expected in ((['mcp__walkie__walkie_send_message'], 'side_mcp'), ([], 'route_unavailable')):
            with self.subTest(reported=reported), tempfile.TemporaryDirectory() as raw:
                root=Path(raw)
                inbox=SideIntakeInbox(root,'claude:test')
                row=inbox.receive('question','key',route={'channel':'external:default','source_kind':'external_event'})
                leased=claim(root,'claude:test','actor')
                finish(root,'claude:test',row['receipt_id'],leased['generation'],{'reply':'answer','reported_sends':reported})
                prepared=prepare(root,'claude:test',row['receipt_id'])
                self.assertEqual(expected,prepared['state'])
                self.assertNotIn('arguments',prepared)
                self.assertEqual(expected,prepared['receipt']['result']['delivery'])
                self.assertEqual([],pending(root,'claude:test'))
                if expected == 'side_mcp':
                    from ciel_runtime_support.side_reply_outbox import retry
                    from ciel_runtime_support.work_plan_tree import WorkPlanError
                    with self.assertRaises(WorkPlanError):
                        retry(root,'claude:test',row['receipt_id'],'side_mcp')

    def test_resolve_keeps_only_reported_mcp_tool_names(self):
        from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
        from ciel_runtime_support.work_plan_store import session_work_plan_store
        import json
        token='outbox-host-token-'*3
        config={'work_mode':True,'work_parallel_host':{'token':token},'work_side_intake':{'enabled':True}}
        def respond(_i,text,error=False): return {'content':[{'type':'text','text':text}],'isError':error}
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            SideIntakeInbox(root,'claude:test').receive('question','key',route={'source_kind':'external_event'})
            claimed=dispatch_work_tool(1,'work_side_claim',{'owner':'side-1','session_id':'claude:test','_host':{'token':token}},
                                       load_config=lambda:config,state_dir=root,respond=respond)
            event=json.loads(claimed['content'][0]['text'])['claim']
            version=session_work_plan_store(root,'claude:test').read()['version']
            reported=['mcp__walkie__walkie_send_message','Bash',{'tool':'x'},'mcp__walkie__walkie_send_message']
            resolved=dispatch_work_tool(2,'work_side_resolve',{'session_id':'claude:test',
                'decision':{'action':'answer','summary':'s','reply':'r'},'expected_version':version,
                '_host':{'token':token,'receipt_id':event['receipt_id'],'generation':event['generation'],'reported_sends':reported}},
                load_config=lambda:config,state_dir=root,respond=respond)
            self.assertFalse(resolved['isError'],resolved)
            self.assertEqual(['mcp__walkie__walkie_send_message'],json.loads(resolved['content'][0]['text'])['result']['reported_sends'])

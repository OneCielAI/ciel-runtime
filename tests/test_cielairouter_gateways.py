import threading
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from ciel_runtime_support.cielairouter_gateways import (
    CielAiRouterGatewayService,
    DiscoveryError,
    GatewayHttpClient,
    ProbeResult,
    normalize_endpoint,
    validate_discovery,
)


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


def row(node, endpoint, now, *, utilization=0.2, backlog=1, health='healthy', observed=None):
    return {'id': node, 'endpoint': endpoint, 'endpoints': [endpoint], 'status': 'online',
            'health': health, 'lastSeenAt': stamp(now - 2), 'availableUntil': stamp(now + 300),
            'healthUrl': endpoint.rstrip('/').removesuffix('/v1') + '/healthz',
            'clientProbe': {'path': '/healthz', 'method': 'GET', 'timeoutMs': 2000},
            'congestion': {'utilization': utilization, 'backlog': backlog,
                           'observedAt': stamp(now - 2 if observed is None else observed)}}


class GatewayTests(unittest.TestCase):
    def test_endpoint_normalization_and_https_policy(self):
        self.assertEqual('https://example.test', normalize_endpoint('https://EXAMPLE.test/v1/'))
        self.assertEqual('http://127.0.0.1:1234', normalize_endpoint('http://127.0.0.1:1234/v1'))
        with self.assertRaises(DiscoveryError):
            normalize_endpoint('http://gateway.example.test/v1')
        self.assertEqual('http://gateway.example.test', normalize_endpoint('http://gateway.example.test/v1', allow_insecure_http=True))
        for value in ('https://u:p@example.test', 'https://example.test?a=1', 'https://example.test/#x'):
            with self.assertRaises(DiscoveryError):
                normalize_endpoint(value)

    def test_schema_filters_stale_and_degraded_telemetry_is_unknown(self):
        now = 1000.0
        payload = {'object': 'list', 'protocol_version': 1, 'generated_at': stamp(now),
                   'data': [row('a', 'https://a.test/v1', now, observed=now - 1000),
                            {'id': 'bad', 'status': 'online'}]}
        snapshot = validate_discovery(payload, now=now)
        self.assertEqual(1, len(snapshot.candidates))
        self.assertIsNone(snapshot.candidates[0].utilization)
        self.assertGreaterEqual(snapshot.excluded_count, 1)

    def test_rank_congestion_then_backlog_then_rtt_and_sticky(self):
        now = [1000.0]
        payload = {'object': 'list', 'protocol_version': 1, 'generated_at': stamp(now[0]),
                   'data': [row('a', 'https://a.test', now[0], utilization=.1, backlog=9),
                            row('b', 'https://b.test', now[0], utilization=.2, backlog=0)]}
        probes = {'a.test': 0.2, 'b.test': 0.01}
        def discovery(*_): return payload
        def probe(candidate, _): return ProbeResult(True, probes[candidate.endpoint.split('//')[1]])
        service = CielAiRouterGatewayService(discovery=discovery, probe=probe, clock=lambda: now[0])
        first = service.select('https://router.test', 'secret', 'm')
        self.assertEqual('https://a.test', first.endpoint)
        probes['a.test'] = .9
        now[0] += 6
        second = service.select('https://router.test', 'secret', 'm', refresh=True)
        self.assertEqual(first.endpoint, second.endpoint)
        self.assertEqual('sticky', second.reason)

    def test_failure_cooldown_and_non_gateway_errors(self):
        now = [1000.0]
        payload = {'object': 'list', 'protocol_version': 1, 'generated_at': stamp(now[0]),
                   'data': [row('a', 'https://a.test', now[0]), row('b', 'https://b.test', now[0], utilization=.3)]}
        service = CielAiRouterGatewayService(discovery=lambda *_: payload,
            probe=lambda c, _: ProbeResult(True, .1), clock=lambda: now[0], refresh_interval=.01)
        selected = service.select('https://router.test', 'secret', 'm')
        self.assertFalse(service.record_failure(selected, '401'))
        self.assertTrue(service.record_failure(selected, 'timeout'))
        now[0] += .02
        changed = service.select('https://router.test', 'secret', 'm')
        self.assertNotEqual(selected.endpoint, changed.endpoint)

    def test_concurrent_select_is_singleflight(self):
        now = 1000.0
        payload = {'object': 'list', 'protocol_version': 1, 'generated_at': stamp(now), 'data': [row('a', 'https://a.test', now)]}
        calls = mock.Mock(side_effect=lambda *_: (time.sleep(.02), payload)[1])
        service = CielAiRouterGatewayService(discovery=calls, probe=lambda c, _: ProbeResult(True, .1), clock=lambda: now)
        results = []
        threads = [threading.Thread(target=lambda: results.append(service.select('https://r.test', 'key', 'm'))) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(1, calls.call_count)
        self.assertEqual(1, len({item.endpoint for item in results}))

    def test_http_discovery_auth_and_probe_without_key(self):
        calls = []
        class Response:
            status = 200
            def geturl(self): return calls[-1][0]
            def read(self, _): return b'{}'
            def __enter__(self): return self
            def __exit__(self, *_): pass
        client = GatewayHttpClient(lambda request, timeout: (calls.append((request.full_url, dict(request.headers), timeout)) or Response()))
        client.get_json('https://r.test/api', 1, 'key')
        client.get_json('https://a.test/healthz', 1)
        self.assertIn('Authorization', calls[0][1])
        self.assertNotIn('Authorization', calls[1][1])



    def test_model_filter_scope_separation_and_stale_failure_fence(self):
        now = 1000.0
        payload = {'object': 'list', 'protocol_version': 1, 'generated_at': stamp(now),
                   'data': [row('a', 'https://a.test', now), row('b', 'https://b.test', now)]}
        service = CielAiRouterGatewayService(discovery=lambda *_: payload,
            probe=lambda c, _: ProbeResult(True, .1), clock=lambda: now,
            model_support=lambda c, m, k, t: c.node_id == ('a' if m == 'one' else 'b'))
        first = service.select('https://router.test', 'key', 'one')
        second = service.select('https://router.test', 'key', 'two')
        self.assertEqual('https://a.test', first.endpoint)
        self.assertEqual('https://b.test', second.endpoint)
        self.assertTrue(service.record_failure(first, '5xx'))
        self.assertFalse(service.record_failure(first, '5xx'))
        self.assertNotIn('key', str(service.status()))

    def test_all_probes_failed_returns_explicit_degraded_base(self):
        now = 1000.0
        payload = {'object': 'list', 'protocol_version': 1, 'generated_at': stamp(now),
                   'data': [row('a', 'https://a.test', now)]}
        service = CielAiRouterGatewayService(discovery=lambda *_: payload,
            probe=lambda *_: ProbeResult(False), clock=lambda: now)
        result = service.select('https://router.test', 'key')
        self.assertTrue(result.degraded)
        self.assertIsNone(result.candidate)
        self.assertEqual('https://router.test', result.endpoint)

    def test_bad_health_identity_and_ready_are_excluded(self):
        now = 1000.0
        payload = {'object': 'list', 'protocol_version': 1, 'generated_at': stamp(now),
                   'data': [row('a', 'https://a.test', now)]}
        for outcome in (ProbeResult(True, .1, node_id='wrong'),
                        ProbeResult(True, .1, role='central'), ProbeResult(True, .1, ready=False)):
            service = CielAiRouterGatewayService(discovery=lambda *_: payload,
                probe=lambda *_, result=outcome: result, clock=lambda: now)
            self.assertTrue(service.select('https://r.test', 'key').degraded)

    def test_alternate_endpoint_uses_its_own_origin_and_cap(self):
        now = 1000.0
        node = row('a', 'https://a.test/v1', now)
        node['endpoints'] += ['https://b.test/v1'] + [f'https://g{i}.test' for i in range(30)]
        node['clientProbe']['timeoutMs'] = 9000
        snapshot = validate_discovery({'object': 'list', 'protocol_version': 1,
            'generated_at': stamp(now), 'data': [node]}, now=now)
        self.assertEqual(16, len(snapshot.candidates))
        self.assertEqual('https://b.test/healthz', snapshot.candidates[1].health_url)
        self.assertEqual(2, snapshot.candidates[1].timeout_seconds)
        self.assertGreater(snapshot.excluded_count, 0)



class ClockOffsetTests(unittest.TestCase):
    def test_central_clock_ahead_or_behind_still_yields_candidates(self):
        now = 1_800_000_000.0
        for skew in (-45.0, 3.0, 120.0):
            with self.subTest(skew=skew):
                server = now + skew
                payload = {'object': 'list', 'protocol_version': 1, 'generated_at': stamp(server),
                           'data': [row('a', 'https://a.test', server)]}
                snapshot = validate_discovery(payload, now=now)
                self.assertEqual(['https://a.test'], [c.endpoint for c in snapshot.candidates])
                candidate = snapshot.candidates[0]
                self.assertLessEqual(candidate.last_seen_at, now)
                self.assertGreater(candidate.available_until, now)
                self.assertIsNotNone(candidate.utilization)

    def test_absurd_clock_offset_is_rejected(self):
        now = 1_800_000_000.0
        server = now + 3 * 86400
        payload = {'object': 'list', 'protocol_version': 1, 'generated_at': stamp(server),
                   'data': [row('a', 'https://a.test', server)]}
        with self.assertRaises(DiscoveryError):
            validate_discovery(payload, now=now)


class SessionRankingTests(unittest.TestCase):
    def service(self, now, rows):
        payload = {'object': 'list', 'protocol_version': 1, 'generated_at': stamp(now[0]), 'data': rows}
        rtts = {'https://a.test': .3, 'https://b.test': .1}
        return CielAiRouterGatewayService(discovery=lambda *_: payload,
                                          probe=lambda c, _: ProbeResult(True, rtts[c.endpoint]),
                                          clock=lambda: now[0], refresh_interval=.01)

    def test_fresh_selection_ranks_again_instead_of_keeping_the_sticky_gateway(self):
        now = [1000.0]
        rows = [row('a', 'https://a.test', now[0], utilization=.1),
                row('b', 'https://b.test', now[0], utilization=.1)]
        service = self.service(now, rows)
        first = service.select('https://router.test', 'key', 'm')
        self.assertEqual('https://b.test', first.endpoint)
        rows[1]['congestion']['utilization'] = .8
        now[0] += .02
        self.assertEqual('https://b.test', service.select('https://router.test', 'key', 'm', refresh=True).endpoint)
        now[0] += .02
        self.assertEqual('https://a.test', service.select('https://router.test', 'key', 'm', fresh=True).endpoint)

    def test_a_late_failure_from_an_older_route_still_cools_its_gateway_once(self):
        now = [1000.0]
        rows = [row('a', 'https://a.test', now[0]), row('b', 'https://b.test', now[0])]
        service = self.service(now, rows)
        old = service.select('https://router.test', 'key', 'm')
        now[0] += .02
        service.select('https://router.test', 'key', 'm', fresh=True)
        self.assertTrue(service.record_failure(old, 'stream_truncated'))
        self.assertFalse(service.record_failure(old, 'stream_truncated'))
        self.assertTrue(service.is_cooling(old))

if __name__ == '__main__':
    unittest.main()

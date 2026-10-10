import unittest
import json
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
from urllib.error import HTTPError
from ciel_runtime_support import provider_models as m
from ciel_runtime_support.provider_model_selection import ProviderModelSelection


class AuthoritativeCatalogFailureTests(unittest.TestCase):
    def services(self, error, authoritative=True):
        cache = {}
        policy = NS(kind='openai', authoritative_upstream_catalog=authoritative,
                    allow_configured_fallback=True, fallback_models=('ASTRA',),
                    supplemental_model_aliases=(), allow_public_without_auth=False,
                    use_bundled_catalog_fallback=False)
        http = Mock(side_effect=[error, {'data': [{'id': 'requested'}]}])
        def write(_provider, _config, ids, *args):
            cache['ids'] = ids
        def noop(*a, **kw):
            return None
        services = m.ProviderModelServices(
            storage=m.ModelCatalogStorage(lambda *a: cache.get('ids'), write, noop, noop),
            http=m.ModelCatalogHttp(http, lambda b,p: b+p, lambda x:x, noop, noop, noop),
            sources=m.ProviderCatalogSources((), noop, noop, noop, noop, noop),
            response_codec=m.ModelCatalogResponseCodec(lambda d:[r['id'] for r in d['data']], lambda *a:{}),
            policy=m.ModelCatalogPolicy(lambda p,x:x, noop, lambda *a:True, lambda *a:policy,
                lambda *a:('/v1/models',), lambda *a:{}, lambda *a:'https://fixture.invalid',
                sorted, lambda p,ids:list(dict.fromkeys(x for x in ids if x))))
        return services, cache, http

    def test_failed_authoritative_fetch_does_not_cache_fallback_and_can_recover(self):
        for error in (TimeoutError('fixture'), HTTPError('https://fixture.invalid', 401, 'unauthorized', {}, None)):
            with self.subTest(error=type(error).__name__):
                services, cache, http = self.services(error)
                with self.assertRaisesRegex(RuntimeError, 'catalog unavailable'):
                    m.fetch_upstream_model_ids('test', {'current_model':'ASTRA'}, services=services)
                self.assertEqual(cache, {})
                self.assertEqual(m.fetch_upstream_model_ids('test', {}, services=services), ['requested'])
                self.assertEqual(http.call_count, 2)

    def test_non_authoritative_offline_fallback_is_preserved(self):
        services, cache, _ = self.services(TimeoutError(), authoritative=False)
        self.assertEqual(m.fetch_upstream_model_ids('test', {}, services=services), ['ASTRA'])
        self.assertEqual(cache['ids'], ['ASTRA'])

    def test_successful_empty_catalog_remains_authoritative(self):
        services, _, http = self.services(TimeoutError())
        http.side_effect = None
        http.return_value = {'data': []}
        self.assertEqual(m.fetch_upstream_model_ids('test', {'current_model':'ASTRA'}, services=services), [])

    def test_catalog_unavailable_does_not_authorize_configured_model(self):
        adapter = Mock()
        adapter.model_catalog_policy.return_value = NS(authoritative_upstream_catalog=True)
        selection = Mock()
        selection.adapter.return_value = adapter
        selection.placeholders.return_value = set()
        selection.upstream_ids.side_effect = RuntimeError('catalog unavailable')
        identity = Mock()
        identity.normalize.side_effect = lambda provider, model:model
        ok, messages = ProviderModelSelection(identity, selection, Mock()).ensure_selected('test', {'current_model':'requested'})
        self.assertFalse(ok)
        self.assertIn('unavailable', ' '.join(messages))


    def test_diagnostic_log_has_safe_cause_and_provider_timeout(self):
        error = HTTPError('https://fixture.invalid/models?secret=fixture-secret', 401, 'fixture-secret', {}, None)
        services, _, http = self.services(error)
        policy = services.policy.provider_model_catalog_policy('test', {})
        policy.request_timeout_seconds = 10.0
        log = Mock()
        services = replace(services,
            storage=replace(services.storage, router_log=log),
            policy=replace(services.policy,
                provider_model_paths=lambda *a:('/v1/models?secret=fixture-secret',),
                provider_model_list_headers=lambda *a:{'Authorization':'Bearer fixture-secret'}))
        with self.assertRaises(m.CatalogUnavailableError):
            m.fetch_upstream_model_ids('test', {}, services=services)
        self.assertEqual(http.call_args.kwargs['timeout'], 10.0)
        level, message = log.call_args.args
        self.assertEqual(level, 'WARN')
        self.assertNotIn('fixture-secret', message)
        self.assertNotIn('?secret', message)
        details = json.loads(message.split(' ', 1)[1])
        self.assertEqual(details['http_status'], 401)
        self.assertTrue(details['auth_header_present'])
        self.assertEqual(len(details['key_fingerprint']), 12)

    def test_router_catalog_unavailable_returns_503_not_empty_success(self):
        from ciel_runtime_support.router_http import RouterHttpHandler
        endpoints = NS(**{name:lambda *a:False for name in (
            'tui','events','external_events','llm_config','channel_mcp','web','speech','chat','plan','runtime')})
        writer = Mock()
        services = NS(core=NS(load_config=lambda:{},reject_external=lambda *a:False,
            get_current_provider=lambda cfg:('test',{}),remote_bridge=None),
            get=endpoints,files=None,access=None,
            presentation=NS(list_models=Mock(side_effect=m.CatalogUnavailableError('unavailable')),write_json=writer))
        class Handler(RouterHttpHandler):
            services_factory = staticmethod(lambda:services)
        handler = object.__new__(Handler)
        handler.path = '/v1/models'
        handler.headers = {}
        handler.do_GET()
        self.assertEqual(writer.call_args.args[2], 503)
        self.assertEqual(writer.call_args.args[1]['error']['code'], 'model_catalog_unavailable')


    def test_schema_change_invalidates_old_list_and_registry(self):
        import tempfile
        from pathlib import Path
        from ciel_runtime_support.provider_model_metadata_context import ProviderModelMetadataContext
        from ciel_runtime_support.model_registry_repository import ModelRegistryPaths, ModelRegistryPolicy, ModelRegistryRepository
        headers = Mock()
        headers.api_key_count.return_value = 1
        headers.primary_api_key.return_value = 'fixture-key'
        headers.configured_adapter.return_value.model_catalog_policy.return_value = NS(per_key_catalog=True)
        context = ProviderModelMetadataContext(Mock(), Mock(), headers, Path('/unused'))
        key = context.cache_key('test', {})
        old_key = json.dumps({**json.loads(key), 'schema':7}, sort_keys=True)
        self.assertNotEqual(key, old_key)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = {'key':old_key,'models':['ASTRA'],'time':0}
            (root/'list.json').write_text(json.dumps(old))
            (root/'registry.json').write_text(json.dumps({'providers':{'test':old}}))
            policy = ModelRegistryPolicy(context.cache_key, lambda p,ids:ids, lambda p,x:x,
                lambda x:None, lambda *a:{}, lambda *a:None)
            repo = ModelRegistryRepository(ModelRegistryPaths(root,root/'registry.json',root/'list.json'), policy, 300)
            self.assertIsNone(repo.read_list_cache('test', {}))


if __name__ == '__main__':
    unittest.main()

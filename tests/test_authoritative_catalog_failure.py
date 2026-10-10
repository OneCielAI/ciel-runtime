import unittest
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


if __name__ == '__main__':
    unittest.main()

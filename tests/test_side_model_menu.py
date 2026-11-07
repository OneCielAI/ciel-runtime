import unittest

from ciel_runtime_support.side_model_menu import rows


class SideModelMenuTests(unittest.TestCase):
    def test_primary_side_model_only_offers_main_session_connection(self):
        config = {'work_side_intake': {'model': 'API KEYS - SSE'}}
        labels, values = rows(config, (['Refresh', '* main', 'SSE', 'Custom', 'Back'],
                                      ['__refresh_models__', 'main', 'API KEYS - SSE', '__custom__', 'back']))
        self.assertEqual(['inherit', 'back'], values)
        self.assertTrue(labels[0].startswith('* '))
        self.assertNotIn('current_model', config)

    def test_comparison_catalog_and_inherit_do_not_change_main_model(self):
        config = {'work_side_intake': {'comparison_model': 'API KEYS - SSE'}}
        labels, values = rows(config, (['Refresh', '* main', 'SSE', 'Custom', 'Back'],
                                      ['__refresh_models__', 'main', 'API KEYS - SSE', '__custom__', 'back']),
                              field='comparison_model')
        self.assertEqual('inherit', values[0])
        self.assertEqual(1, values.count('API KEYS - SSE'))
        self.assertEqual(['__edit__', 'back'], values[-2:])
        self.assertTrue(labels[values.index('API KEYS - SSE')].startswith('* '))
        self.assertNotIn('current_model', config)

    def test_missing_comparison_catalog_preserves_explicit_current(self):
        labels, values = rows({'work_side_intake': {'comparison_model': 'saved-custom'}}, ([], []),
                              field='comparison_model')
        self.assertEqual(['inherit', 'saved-custom', '__edit__', 'back'], values)
        self.assertTrue(labels[1].startswith('* '))

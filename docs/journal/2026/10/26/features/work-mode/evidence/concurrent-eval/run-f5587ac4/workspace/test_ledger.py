import csv
from decimal import Decimal, localcontext
from io import StringIO
import random
import unittest

from ledger import reconcile


class LedgerTests(unittest.TestCase):
    def test_empty_and_blank_rows(self):
        for chunks in ([], [''], ['', ''], ['\r', '\n\r\n'],
                       ['id,account,amount\r\n\r\n']):
            self.assertEqual(reconcile(iter(chunks)), {})
        self.assertEqual(reconcile(['\n', 'id,account,amount\n\n1,a,0\n\n']),
                         {'a': Decimal('0')})

    def test_exact_negative_and_zero(self):
        result = reconcile(['id,account,amount\n1,a,0.1\n2,a,0.2\n',
                            '3,b,-12.50\n4,b,12.50\n5,c,0\n'])
        self.assertEqual(result, {'a': Decimal('0.3'), 'b': Decimal(0), 'c': Decimal(0)})
        self.assertTrue(all(isinstance(value, Decimal) for value in result.values()))

    def test_every_single_and_double_fragment_boundary(self):
        text = 'id,account,amount\r\n1,"a,""b""\r\nc",1.25\r\n2,z,-2\r\n'
        expected = {'a,"b"\r\nc': Decimal('1.25'), 'z': Decimal('-2')}
        self.assertEqual(reconcile(iter(text)), expected)
        for first in range(len(text) + 1):
            for second in range(first, len(text) + 1):
                self.assertEqual(reconcile([text[:first], text[first:second], text[second:]]), expected)

    def test_multiline_quotes_commas_unicode(self):
        text = 'id,account,amount\n1,"line one\nline two",3\n2,"한글,é",4\n3,"say ""hi""",5'
        expected = {'line one\nline two': Decimal(3), '한글,é': Decimal(4),
                    'say "hi"': Decimal(5)}
        self.assertEqual(reconcile(text[i:i + 3] for i in range(0, len(text), 3)), expected)

    def test_missing_and_empty_fields(self):
        for row in ('', ',a,1', 'id,,1', 'id,a', 'id', ',,', 'id,a,', 'id,a,1,extra'):
            if not row:
                continue  # A truly blank row is ignored, unlike missing fields.
            with self.subTest(row=row), self.assertRaises(ValueError):
                reconcile(['id,account,amount\n' + row])

    def test_invalid_finite_decimals(self):
        for amount in ('NaN', 'sNaN', 'NaN123', 'Infinity', '-Infinity', 'inf',
                       'not-a-number', '1.2.3', ' ', ''):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                reconcile(['id,account,amount\n1,a,' + amount])

    def test_bad_header_and_malformed_csv(self):
        for text in ('account,id,amount\n1,a,2', 'id,account\n1,a',
                     'id,account,amount\n1,"unterminated,2',
                     'id,account,amount\n1,"a"trailing,2'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                reconcile([text])

    def test_global_last_id_wins_account_changes_and_zero_retained(self):
        text = ('id,account,amount\n'
                '1,old,100\n2,old,5\n1,new,7\n2,old,-5\n'
                '3,new,-7\n4,emptied,9\n4,other,2\n')
        self.assertEqual(reconcile(iter(text)), {
            'old': Decimal('-5'), 'new': Decimal(0),
            'emptied': Decimal(0), 'other': Decimal(2)})

    def test_same_id_last_amount_only(self):
        self.assertEqual(reconcile(['id,account,amount\n1,a,1\n1,a,2\n1,a,3']),
                         {'a': Decimal(3)})

    def test_every_row_validated_even_if_overridden(self):
        for bad in ('1,a,NaN', '1,,5', '1,a,nope', ',a,1', '1,a'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                reconcile(['id,account,amount\n' + bad + '\n1,a,2'])

    def test_exact_beyond_decimal_context_precision_and_exponents(self):
        with localcontext() as ctx:
            ctx.prec = 2
            result = reconcile(['id,account,amount\n'
                                '1,a,123456789012345678901234567890.123456789\n'
                                '2,a,0.000000001\n3,b,1E+100\n4,b,1\n'
                                '5,c,1E+100\n6,c,-1E+100\n7,c,0.1'])
        self.assertEqual(result['a'], Decimal('123456789012345678901234567890.123456790'))
        self.assertEqual(result['b'], Decimal('1' + '0' * 99 + '1'))
        self.assertEqual(result['c'], Decimal('0.1'))

    def test_random_fragmentation_and_last_wins_reference(self):
        rng = random.Random(42)
        rows = []
        final = {}
        expected = {name: Decimal(0) for name in ('a', 'b', 'c')}
        for _ in range(100):
            row_id = str(rng.randrange(12))
            account = rng.choice(list(expected))
            value = rng.randrange(-1000, 1000)
            rows.append([row_id, account, str(value)])
            final[row_id] = (account, value)
        for account, value in final.values():
            expected[account] += Decimal(value)
        stream = StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(['id', 'account', 'amount'])
        writer.writerows(rows)
        text = stream.getvalue()
        for _ in range(25):
            cuts = sorted({0, len(text)} | {rng.randrange(len(text)) for _ in range(80)})
            self.assertEqual(reconcile(text[a:b] for a, b in zip(cuts, cuts[1:])), expected)


if __name__ == '__main__':
    unittest.main()

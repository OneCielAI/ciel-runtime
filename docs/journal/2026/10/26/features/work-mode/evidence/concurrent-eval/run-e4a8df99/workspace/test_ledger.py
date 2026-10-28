import unittest
from decimal import Decimal

from ledger import reconcile

DOC = (
    'id,account,amount\r\n'
    '1,"Cash\r\nDrawer",10.50\r\n'
    '2,"Bank ""Main""",-3.25\r\n'
    '\r\n'
    '3,"Cash\r\nDrawer",0.50\r\n'
    '4,Zero,0\r\n'
)
EXPECTED = {
    "Cash\r\nDrawer": Decimal("11.00"),
    'Bank "Main"': Decimal("-3.25"),
    "Zero": Decimal("0"),
}


def split_at(text, *cuts):
    points = [0, *cuts, len(text)]
    return [text[a:b] for a, b in zip(points, points[1:])]


class ParsingTests(unittest.TestCase):
    def test_single_chunk(self):
        self.assertEqual(reconcile([DOC]), EXPECTED)

    def test_every_single_split_point(self):
        for i in range(len(DOC) + 1):
            with self.subTest(cut=i):
                self.assertEqual(reconcile(split_at(DOC, i)), EXPECTED)

    def test_every_pair_of_split_points(self):
        for i in range(len(DOC) + 1):
            for j in range(i, len(DOC) + 1):
                self.assertEqual(reconcile(split_at(DOC, i, j)), EXPECTED, (i, j))

    def test_one_character_chunks_generator(self):
        self.assertEqual(reconcile(c for c in DOC), EXPECTED)

    def test_lf_line_endings_and_no_trailing_newline(self):
        self.assertEqual(
            reconcile(["id,account,amount\n1,a,1\n2,a,2"]), {"a": Decimal("3")}
        )

    def test_split_inside_crlf(self):
        doc = "id,account,amount\r\n1,a,5\r\n2,b,7\r\n"
        cut = doc.index("\r\n", 20) + 1
        self.assertEqual(reconcile(split_at(doc, cut)), {"a": Decimal(5), "b": Decimal(7)})

    def test_types_are_decimal(self):
        for value in reconcile([DOC]).values():
            self.assertIs(type(value), Decimal)


class EmptyAndBlankTests(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(reconcile([]), {})
        self.assertEqual(reconcile([""]), {})
        self.assertEqual(reconcile(["", ""]), {})

    def test_header_only(self):
        self.assertEqual(reconcile(["id,account,amount\r\n"]), {})

    def test_blank_rows_ignored(self):
        doc = "\r\nid,account,amount\r\n\r\n1,a,1\n\n\r\n2,a,1\r\n\r\n"
        self.assertEqual(reconcile([doc]), {"a": Decimal(2)})


class AmountTests(unittest.TestCase):
    def test_exact_decimal_no_float_drift(self):
        doc = "id,account,amount\n" + "".join(f"{i},a,0.1\n" for i in range(10))
        self.assertEqual(reconcile([doc]), {"a": Decimal("1.0")})

    def test_high_precision_exact(self):
        doc = (
            "id,account,amount\n"
            "1,a,12345678901234567890123456789.000000001\n"
            "2,a,0.000000000000000000000000000001\n"
        )
        self.assertEqual(
            reconcile([doc])["a"],
            Decimal("12345678901234567890123456789.000000001000000000000000000001"),
        )

    def test_negative_zero_and_signs(self):
        doc = "id,account,amount\n1,a,-5\n2,a,+5\n3,b,0\n4,c,-0.00\n5,d,1e2\n"
        result = reconcile([doc])
        self.assertEqual(result, {"a": Decimal(0), "b": Decimal(0), "c": Decimal(0),
                                  "d": Decimal(100)})

    def test_invalid_amounts_rejected(self):
        for bad in ["NaN", "nan", "sNaN", "Infinity", "-Infinity", "inf", "abc", "",
                    "1,5", "1_000", " 1", "1 ", "0x10", "1.2.3", "--1", "1e"]:
            with self.subTest(amount=bad):
                doc = f'id,account,amount\n1,a,"{bad}"\n'
                with self.assertRaises(ValueError):
                    reconcile([doc])


class ValidationTests(unittest.TestCase):
    def test_missing_or_empty_fields(self):
        for row in ["1,a", ",a,1", "1,,1", '"",a,1', '1,"",1', "1,a,1,extra", "  ,a,1",
                    "1, ,1"]:
            with self.subTest(row=row):
                with self.assertRaises(ValueError):
                    reconcile([f"id,account,amount\n{row}\n"])

    def test_bad_header(self):
        for header in ["id,acct,amount", "account,id,amount", "id,account", "1,a,1"]:
            with self.subTest(header=header):
                with self.assertRaises(ValueError):
                    reconcile([f"{header}\n1,a,1\n"])

    def test_malformed_quoting(self):
        for doc in ['id,account,amount\n1,"unterminated,1\n',
                    'id,account,amount\n1,"a"x,1\n']:
            with self.subTest(doc=doc):
                with self.assertRaises(ValueError):
                    reconcile([doc])

    def test_non_string_chunk(self):
        with self.assertRaises(TypeError):
            reconcile([b"id,account,amount\n"])


class DuplicateIdTests(unittest.TestCase):
    def test_last_row_wins_same_account(self):
        doc = "id,account,amount\n1,a,10\n1,a,3\n2,a,1\n"
        self.assertEqual(reconcile([doc]), {"a": Decimal(4)})

    def test_last_row_wins_moves_account_and_keeps_zero(self):
        doc = "id,account,amount\n1,a,10\n2,b,5\n1,b,7\n"
        self.assertEqual(reconcile([doc]), {"a": Decimal(0), "b": Decimal(12)})

    def test_last_occurrence_after_many_moves(self):
        doc = "id,account,amount\nx,a,1\nx,b,2\nx,c,3\nx,a,4\n"
        self.assertEqual(
            reconcile([doc]), {"a": Decimal(4), "b": Decimal(0), "c": Decimal(0)}
        )

    def test_overridden_row_still_validated(self):
        for bad_row in ["1,a,NaN", "1,,5", "1,a,oops"]:
            with self.subTest(row=bad_row):
                with self.assertRaises(ValueError):
                    reconcile([f"id,account,amount\n{bad_row}\n1,a,5\n"])

    def test_duplicates_across_fragment_splits(self):
        doc = 'id,account,amount\r\n7,"x\r\ny",1.5\r\n7,"z",2.5\r\n'
        for i in range(len(doc) + 1):
            with self.subTest(cut=i):
                self.assertEqual(
                    reconcile(split_at(doc, i)),
                    {"x\r\ny": Decimal(0), "z": Decimal("2.5")},
                )


if __name__ == "__main__":
    unittest.main()

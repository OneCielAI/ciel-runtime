import json
import random
import unittest

from inventory_stream import MAX_LINE, ConflictError, InventoryError, InventoryStream


def line(**obj):
    return json.dumps(obj) + "\n"


class Parsing(unittest.TestCase):
    def test_arbitrary_fragment_splits(self):
        text = ('{"id": "e1", "kind": "stock", "sku": "A \\"q\\" \\n x", "qty": 5}\r\n'
                '\r\n   \n'
                '{"kind": "stock", "id": "e2", "sku": "B", "qty": 2}\n'
                '{"id": "e3", "kind": "move", "src": "B", "dst": "C", "qty": 2}\r\n'
                '{"id": "e4", "kind": "stock", "sku": "é", "qty": 1}')
        expected_snapshot = {'A "q" \n x': 5, "B": 0, "C": 2, "é": 1}
        rng = random.Random(3)
        for trial in range(300):
            s = InventoryStream()
            cuts = sorted(rng.sample(range(1, len(text)), rng.randint(0, 25)))
            pieces = [text[i:j] for i, j in zip([0] + cuts, cuts + [len(text)])]
            got = []
            for p in pieces:
                got.extend(s.feed(p))
            self.assertEqual(got, ["e1", "e2", "e3"], msg=pieces)
            self.assertEqual(s.finish(), ["e4"])
            self.assertEqual(s.finish(), [])
            self.assertEqual(s.snapshot(), expected_snapshot)

    def test_char_by_char(self):
        s = InventoryStream()
        text = line(id="a", kind="stock", sku="X", qty=3)
        out = []
        for ch in text:
            out.extend(s.feed(ch))
        self.assertEqual(out, ["a"])
        self.assertEqual(s.finish(), [])

    def test_crlf_split_between_cr_and_lf(self):
        s = InventoryStream()
        self.assertEqual(s.feed('{"id":"a","kind":"stock","sku":"X","qty":1}\r'), [])
        self.assertEqual(s.feed("\n"), ["a"])

    def test_finish_blank_and_repeat(self):
        s = InventoryStream()
        self.assertEqual(s.finish(), [])
        s.feed("   \r")
        self.assertEqual(s.finish(), [])
        s.feed('{"id":"a","kind":"stock","sku":"X","qty":1}')
        self.assertEqual(s.finish(), ["a"])
        self.assertEqual(s.finish(), [])
        # stream usable after finish
        self.assertEqual(s.feed(line(id="b", kind="stock", sku="X", qty=1)), ["b"])
        self.assertEqual(s.balance("X"), 2)

    def test_invalid_json_and_shapes(self):
        bad = [
            "{not json}", "[1,2]", '"str"', "42", "null",
            '{"id":"a","kind":"stock","sku":"X","qty":NaN}',
            '{"id":"a","kind":"stock","sku":"X","qty":1,"qty":2}',
            '{"id":"a","kind":"stock","sku":"X","qty":Infinity}',
        ]
        for text in bad:
            s = InventoryStream()
            with self.assertRaises(InventoryError, msg=text):
                s.feed(text + "\n")
            self.assertEqual(s.snapshot(), {})
            # stream continues
            self.assertEqual(s.feed(line(id="a", kind="stock", sku="X", qty=1)), ["a"])

    def test_non_str_fragment(self):
        with self.assertRaises(TypeError):
            InventoryStream().feed(b"{}\n")


class Validation(unittest.TestCase):
    def assert_invalid(self, obj):
        s = InventoryStream()
        s.feed(line(id="seed", kind="stock", sku="A", qty=10))
        before = s.snapshot()
        with self.assertRaises(InventoryError, msg=obj):
            s.feed(json.dumps(obj) + "\n")
        self.assertEqual(s.snapshot(), before)
        return s

    def test_invalid_fields(self):
        base_stock = {"id": "x", "kind": "stock", "sku": "A", "qty": 1}
        base_move = {"id": "x", "kind": "move", "src": "A", "dst": "B", "qty": 1}
        for key, vals in (("id", ["", 1, None, True]),
                          ("sku", ["", 3, None, ["A"]]),
                          ("qty", [0, -1, True, False, 1.0, "1", None]),
                          ("kind", ["STOCK", "", 1, None, ["stock"], "transfer"])):
            for v in vals:
                self.assert_invalid(dict(base_stock, **{key: v}))
        for key, vals in (("src", ["", 1]), ("dst", ["", None]), ("qty", [True, 0])):
            for v in vals:
                self.assert_invalid(dict(base_move, **{key: v}))
        self.assert_invalid(dict(base_move, dst="A"))  # distinct names
        for k in ("id", "kind", "sku", "qty"):
            self.assert_invalid({kk: vv for kk, vv in base_stock.items() if kk != k})
        self.assert_invalid(dict(base_stock, extra=1))
        self.assert_invalid(dict(base_move, sku="A"))

    def test_insufficient_stock(self):
        s = self.assert_invalid({"id": "m", "kind": "move", "src": "A", "dst": "B", "qty": 11})
        # failed id not recorded: same id can succeed later
        self.assertEqual(s.feed(line(id="m", kind="move", src="A", dst="B", qty=10)), ["m"])
        self.assertEqual(s.snapshot(), {"A": 0, "B": 10})
        with self.assertRaises(InventoryError):
            s.feed(line(id="m2", kind="move", src="Z", dst="B", qty=1))
        self.assertEqual(s.balance("Z"), 0)
        self.assertNotIn("Z", s.snapshot())


class Idempotency(unittest.TestCase):
    def test_identical_repeat_ignored_key_order_irrelevant(self):
        s = InventoryStream()
        self.assertEqual(s.feed('{"id":"a","kind":"stock","sku":"X","qty":2}\n'), ["a"])
        self.assertEqual(s.feed('{"qty":2, "sku":"X", "kind":"stock", "id":"a"}\n'), [])
        self.assertEqual(s.balance("X"), 2)

    def test_conflict(self):
        s = InventoryStream()
        s.feed(line(id="a", kind="stock", sku="X", qty=2))
        with self.assertRaises(ConflictError):
            s.feed(line(id="a", kind="stock", sku="X", qty=3))
        with self.assertRaises(ConflictError):
            s.feed(line(id="a", kind="move", src="X", dst="Y", qty=1))
        self.assertEqual(s.snapshot(), {"X": 2})
        # first accepted wins; original still recognized as duplicate
        self.assertEqual(s.feed(line(id="a", kind="stock", sku="X", qty=2)), [])
        self.assertTrue(issubclass(ConflictError, InventoryError))

    def test_duplicate_move_not_rechecked_for_stock(self):
        s = InventoryStream()
        s.feed(line(id="s", kind="stock", sku="X", qty=1))
        s.feed(line(id="m", kind="move", src="X", dst="Y", qty=1))
        self.assertEqual(s.feed(line(id="m", kind="move", src="X", dst="Y", qty=1)), [])
        self.assertEqual(s.snapshot(), {"X": 0, "Y": 1})


class FailureContinuation(unittest.TestCase):
    def test_failed_line_consumed_and_later_lines_continue(self):
        s = InventoryStream()
        chunk = (line(id="a", kind="stock", sku="X", qty=1)
                 + line(id="b", kind="move", src="X", dst="Y", qty=5)
                 + line(id="c", kind="stock", sku="Y", qty=1)
                 + '{"id":"d","kind":"stock",')
        with self.assertRaises(InventoryError) as cm:
            s.feed(chunk)
        self.assertEqual(cm.exception.applied, ["a"])
        self.assertEqual(s.snapshot(), {"X": 1})
        self.assertEqual(s.feed('"sku":"Z","qty":4}\n'), ["c", "d"])
        self.assertEqual(s.snapshot(), {"X": 1, "Y": 1, "Z": 4})

    def test_failing_final_line(self):
        s = InventoryStream()
        s.feed('{"id":"a","kind":"stock","sku":"X","qty":0}')
        with self.assertRaises(InventoryError):
            s.finish()
        self.assertEqual(s.finish(), [])
        self.assertEqual(s.snapshot(), {})

    def test_return_order(self):
        s = InventoryStream()
        ids = ["z", "a", "m", "b"]
        text = "".join(line(id=i, kind="stock", sku=i, qty=1) for i in ids)
        self.assertEqual(s.feed(text), ids)
        snap = s.snapshot()
        snap["z"] = 99
        self.assertEqual(s.balance("z"), 1)


class Batch(unittest.TestCase):
    """REQ-C1."""

    def batch(self, id, ops):
        return json.dumps({"id": id, "kind": "batch", "ops": ops}) + "\n"

    def test_later_ops_consume_earlier_stock(self):
        s = InventoryStream()
        ops = [{"kind": "stock", "sku": "A", "qty": 5},
               {"kind": "move", "src": "A", "dst": "B", "qty": 5},
               {"kind": "move", "src": "B", "dst": "C", "qty": 2}]
        self.assertEqual(s.feed(self.batch("b1", ops)), ["b1"])
        self.assertEqual(s.snapshot(), {"A": 0, "B": 3, "C": 2})

    def test_rollback_and_id_not_reserved(self):
        s = InventoryStream()
        s.feed(line(id="s", kind="stock", sku="A", qty=1))
        bad_ops = [
            [{"kind": "stock", "sku": "A", "qty": 5},
             {"kind": "move", "src": "A", "dst": "B", "qty": 7}],         # insufficient
            [{"kind": "stock", "sku": "A", "qty": 5}, {"kind": "stock", "sku": "A", "qty": 0}],
            [{"kind": "stock", "sku": "A", "qty": 5}, {"id": "x", "kind": "stock", "sku": "A", "qty": 1}],
            [{"kind": "batch", "ops": []}],                                   # nested
            [{"kind": "stock", "sku": "A", "qty": 5}, "notadict"],
            [{"kind": "move", "src": "A", "dst": "A", "qty": 1}],
        ]
        for ops in bad_ops:
            with self.assertRaises(InventoryError, msg=ops):
                s.feed(self.batch("b", ops))
            self.assertEqual(s.snapshot(), {"A": 1})
        for obj in ({"id": "b", "kind": "batch", "ops": {}},
                    {"id": "b", "kind": "batch"},
                    {"id": "b", "kind": "batch", "ops": [], "sku": "A"}):
            with self.assertRaises(InventoryError, msg=obj):
                s.feed(json.dumps(obj) + "\n")
        # corrected retry with the same id succeeds
        good = [{"kind": "stock", "sku": "A", "qty": 5},
                {"kind": "move", "src": "A", "dst": "B", "qty": 6}]
        self.assertEqual(s.feed(self.batch("b", good)), ["b"])
        self.assertEqual(s.snapshot(), {"A": 0, "B": 6})

    def test_empty_ops_records_id(self):
        s = InventoryStream()
        self.assertEqual(s.feed(self.batch("e", [])), ["e"])
        self.assertEqual(s.snapshot(), {})
        self.assertEqual(s.feed(self.batch("e", [])), [])
        with self.assertRaises(ConflictError):
            s.feed(self.batch("e", [{"kind": "stock", "sku": "A", "qty": 1}]))
        with self.assertRaises(ConflictError):
            s.feed(line(id="e", kind="stock", sku="A", qty=1))
        self.assertEqual(s.snapshot(), {})

    def test_repeat_and_conflict(self):
        s = InventoryStream()
        ops = [{"kind": "stock", "sku": "A", "qty": 2}, {"kind": "move", "src": "A", "dst": "B", "qty": 1}]
        self.assertEqual(s.feed(self.batch("b", ops)), ["b"])
        # key order irrelevant inside ops, but op order matters
        reordered = '{"ops":[{"qty":2,"sku":"A","kind":"stock"},{"dst":"B","qty":1,"src":"A","kind":"move"}],"kind":"batch","id":"b"}\n'
        self.assertEqual(s.feed(reordered), [])
        with self.assertRaises(ConflictError):
            s.feed(self.batch("b", list(reversed(ops))))
        self.assertEqual(s.snapshot(), {"A": 1, "B": 1})

    def test_batch_kind_must_be_string(self):
        s = InventoryStream()
        with self.assertRaises(InventoryError):
            s.feed('{"id":"b","kind":["batch"],"ops":[]}\n')

    def test_batch_id_shared_namespace(self):
        s = InventoryStream()
        s.feed(line(id="x", kind="stock", sku="A", qty=1))
        with self.assertRaises(ConflictError):
            s.feed(self.batch("x", []))


class Undo(unittest.TestCase):
    """REQ-C2."""

    def test_undo_stock_and_move(self):
        s = InventoryStream()
        s.feed(line(id="s1", kind="stock", sku="A", qty=5))
        s.feed(line(id="m1", kind="move", src="A", dst="B", qty=2))
        s.undo("m1")
        self.assertEqual(s.snapshot(), {"A": 5, "B": 0})
        s.undo("s1")
        self.assertEqual(s.snapshot(), {"A": 0, "B": 0})

    def test_unknown_and_double_undo(self):
        s = InventoryStream()
        with self.assertRaises(InventoryError):
            s.undo("nope")
        with self.assertRaises(InventoryError):
            s.undo(["unhashable"])
        s.feed(line(id="s1", kind="stock", sku="A", qty=5))
        s.undo("s1")
        with self.assertRaises(InventoryError):
            s.undo("s1")
        # failed (never accepted) events are unknown
        with self.assertRaises(InventoryError):
            s.feed(line(id="bad", kind="move", src="A", dst="B", qty=1))
        with self.assertRaises(InventoryError):
            s.undo("bad")

    def test_negative_fails_unchanged_then_retry(self):
        s = InventoryStream()
        s.feed(line(id="s1", kind="stock", sku="A", qty=5))
        s.feed(line(id="m1", kind="move", src="A", dst="B", qty=4))
        with self.assertRaises(InventoryError):
            s.undo("s1")  # A would be 1 - 5
        self.assertEqual(s.snapshot(), {"A": 1, "B": 4})
        s.undo("m1")      # unrelated/independent undo
        s.undo("s1")      # retry now succeeds
        self.assertEqual(s.snapshot(), {"A": 0, "B": 0})

    def test_batch_uses_net_delta(self):
        s = InventoryStream()
        # A: +3 -3 = 0 net; B: +3 -1 = +2; C: +1.  Reversing ops one by one in
        # reverse order would be fine too, but net matters when A is drained.
        ops = [{"kind": "stock", "sku": "A", "qty": 3},
               {"kind": "move", "src": "A", "dst": "B", "qty": 3},
               {"kind": "move", "src": "B", "dst": "C", "qty": 1}]
        s.feed(json.dumps({"id": "b", "kind": "batch", "ops": ops}) + "\n")
        self.assertEqual(s.snapshot(), {"A": 0, "B": 2, "C": 1})
        s.undo("b")
        self.assertEqual(s.snapshot(), {"A": 0, "B": 0, "C": 0})

    def test_net_delta_not_reverse_op_order(self):
        s = InventoryStream()
        s.feed(line(id="seed", kind="stock", sku="B", qty=1))
        # Batch: move B->A 1, then stock B 1  => net B: 0, A: +1
        ops = [{"kind": "move", "src": "B", "dst": "A", "qty": 1},
               {"kind": "stock", "sku": "B", "qty": 1}]
        s.feed(json.dumps({"id": "b", "kind": "batch", "ops": ops}) + "\n")
        s.feed(line(id="drain", kind="move", src="B", dst="C", qty=1))
        self.assertEqual(s.snapshot(), {"A": 1, "B": 0, "C": 1})
        # Reverse-op replay would first un-stock B (B=-1) and fail; net works.
        s.undo("b")
        self.assertEqual(s.snapshot(), {"A": 0, "B": 0, "C": 1})

    def test_batch_undo_negative_atomic(self):
        s = InventoryStream()
        ops = [{"kind": "stock", "sku": "A", "qty": 2}, {"kind": "stock", "sku": "B", "qty": 2}]
        s.feed(json.dumps({"id": "b", "kind": "batch", "ops": ops}) + "\n")
        s.feed(line(id="m", kind="move", src="B", dst="C", qty=1))
        with self.assertRaises(InventoryError):
            s.undo("b")
        self.assertEqual(s.snapshot(), {"A": 2, "B": 1, "C": 1})

    def test_empty_batch_undo(self):
        s = InventoryStream()
        s.feed('{"id":"e","kind":"batch","ops":[]}\n')
        s.undo("e")
        with self.assertRaises(InventoryError):
            s.undo("e")

    def test_tombstone(self):
        s = InventoryStream()
        s.feed(line(id="s1", kind="stock", sku="A", qty=5))
        s.undo("s1")
        self.assertEqual(s.feed('{"qty":5,"sku":"A","kind":"stock","id":"s1"}\n'), [])
        self.assertEqual(s.balance("A"), 0)
        with self.assertRaises(ConflictError):
            s.feed(line(id="s1", kind="stock", sku="A", qty=6))
        self.assertEqual(s.snapshot(), {"A": 0})
        with self.assertRaises(InventoryError):
            s.undo("s1")

    def test_no_auto_undo_of_later_events(self):
        s = InventoryStream()
        s.feed(line(id="s1", kind="stock", sku="A", qty=5))
        s.feed(line(id="s2", kind="stock", sku="A", qty=1))
        s.feed(line(id="m", kind="move", src="A", dst="B", qty=1))
        s.undo("s1")
        self.assertEqual(s.snapshot(), {"A": 0, "B": 1})
        s.undo("m")
        self.assertEqual(s.snapshot(), {"A": 1, "B": 0})


class CheckpointAndLineBound(unittest.TestCase):
    """REQ-C3."""

    def test_restore_roundtrip(self):
        s = InventoryStream()
        s.feed(line(id="s1", kind="stock", sku="A", qty=5))
        s.feed(line(id="s2", kind="stock", sku="B", qty=2))
        s.undo("s2")
        s.feed('{"id":"m1","kind":"move","src":"A",')  # unfinished
        cp = s.checkpoint()
        self.assertEqual(s.feed('"dst":"C","qty":1}\n'), ["m1"])
        s.feed(line(id="x", kind="stock", sku="Z", qty=9))
        s.restore(cp)
        self.assertEqual(s.snapshot(), {"A": 5, "B": 0})
        self.assertEqual(s.feed('"dst":"C","qty":1}\n'), ["m1"])
        self.assertEqual(s.feed(line(id="s1", kind="stock", sku="A", qty=5)), [])
        self.assertEqual(s.feed(line(id="s2", kind="stock", sku="B", qty=2)), [])
        with self.assertRaises(ConflictError):
            s.feed(line(id="s2", kind="stock", sku="B", qty=3))
        with self.assertRaises(InventoryError):
            s.undo("s2")
        self.assertEqual(s.feed(line(id="x", kind="stock", sku="Z", qty=1)), ["x"])
        s.undo("m1")
        self.assertEqual(s.snapshot(), {"A": 5, "B": 0, "C": 0, "Z": 1})
        # checkpoint reusable and unaffected by later mutation
        s.restore(cp)
        self.assertEqual(s.snapshot(), {"A": 5, "B": 0})
        s.undo("s1")
        self.assertEqual(s.snapshot(), {"A": 0, "B": 0})
        s.restore(cp)
        with self.assertRaises(InventoryError):
            s.finish()  # the restored unfinished fragment is incomplete JSON
        self.assertEqual(s.finish(), [])

    def test_checkpoint_foreign_invalid_immutable(self):
        s, other = InventoryStream(), InventoryStream()
        cp = s.checkpoint()
        with self.assertRaises(AttributeError):
            cp._state = None
        for bad in (other.checkpoint(), None, "cp", {}, object()):
            with self.assertRaises(InventoryError):
                s.restore(bad)

    def test_line_bound_code_points(self):
        def stock_line(total_len, sku_char="é"):
            base = '{"id":"a","kind":"stock","sku":"","qty":1}'
            return base.replace('"sku":""', '"sku":"%s"' % (sku_char * (total_len - len(base))))
        ok, too_long = stock_line(MAX_LINE), stock_line(MAX_LINE + 1)
        self.assertEqual((len(ok), len(too_long)), (4096, 4097))
        self.assertGreater(len(ok.encode("utf-8")), 4096)  # bytes don't matter
        s = InventoryStream()
        self.assertEqual(s.feed(ok + "\r\n"), ["a"])
        s2 = InventoryStream()
        with self.assertRaises(InventoryError):
            s2.feed(too_long + "\n")
        # unfinished line also bounded, across fragments
        s3 = InventoryStream()
        s3.feed(too_long[:3000])
        with self.assertRaises(InventoryError):
            s3.feed(too_long[3000:])
        self.assertEqual(s3.feed(ok[3000:] + "\n"), ["a"])  # buffer kept as before

    def test_overlong_feed_rolls_back_whole_call(self):
        s = InventoryStream()
        s.feed(line(id="s0", kind="stock", sku="A", qty=1))
        s.feed('{"id":"p","kind":"stock",')
        chunk = ('"sku":"P","qty":1}\n'
                 + line(id="s1", kind="stock", sku="A", qty=5)
                 + line(id="m", kind="move", src="A", dst="B", qty=2)
                 + "x" * (MAX_LINE + 1))
        with self.assertRaises(InventoryError):
            s.feed(chunk)
        self.assertEqual(s.snapshot(), {"A": 1})
        with self.assertRaises(InventoryError):
            s.undo("s1")  # never recorded
        # buffer unchanged: previous partial line still completes
        self.assertEqual(s.feed('"sku":"P","qty":1}\n'), ["p"])
        self.assertEqual(s.feed(line(id="s1", kind="stock", sku="A", qty=5)), ["s1"])


class RandomizedModel(unittest.TestCase):
    def test_random_streams_vs_model(self):
        rng = random.Random(11)
        skus = ["A", "B", "C"]
        for trial in range(80):
            events = []
            for i in range(30):
                eid = "e%d" % rng.randint(0, 20)
                if rng.random() < 0.5:
                    events.append({"id": eid, "kind": "stock", "sku": rng.choice(skus),
                                   "qty": rng.randint(1, 5)})
                else:
                    a, b = rng.sample(skus, 2)
                    events.append({"id": eid, "kind": "move", "src": a, "dst": b,
                                   "qty": rng.randint(1, 6)})
            # model
            bal, seen, outcomes = {}, {}, []
            for ev in events:
                if ev["id"] in seen:
                    outcomes.append("dup" if seen[ev["id"]] == ev else "conflict")
                    continue
                if ev["kind"] == "move" and bal.get(ev["src"], 0) < ev["qty"]:
                    outcomes.append("insufficient")
                    continue
                if ev["kind"] == "stock":
                    bal[ev["sku"]] = bal.get(ev["sku"], 0) + ev["qty"]
                else:
                    bal[ev["src"]] -= ev["qty"]
                    bal[ev["dst"]] = bal.get(ev["dst"], 0) + ev["qty"]
                seen[ev["id"]] = ev
                outcomes.append("ok")
            # feed one event per call, split in random halves
            s = InventoryStream()
            for ev, outcome in zip(events, outcomes):
                text = json.dumps(ev) + rng.choice(["\n", "\r\n"])
                cut = rng.randint(0, len(text) - 1)  # last char (LF) in 2nd piece
                self.assertEqual(s.feed(text[:cut]), [])
                if outcome == "ok":
                    self.assertEqual(s.feed(text[cut:]), [ev["id"]])
                elif outcome == "dup":
                    self.assertEqual(s.feed(text[cut:]), [])
                else:
                    exc = ConflictError if outcome == "conflict" else InventoryError
                    with self.assertRaises(exc) as cm:
                        s.feed(text[cut:])
                    if outcome == "insufficient":
                        self.assertNotIsInstance(cm.exception, ConflictError)
            self.assertEqual(s.snapshot(), bal)


if __name__ == "__main__":
    unittest.main()

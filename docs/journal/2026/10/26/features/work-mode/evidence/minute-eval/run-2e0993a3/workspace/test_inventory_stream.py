import json
import unittest
from inventory_stream import ConflictError, InventoryError, InventoryStream


def stock(id="s", sku="a", qty=3):
    return {"id": id, "kind": "stock", "sku": sku, "qty": qty}


def line(event):
    return json.dumps(event) + "\n"


class StreamTests(unittest.TestCase):
    def test_every_fragment_boundary_crlf_and_finish(self):
        text = line(stock(sku='a"\\\nΩ')).replace("\n", "\r\n")
        for boundary in range(len(text) + 1):
            stream = InventoryStream()
            ids = stream.feed(text[:boundary]) + stream.feed(text[boundary:])
            self.assertEqual(ids, ["s"])
            self.assertEqual(stream.balance('a"\\\nΩ'), 3)
        stream = InventoryStream()
        self.assertEqual(stream.feed(json.dumps(stock())), [])
        self.assertEqual(stream.finish(), ["s"])
        self.assertEqual(stream.finish(), [])

    def test_move_zero_balance_and_copy(self):
        stream = InventoryStream()
        self.assertEqual(stream.feed(line(stock()) + line({"id": "m", "kind": "move", "src": "a", "dst": "b", "qty": 3})), ["s", "m"])
        self.assertEqual(stream.snapshot(), {"a": 0, "b": 3})
        snapshot = stream.snapshot()
        snapshot["a"] = 99
        self.assertEqual(stream.balance("a"), 0)
        self.assertEqual(stream.balance("unknown"), 0)
        self.assertNotIn("unknown", stream.snapshot())

    def test_deduplication_conflict_and_recovery(self):
        stream = InventoryStream()
        stream.feed(line(stock()))
        self.assertEqual(stream.feed('{"qty":3,"sku":"a","kind":"stock","id":"s"}\n'), [])
        with self.assertRaises(ConflictError): stream.feed(line(stock(qty=4)))
        with self.assertRaises(InventoryError): stream.feed('bad json\n' + line(stock("next")))
        self.assertEqual(stream.feed(""), ["next"])
        self.assertEqual(stream.balance("a"), 6)

    def test_invalid_is_atomic(self):
        stream = InventoryStream()
        for event in [stock(qty=True), stock(sku=""), {"id": "s", "kind": "move", "src": "a", "dst": "b", "qty": 1}, [], {"id": "s", "kind": "unknown"}]:
            with self.assertRaises(InventoryError): stream.feed(line(event))
            self.assertEqual(stream.snapshot(), {})
        self.assertEqual(stream.feed(line(stock())), ["s"])

    def test_c1_batch_order_rollback_retry_and_empty(self):
        stream = InventoryStream()
        batch = {"id": "batch", "kind": "batch", "ops": [
            {"kind": "stock", "sku": "a", "qty": 5},
            {"kind": "move", "src": "a", "dst": "b", "qty": 6}]}
        with self.assertRaises(InventoryError): stream.feed(line(batch))
        self.assertEqual(stream.snapshot(), {})
        batch["ops"][1]["qty"] = 5
        self.assertEqual(stream.feed(line(batch)), ["batch"])
        self.assertEqual(stream.snapshot(), {"a": 0, "b": 5})
        self.assertEqual(stream.feed(line(batch)), [])
        empty = {"id": "empty", "kind": "batch", "ops": []}
        self.assertEqual(stream.feed(line(empty)), ["empty"])
        with self.assertRaises(InventoryError):
            stream.feed(line({"id": "nested", "kind": "batch", "ops": [{"kind": "batch", "ops": []}]}))

    def test_c2_undo_net_delta_retry_and_tombstone(self):
        stream = InventoryStream()
        batch = {"id": "b", "kind": "batch", "ops": [
            {"kind": "stock", "sku": "a", "qty": 5},
            {"kind": "move", "src": "a", "dst": "b", "qty": 5},
            {"kind": "move", "src": "b", "dst": "a", "qty": 2}]}
        stream.feed(line(stock("other", "z", 1)) + line(batch))
        self.assertEqual(stream.snapshot(), {"z": 1, "a": 2, "b": 3})
        stream.feed(line({"id": "drain", "kind": "move", "src": "b", "dst": "c", "qty": 3}))
        with self.assertRaises(InventoryError): stream.undo("b")
        self.assertEqual(stream.snapshot(), {"z": 1, "a": 2, "b": 0, "c": 3})
        stream.undo("drain")
        stream.undo("b")
        self.assertEqual(stream.snapshot(), {"z": 1, "a": 0, "b": 0, "c": 0})
        with self.assertRaises(InventoryError): stream.undo("b")
        with self.assertRaises(InventoryError): stream.undo("missing")
        self.assertEqual(stream.feed(line(batch)), [])
        with self.assertRaises(ConflictError): stream.feed(line(stock("b")))
        self.assertEqual(stream.balance("a"), 0)
        stream.undo("other")
        self.assertEqual(stream.balance("z"), 0)

    def test_c3_checkpoint_restores_buffer_idempotency_and_undo(self):
        stream = InventoryStream()
        stream.feed(line(stock("s1", "a", 4)) + line(stock("s2", "a", 1)))
        stream.undo("s2")
        partial = line(stock("s3", "a", 2))
        stream.feed(partial[:10])
        checkpoint = stream.checkpoint()
        with self.assertRaises(AttributeError): checkpoint._state = None
        self.assertEqual(stream.feed(partial[10:]), ["s3"])
        stream.undo("s1")
        stream.feed(line(stock("s4", "b", 9)))
        stream.restore(checkpoint)
        self.assertEqual(stream.snapshot(), {"a": 4})
        self.assertEqual(stream.feed(partial[10:]), ["s3"])
        self.assertEqual(stream.feed(line(stock("s1", "a", 4))), [])
        with self.assertRaises(InventoryError): stream.undo("s2")
        stream.restore(checkpoint)
        self.assertEqual(stream.feed(partial[10:] + line(stock("s4", "b", 9))), ["s3", "s4"])
        self.assertEqual(stream.snapshot(), {"a": 6, "b": 9})
        for foreign in (InventoryStream().checkpoint(), None, "checkpoint"):
            with self.assertRaises(InventoryError): stream.restore(foreign)

    def test_c3_line_bound_code_points_and_feed_rollback(self):
        stream = InventoryStream()
        prefix = '{"id":"x","kind":"stock","qty":1,"sku":"'
        exact = prefix + "Ω" * (4096 - len(prefix) - 2) + '"}'
        self.assertEqual(len(exact), 4096)
        self.assertEqual(stream.feed(exact + "\r\n"), ["x"])
        stream.feed("  ")
        before = stream.checkpoint()
        with self.assertRaises(InventoryError):
            stream.feed(line(stock("early")) + "x" * 4097)
        self.assertEqual(stream.feed(line(stock("early"))), ["early"])
        stream.restore(before)
        with self.assertRaises(InventoryError): stream.feed("y" * 4095)
        self.assertEqual(stream.feed("\n" + line(stock("after"))), ["after"])
        self.assertEqual(stream.feed("z" * 4096), [])
        with self.assertRaises(InventoryError): stream.feed("z")


if __name__ == "__main__":
    unittest.main()

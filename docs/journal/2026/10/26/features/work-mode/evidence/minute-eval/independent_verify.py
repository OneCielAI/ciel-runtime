#!/usr/bin/env python3
"""Independent public-API verifier authored ONLY from requests.json.
Usage: python -I independent_verify.py RUN_DIR
"""
import collections.abc
import hashlib
import importlib
import io
import itertools
import json
import pathlib
import random
import sys
import time
import traceback
import unittest

MODULES = {}
IMPORT_ERRORS = {}
SPEC_IDS = tuple(f"{b}{n}" for b in "ABC" for n in range(4))


def covers(*ids):
    def decorate(fn):
        fn.requirements = ids
        return fn
    return decorate


def event(identifier, kind="stock", **fields):
    return dict(id=identifier, kind=kind, **fields)


def line(payload):
    return json.dumps(payload, ensure_ascii=False) + "\n"


class ContractCase(unittest.TestCase):
    branch = ""

    def setUp(self):
        name = {"A": "routing_graph", "B": "booking_calendar", "C": "inventory_stream"}[self.branch]
        if name in IMPORT_ERRORS:
            self.fail("Import failed: " + IMPORT_ERRORS[name])
        self.m = MODULES[name]

    def immutable(self, value, seen=None):
        """Probe exposed value protocols, not private checkpoint representation."""
        seen = set() if seen is None else seen
        if id(value) in seen:
            return
        seen.add(id(value))
        if isinstance(value, (str, bytes, int, float, complex, bool, type(None))):
            return
        if isinstance(value, collections.abc.Mapping):
            self.assertNotIsInstance(value, collections.abc.MutableMapping)
            for key, child in value.items():
                if hasattr(value, "__setitem__"):
                    with self.assertRaises((TypeError, AttributeError)):
                        value[key] = child
                self.immutable(child, seen)
        elif isinstance(value, collections.abc.Sequence):
            self.assertNotIsInstance(value, collections.abc.MutableSequence)
            for index, child in enumerate(value):
                if hasattr(value, "__setitem__"):
                    with self.assertRaises((TypeError, AttributeError)):
                        value[index] = child
                self.immutable(child, seen)
        elif isinstance(value, collections.abc.Set):
            self.assertNotIsInstance(value, collections.abc.MutableSet)
            for child in value:
                self.immutable(child, seen)
        else:
            for name in dir(value):
                if name.startswith("_"):
                    continue
                child = getattr(value, name)
                if callable(child):
                    continue
                with self.assertRaises((AttributeError, TypeError)):
                    setattr(value, name, child)
                self.immutable(child, seen)
        with self.assertRaises((AttributeError, TypeError)):
            setattr(value, "independent_verifier_probe", True)


class GraphContract(ContractCase):
    branch = "A"

    def graph(self, names="s a b z", edges=(), **kwargs):
        g = self.m.Graph(**kwargs)
        for name in names.split():
            g.add_node(name)
        for src, dst, cost in edges:
            g.set_edge(src, dst, cost)
        return g

    def state(self, g, names=("s", "a", "b", "z", "new")):
        out = {}
        for src, dst in itertools.product(names, repeat=2):
            try:
                out[src, dst] = g.shortest(src, dst)
            except (self.m.NoPathError, ValueError) as exc:
                out[src, dst] = type(exc).__name__
        return g.version, out

    @covers("A0", "A3")
    def test_final_default_and_explicit_original_tie(self):
        edges = [("s", "z", 5), ("s", "b", 2), ("b", "z", 3), ("s", "a", 1), ("a", "b", 1)]
        default = self.graph(edges=edges)
        lexical = self.graph(edges=edges, tie_policy="lexical")
        original = self.graph(edges=edges, tie_policy="fewest_hops")
        self.assertEqual(default.shortest("s", "z"), (5, ("s", "a", "b", "z")))
        self.assertEqual(lexical.shortest("s", "z"), default.shortest("s", "z"))
        self.assertEqual(original.shortest("s", "z"), (5, ("s", "z")))
        original.remove_edge("s", "z")
        original.set_edge("a", "z", 4)
        self.assertEqual(original.shortest("s", "z"), (5, ("s", "a", "z")))
        for policy in ("lexical", "fewest_hops"):
            g = self.graph(edges=[("s", "z", 8), ("s", "b", 0), ("b", "z", 1)], tie_policy=policy)
            self.assertEqual(g.shortest("s", "z"), (1, ("s", "b", "z")))
            self.assertEqual(g.shortest("z", "z"), (0, ("z",)))
            with self.assertRaises(self.m.NoPathError):
                g.shortest("z", "s")

    @covers("A0", "A1", "A3")
    def test_simple_paths_zero_cycles(self):
        g = self.graph(edges=[("s", "a", 0), ("a", "s", 0), ("a", "b", 0),
                              ("b", "a", 0), ("b", "b", 0), ("a", "z", 1), ("b", "z", 1)])
        self.assertEqual(g.shortest("s", "z"), (1, ("s", "a", "b", "z")))
        self.assertEqual(g.shortest("s", "z", max_hops=2), (1, ("s", "a", "z")))
        self.assertEqual(g.shortest("s", "s"), (0, ("s",)))

    @covers("A0", "A3")
    def test_validation_and_atomic_errors(self):
        for policy in (None, "", "LEXICAL", "original", False, 0, []):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                self.m.Graph(tie_policy=policy)
        g = self.graph()
        before = self.state(g)
        for name in ("", None, 1, True, [], {}):
            with self.subTest(name=name), self.assertRaises(ValueError):
                g.add_node(name)
        for cost in (-1, True, False, 1.5, "1", None):
            with self.subTest(cost=cost), self.assertRaises(ValueError):
                g.set_edge("s", "z", cost)
        for src, dst in (("missing", "z"), ("s", "missing")):
            with self.assertRaises(ValueError):
                g.set_edge(src, dst, 1)
            with self.assertRaises(ValueError):
                g.shortest(src, dst)
        self.assertEqual(self.state(g), before)

    @covers("A0")
    def test_mutation_versions_noops_incident_edges(self):
        g = self.m.Graph()
        self.assertEqual(g.version, 0)
        for expected, name in enumerate(("s", "a", "z"), 1):
            g.add_node(name)
            self.assertEqual(g.version, expected)
        g.add_node("s")
        self.assertEqual(g.version, 3)
        g.set_edge("s", "a", 2)
        self.assertEqual(g.version, 4)
        g.set_edge("s", "a", 2)
        self.assertEqual(g.version, 4)
        g.set_edge("s", "a", 0)
        self.assertEqual(g.version, 5)
        g.set_edge("a", "z", 1)
        g.set_edge("z", "a", 2)
        g.set_edge("a", "a", 0)
        self.assertEqual(g.shortest("s", "z"), (1, ("s", "a", "z")))
        v = g.version
        self.assertTrue(g.remove_edge("s", "a"))
        self.assertEqual(g.version, v + 1)
        self.assertFalse(g.remove_edge("s", "a"))
        self.assertEqual(g.version, v + 1)
        self.assertTrue(g.remove_node("a"))
        self.assertEqual(g.version, v + 2)
        self.assertFalse(g.remove_node("a"))
        self.assertEqual(g.version, v + 2)
        g.add_node("a")
        for src, dst in (("a", "z"), ("z", "a")):
            with self.assertRaises(self.m.NoPathError):
                g.shortest(src, dst)

    @covers("A1", "A3")
    def test_blocking_and_hop_bound_before_minimum_selection(self):
        g = self.graph(edges=[("s", "a", 0), ("a", "b", 0), ("b", "z", 1), ("s", "b", 4), ("s", "z", 9)])
        self.assertEqual(g.shortest("s", "z"), (1, ("s", "a", "b", "z")))
        self.assertEqual(g.shortest("s", "z", max_hops=2), (5, ("s", "b", "z")))
        self.assertEqual(g.shortest("s", "z", max_hops=1), (9, ("s", "z")))
        self.assertEqual(g.shortest("s", "z", blocked=("b",)), (9, ("s", "z")))
        self.assertEqual(g.shortest("s", "s", max_hops=0), (0, ("s",)))
        for kwargs in ({"max_hops": 0}, {"blocked": ["s"]}, {"blocked": ["z"]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(self.m.NoPathError):
                g.shortest("s", "z", **kwargs)
        with self.assertRaises(self.m.NoPathError):
            g.shortest("s", "s", blocked=("s",))
        v = g.version
        for hops in (-1, True, False, 1.5, "2", []):
            with self.subTest(hops=hops), self.assertRaises(ValueError):
                g.shortest("s", "z", max_hops=hops)
        with self.assertRaises(ValueError):
            g.shortest("s", "z", blocked=("missing",))
        self.assertEqual(g.version, v)

    @covers("A2", "A3", "A1")
    def test_snapshot_freezes_data_version_and_policy(self):
        for policy, expected in (("lexical", (2, ("s", "a", "z"))), ("fewest_hops", (2, ("s", "z")))):
            with self.subTest(policy=policy):
                g = self.graph(edges=[("s", "z", 2), ("s", "a", 0), ("a", "z", 2)], tie_policy=policy)
                snap = g.snapshot()
                v = g.version
                g.set_edge("s", "z", 1)
                g.remove_node("a")
                self.assertEqual(snap.version, v)
                self.assertEqual(snap.shortest("s", "z"), expected)
                self.assertEqual(snap.shortest("s", "z", max_hops=1), (2, ("s", "z")))
                self.assertEqual(snap.shortest("s", "z", blocked=("a",)), (2, ("s", "z")))
                with self.assertRaises(self.m.NoPathError):
                    snap.shortest("s", "s", blocked=("s",))
                with self.assertRaises(ValueError):
                    snap.shortest("s", "z", blocked=("missing",))
                for name, args in (("add_node", ("new",)), ("set_edge", ("s", "z", 0)),
                                   ("remove_edge", ("s", "z")), ("remove_node", ("s",)),
                                   ("apply", ([{"op": "add_node", "name": "new"}],))):
                    method = getattr(snap, name, None)
                    if method is not None:
                        try:
                            method(*args)
                        except Exception:
                            pass
                        else:
                            self.fail("read-only snapshot accepted mutation: " + name)
                self.assertEqual(snap.shortest("s", "z"), expected)
                self.assertEqual(snap.version, v)

    @covers("A2", "A0")
    def test_apply_order_atomicity_and_single_version(self):
        g = self.graph(names="s z")
        v = g.version
        g.apply([{"op": "add_node", "name": "a"},
                 {"op": "set_edge", "src": "s", "dst": "a", "cost": 0},
                 {"op": "set_edge", "src": "a", "dst": "z", "cost": 4}], expected_version=v)
        self.assertEqual(g.version, v + 1)
        self.assertEqual(g.shortest("s", "z"), (4, ("s", "a", "z")))
        g.apply([{"op": "remove_edge", "src": "s", "dst": "a"},
                 {"op": "set_edge", "src": "s", "dst": "z", "cost": 8}, {"op": "remove_node", "name": "a"}])
        self.assertEqual(g.version, v + 2)
        self.assertEqual(g.shortest("s", "z"), (8, ("s", "z")))
        with self.assertRaises(ValueError):
            g.shortest("a", "z")
        before = self.state(g)
        for bad in ({"op": "unknown"}, {"op": "set_edge", "src": "s", "dst": "absent", "cost": 1},
                    {"op": "set_edge", "src": "s", "dst": "z", "cost": True}, {"op": "add_node", "name": ""}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                g.apply([{"op": "add_node", "name": "new"}, {"op": "remove_node", "name": "s"}, bad])
            self.assertEqual(self.state(g), before)

    @covers("A2")
    def test_apply_conflict_precedes_ops_and_noops(self):
        g = self.graph()
        v = g.version
        with self.assertRaises(self.m.ConflictError):
            g.apply([{"op": "unknown"}], expected_version=v - 1)
        self.assertEqual(g.version, v)
        g.apply([], expected_version=v)
        g.apply([{"op": "add_node", "name": "s"}, {"op": "remove_edge", "src": "s", "dst": "z"},
                 {"op": "remove_node", "name": "missing"}], expected_version=v)
        self.assertEqual(g.version, v)
        g.set_edge("s", "z", 1)
        g.apply([{"op": "set_edge", "src": "s", "dst": "z", "cost": 1}])
        self.assertEqual(g.version, v + 1)

    @covers("A0", "A1", "A2", "A3")
    def test_exhaustive_simple_path_oracle(self):
        rng = random.Random(470101)
        names = ("s", "a", "aa", "b", "z")
        for sample in range(12):
            edges = {(a, b): rng.randrange(4) for a in names for b in names if rng.random() < .47}
            for policy in ("lexical", "fewest_hops"):
                g = self.graph(names=" ".join(names), edges=[(*e, c) for e, c in edges.items()], tie_policy=policy)
                for view in (g, g.snapshot()):
                    for src, dst in itertools.product(names, repeat=2):
                        for blocked in ((), ("aa",)):
                            for bound in (None, 0, 1, 3):
                                candidates = []
                                def visit(path, cost):
                                    if path[-1] == dst:
                                        candidates.append((cost, tuple(path)))
                                        return
                                    if bound is not None and len(path) - 1 >= bound:
                                        return
                                    for node in names:
                                        if node not in path and node not in blocked and (path[-1], node) in edges:
                                            visit(path + [node], cost + edges[path[-1], node])
                                if src not in blocked and dst not in blocked:
                                    visit([src], 0)
                                with self.subTest(sample=sample, policy=policy, src=src, dst=dst,
                                                  blocked=blocked, bound=bound, snapshot=view is not g):
                                    if not candidates:
                                        with self.assertRaises(self.m.NoPathError):
                                            view.shortest(src, dst, blocked=blocked, max_hops=bound)
                                    else:
                                        key = ((lambda x: (x[0], x[1], len(x[1]))) if policy == "lexical"
                                               else (lambda x: (x[0], len(x[1]), x[1])))
                                        self.assertEqual(view.shortest(src, dst, blocked=blocked, max_hops=bound),
                                                         min(candidates, key=key))


class CalendarContract(ContractCase):
    branch = "B"

    def state(self, c):
        return c.version, c.allocations(), c.waiting()

    def atomic_failure(self, c, exception, fn):
        before = self.state(c)
        with self.assertRaises(exception):
            fn()
        self.assertEqual(self.state(c), before)

    @covers("B0", "B3")
    def test_capacity_half_open_negatives_sort_and_copy(self):
        c = self.m.Calendar(capacity=3)
        self.assertEqual(c.version, 0)
        c.reserve("z", -10, 0, 2)
        c.reserve("b", -10, 0)
        c.reserve("a", 0, 10, 3)
        self.assertEqual(c.allocations(), [("b", -10, 0, 1), ("z", -10, 0, 2), ("a", 0, 10, 3)])
        self.assertFalse(c.available(-1, 1))
        self.assertTrue(c.available(10, 20, 3))
        self.assertTrue(c.available(-20, -10, 3))
        self.assertFalse(c.available(-20, 20, 4))
        self.assertEqual(c.version, 3)
        self.atomic_failure(c, self.m.CapacityError, lambda: c.reserve("extra", -2, 2))
        self.atomic_failure(c, ValueError, lambda: c.reserve("b", 20, 30))
        returned = c.allocations()
        returned.clear()
        self.assertEqual(len(c.allocations()), 3)

    @covers("B0", "B1", "B2", "B3")
    def test_validation_across_public_operations(self):
        for cap in (0, -1, True, False, 1.5, "2", None):
            with self.subTest(capacity=cap), self.assertRaises(ValueError):
                self.m.Calendar(capacity=cap)
        c = self.m.Calendar(4)
        c.reserve("old", 0, 2)
        invalid = [("", 2, 3, 1), (None, 2, 3, 1), (3, 2, 3, 1),
                   ("x", True, 3, 1), ("x", 2, True, 1), ("x", 2.0, 3, 1),
                   ("x", 3, 3, 1), ("x", 4, 3, 1), ("x", 2, 3, 0),
                   ("x", 2, 3, -1), ("x", 2, 3, True), ("x", 2, 3, 1.5)]
        for args in invalid:
            for method in (c.reserve, c.wait):
                with self.subTest(method=method.__name__, args=args):
                    self.atomic_failure(c, ValueError, lambda: method(*args))
            with self.subTest(method="reserve_many", args=args):
                self.atomic_failure(c, ValueError, lambda: c.reserve_many([("good", 20, 21, 1), args]))
            if args[0] == "x":
                with self.subTest(method="replace", args=args):
                    self.atomic_failure(c, ValueError, lambda: c.replace("old", *args[1:]))
        for args in ((True, 3, 1), (2, True, 1), (2, 2, 1), (2, 3, True), (2, 3, 0)):
            self.atomic_failure(c, ValueError, lambda: c.available(*args))
        for priority in (True, False, 1.5, "1", None):
            self.atomic_failure(c, ValueError, lambda: c.wait("x", 10, 11, priority=priority))

    @covers("B0", "B3")
    def test_many_overlaps_sweep_oracle(self):
        c = self.m.Calendar(9)
        rows = []
        rng = random.Random(470102)
        version = 0
        for index in range(120):
            start = rng.randrange(-15, 15)
            end = start + rng.randrange(1, 10)
            units = rng.randrange(1, 6)
            fits = all(units + sum(u for _, s, e, u in rows if s <= t < e) <= 9 for t in range(start, end))
            with self.subTest(index=index):
                self.assertEqual(c.available(start, end, units), fits)
                if fits:
                    c.reserve(str(index), start, end, units)
                    rows.append((str(index), start, end, units))
                    version += 1
                else:
                    self.atomic_failure(c, self.m.CapacityError, lambda: c.reserve(str(index), start, end, units))
                self.assertEqual(c.version, version)
                self.assertEqual(c.allocations(), sorted(rows, key=lambda row: (row[1], row[0])))

    @covers("B1", "B3")
    def test_batch_atomic_capacity_duplicates_and_iterable(self):
        c = self.m.Calendar(3)
        c.reserve_many(iter([("z", 1, 5, 1), ("a", 1, 5, 2), ("next", 5, 7, 3)]))
        self.assertEqual(c.allocations(), [("a", 1, 5, 2), ("z", 1, 5, 1), ("next", 5, 7, 3)])
        self.assertEqual(c.version, 1)
        for bad, exc in (([("x", 10, 15, 2), ("y", 11, 16, 2)], self.m.CapacityError),
                         ([("x", 10, 11, 1), ("x", 12, 13, 1)], ValueError),
                         ([("x", 10, 11, 1), ("a", 12, 13, 1)], ValueError),
                         ([("x", 10, 11, 1), ("y", 10, 10, 1)], ValueError)):
            with self.subTest(bad=bad):
                self.atomic_failure(c, exc, lambda: c.reserve_many(iter(bad)))
        c.reserve_many(iter(()))
        self.assertEqual(c.version, 1)
        c.reserve("x", 10, 11)
        self.assertEqual(c.version, 2)

    @covers("B1", "B3")
    def test_replace_excludes_original_and_retains_on_failure(self):
        c = self.m.Calendar(3)
        c.reserve("x", 0, 5, 3)
        c.reserve("y", 5, 10, 3)
        c.replace("x", 0, 5, 3)
        self.assertEqual(c.version, 2)
        c.replace("x", -5, 4, 2)
        self.assertEqual(c.version, 3)
        self.atomic_failure(c, self.m.CapacityError, lambda: c.replace("x", 4, 6, 1))
        self.atomic_failure(c, ValueError, lambda: c.replace("absent", 0, 1))
        self.assertTrue(c.cancel("y"))
        self.assertEqual(c.version, 4)
        self.assertFalse(c.cancel("absent"))
        self.assertEqual(c.version, 4)

    @covers("B2", "B3")
    def test_wait_priority_fifo_skip_continue_and_promotion_version(self):
        c = self.m.Calendar(2)
        c.reserve("busy", 0, 10, 2)
        c.wait("huge", 20, 30, 3, priority=100)
        c.wait("z-first", 0, 10, 1, priority=8)
        c.wait("a-second", 0, 10, 1, priority=8)
        c.wait("low", 10, 20, 2, priority=-1)
        self.assertEqual(c.waiting(), ["huge", "z-first", "a-second", "low"])
        self.assertEqual(c.version, 5)
        returned = c.waiting()
        returned.clear()
        self.assertEqual(c.waiting(), ["huge", "z-first", "a-second", "low"])
        self.assertEqual(c.promote(), ["low"])
        self.assertEqual(c.version, 6)
        self.assertEqual(c.waiting(), ["huge", "z-first", "a-second"])
        self.assertEqual(c.promote(), [])
        self.assertEqual(c.version, 6)
        self.assertTrue(c.cancel("busy"))
        self.assertEqual(c.version, 7)
        self.assertEqual(c.waiting(), ["huge", "z-first", "a-second"])
        self.assertEqual(c.promote(), ["z-first", "a-second"])
        self.assertEqual(c.version, 8)
        self.assertEqual(c.waiting(), ["huge"])
        self.assertTrue(c.cancel("huge"))
        self.assertEqual(c.version, 9)
        self.assertFalse(c.cancel("huge"))
        self.assertEqual(c.version, 9)

    @covers("B2", "B3")
    def test_promotion_updates_later_fit(self):
        c = self.m.Calendar(2)
        c.wait("z", 0, 10, 2)
        c.wait("a", 0, 10, 1)
        c.wait("elsewhere", 10, 20, 2, priority=-50)
        self.assertTrue(c.available(0, 10, 2))
        self.assertEqual(c.promote(), ["z", "elsewhere"])
        self.assertEqual(c.waiting(), ["a"])
        self.assertEqual(c.version, 4)

    @covers("B1", "B2", "B3")
    def test_waiting_ids_cannot_be_stolen(self):
        c = self.m.Calendar(1)
        c.reserve("active", 0, 1)
        c.wait("waiting", 2, 3)
        for action in (lambda: c.reserve("waiting", 4, 5), lambda: c.wait("waiting", 4, 5),
                       lambda: c.wait("active", 4, 5), lambda: c.replace("waiting", 4, 5),
                       lambda: c.reserve_many([("fresh", 6, 7, 1), ("waiting", 4, 5, 1)])):
            self.atomic_failure(c, ValueError, action)
        self.assertTrue(c.cancel("waiting"))
        c.reserve("waiting", 4, 5)
        self.assertEqual(c.version, 4)

    @covers("B3", "B2")
    def test_checkpoint_immutable_reusable_exact_restore_current_version(self):
        c = self.m.Calendar(2)
        c.reserve("active", -1, 4, 2)
        c.wait("z-first", 0, 3, 1, priority=7)
        c.wait("a-second", 0, 3, 1, priority=7)
        c.wait("higher", 0, 3, 2, priority=8)
        c.wait("lower", 4, 5, 2, priority=-3)
        cp = c.checkpoint()
        self.immutable(cp)
        saved = self.state(c)
        c.restore(cp)
        self.assertEqual(self.state(c), saved)
        c.cancel("z-first")
        c.wait("z-first", 0, 3, 1, priority=7)
        c.replace("active", -5, -2, 1)
        c.promote()
        v = c.version
        c.restore(cp)
        self.assertEqual(c.version, v + 1)
        self.assertEqual(c.allocations(), saved[1])
        self.assertEqual(c.waiting(), saved[2])
        v = c.version
        c.restore(cp)
        self.assertEqual(c.version, v)
        c.wait("later", 0, 3, 1, priority=7)
        self.assertEqual(c.waiting(), ["higher", "z-first", "a-second", "later", "lower"])
        c.cancel("active")
        self.assertEqual(c.promote(), ["higher", "lower"])
        self.assertFalse(c.available(0, 3))
        c.restore(cp)
        self.assertEqual(c.allocations(), saved[1])
        self.assertEqual(c.waiting(), saved[2])
        c.cancel("active")
        c.cancel("higher")
        self.assertEqual(c.promote(), ["z-first", "a-second", "lower"])
        self.assertEqual(sum(row[3] for row in c.allocations() if row[1] == 0), 2)

    @covers("B3")
    def test_checkpoint_restores_original_wait_sequence(self):
        c = self.m.Calendar()
        c.wait("a", 0, 1)
        cp = c.checkpoint()
        c.cancel("a")
        c.wait("a", 0, 1)
        v = c.version
        # Same visible bookings but a different original insertion sequence.
        c.restore(cp)
        self.assertEqual(c.version, v + 1)
        c.wait("b", 0, 1)
        self.assertEqual(c.promote(), ["a"])

    @covers("B3")
    def test_invalid_foreign_checkpoint_atomic(self):
        c = self.m.Calendar(3)
        c.reserve("active", 0, 1, 3)
        c.wait("waiting", 1, 2, 3)
        foreign = self.m.Calendar(3).checkpoint()
        for cp in (None, {}, [], 0, "checkpoint", object(), foreign):
            with self.subTest(type=type(cp).__name__):
                self.atomic_failure(c, ValueError, lambda: c.restore(cp))
        self.assertFalse(c.available(0, 1))
        c.cancel("active")
        self.assertEqual(c.promote(), ["waiting"])


class StreamContract(ContractCase):
    branch = "C"

    def stock(self, stream, identifier, sku, qty):
        return stream.feed(line(event(identifier, sku=sku, qty=qty)))

    def bad(self, stream, payload, exception=None):
        before = stream.snapshot()
        with self.assertRaises(exception or self.m.InventoryError):
            stream.feed(line(payload))
        self.assertEqual(stream.snapshot(), before)

    @covers("C0")
    def test_fragmented_quotes_crlf_blank_and_final_line(self):
        s = self.m.InventoryStream()
        first = event("one", sku='雪"quoted\sku', qty=5)
        second = event("two", "move", src=first["sku"], dst="target", qty=5)
        text = " " + chr(13) + chr(10) + line(first).replace(chr(10), chr(13) + chr(10)) + chr(9) + chr(10) + json.dumps(second, ensure_ascii=False)
        returned = []
        for char in text:
            returned.extend(s.feed(char))
        self.assertEqual(returned, ["one"])
        self.assertEqual(s.finish(), ["two"])
        self.assertEqual(s.finish(), [])
        self.assertEqual(s.snapshot(), {first["sku"]: 0, "target": 5})
        self.assertEqual(s.balance("unknown"), 0)
        self.assertNotIn("unknown", s.snapshot())
        independent = s.snapshot()
        independent["target"] = 999
        independent.clear()
        self.assertEqual(s.balance("target"), 5)

    @covers("C0")
    def test_identity_key_order_first_id_conflict_consumption(self):
        s = self.m.InventoryStream()
        payload = event("same", sku="A", qty=3)
        self.assertEqual(s.feed(line(payload)), ["same"])
        self.assertEqual(s.feed(line(dict(reversed(tuple(payload.items()))))), [])
        self.assertEqual(s.balance("A"), 3)
        self.bad(s, event("same", sku="A", qty=4), self.m.ConflictError)
        self.assertEqual(self.stock(s, "after", "B", 2), ["after"])
        self.assertEqual(s.snapshot(), {"A": 3, "B": 2})

    @covers("C0")
    def test_invalid_events_consumed_and_ids_not_reserved(self):
        invalid = [None, [], 3, "stock", {}, {"id": "bad", "kind": "other"},
                   event("", sku="A", qty=1), event(2, sku="A", qty=1),
                   event("bad", sku="", qty=1), event("bad", sku=None, qty=1),
                   event("bad", sku="A", qty=True), event("bad", sku="A", qty=False),
                   event("bad", sku="A", qty=0), event("bad", sku="A", qty=-1),
                   event("bad", sku="A", qty=1.5), event("bad", sku="A", qty="2"),
                   {"id": "bad", "kind": "stock", "sku": "A"},
                   event("bad", "move", src="A", dst="A", qty=1),
                   event("bad", "move", src="", dst="B", qty=1),
                   event("bad", "move", src="A", dst="", qty=1),
                   event("bad", "move", src=3, dst="B", qty=1),
                   event("bad", "move", src="A", dst="B", qty=10)]
        for payload in invalid:
            with self.subTest(payload=payload):
                s = self.m.InventoryStream()
                self.stock(s, "seed", "A", 3)
                self.bad(s, payload)
                self.assertEqual(self.stock(s, "bad", "A", 1), ["bad"])
                self.assertEqual(s.balance("A"), 4)
                self.assertNotIn("B", s.snapshot())
        for malformed in ('{"id":', 'not json', '{"id":"bad",}'):
            s = self.m.InventoryStream()
            with self.assertRaises(self.m.InventoryError):
                s.feed(malformed + "\n")
            self.assertEqual(self.stock(s, "bad", "A", 1), ["bad"])

    @covers("C0")
    def test_semantic_failure_keeps_earlier_complete_event(self):
        s = self.m.InventoryStream()
        good = event("good", sku="A", qty=2)
        bad = event("bad", "move", src="A", dst="B", qty=3)
        with self.assertRaises(self.m.InventoryError):
            s.feed(line(good) + line(bad))
        self.assertEqual(s.snapshot(), {"A": 2})
        self.assertEqual(s.feed(line(good)), [])
        self.assertEqual(self.stock(s, "bad", "B", 1), ["bad"])

    @covers("C0")
    def test_finish_failure_consumed_and_corrected_retry(self):
        s = self.m.InventoryStream()
        s.feed(json.dumps(event("bad", "move", src="A", dst="B", qty=2)))
        with self.assertRaises(self.m.InventoryError):
            s.finish()
        self.assertEqual(s.finish(), [])
        self.assertEqual(self.stock(s, "bad", "A", 2), ["bad"])
        s.feed('{"invalid":')
        with self.assertRaises(self.m.InventoryError):
            s.finish()
        self.assertEqual(s.finish(), [])
        self.assertEqual(self.stock(s, "next", "A", 1), ["next"])

    @covers("C0", "C1")
    def test_batch_order_outer_id_empty_and_duplicates(self):
        s = self.m.InventoryStream()
        payload = event("batch", "batch", ops=[{"kind": "stock", "sku": "A", "qty": 4},
                                               {"kind": "move", "src": "A", "dst": "B", "qty": 4},
                                               {"kind": "move", "src": "B", "dst": "C", "qty": 1}])
        self.assertEqual(s.feed(line(payload)), ["batch"])
        self.assertEqual(s.snapshot(), {"A": 0, "B": 3, "C": 1})
        self.assertEqual(s.feed(line(dict(reversed(tuple(payload.items()))))), [])
        self.bad(s, event("batch", "batch", ops=[]), self.m.ConflictError)
        empty = event("empty", "batch", ops=[])
        self.assertEqual(s.feed(line(empty)), ["empty"])
        self.assertEqual(s.feed(line(empty)), [])
        self.bad(s, event("empty", sku="D", qty=1), self.m.ConflictError)
        self.assertEqual(s.feed(line(event("one", sku="D", qty=1)) + line(event("two", sku="D", qty=2))), ["one", "two"])

    @covers("C1")
    def test_batch_invalid_op_rollback_and_corrected_id_retry(self):
        invalid_ops = [None, [], {}, {"kind": "batch", "ops": []},
                       {"id": "forbidden", "kind": "stock", "sku": "A", "qty": 1},
                       {"kind": "stock", "sku": "A", "qty": True},
                       {"kind": "stock", "sku": "", "qty": 1},
                       {"kind": "move", "src": "A", "dst": "B", "qty": 100},
                       {"kind": "move", "src": "A", "dst": "A", "qty": 1}]
        for bad in invalid_ops:
            with self.subTest(op=bad):
                s = self.m.InventoryStream()
                self.stock(s, "seed", "A", 2)
                self.bad(s, event("retry", "batch", ops=[{"kind": "stock", "sku": "new", "qty": 5}, bad]))
                self.assertNotIn("new", s.snapshot())
                self.assertEqual(s.feed(line(event("retry", "batch", ops=[]))), ["retry"])
        for ops in (None, {}, "ops", 1):
            s = self.m.InventoryStream()
            self.bad(s, event("retry", "batch", ops=ops))
            self.assertEqual(self.stock(s, "retry", "A", 1), ["retry"])

    @covers("C2", "C0")
    def test_undo_stock_failure_retry_and_tombstone(self):
        s = self.m.InventoryStream()
        payload = event("stock", sku="A", qty=5)
        s.feed(line(payload))
        s.feed(line(event("move", "move", src="A", dst="B", qty=5)))
        before = s.snapshot()
        with self.assertRaises(self.m.InventoryError):
            s.undo("stock")
        self.assertEqual(s.snapshot(), before)
        self.assertEqual(s.feed(line(payload)), [])
        self.stock(s, "refill", "A", 5)
        s.undo("stock")
        self.assertEqual(s.snapshot(), {"A": 0, "B": 5})
        self.assertEqual(s.feed(line(payload)), [])
        self.bad(s, event("stock", sku="A", qty=6), self.m.ConflictError)
        with self.assertRaises(self.m.InventoryError):
            s.undo("stock")
        with self.assertRaises(self.m.InventoryError):
            s.undo("unknown")
        self.assertEqual(s.snapshot(), {"A": 0, "B": 5})
        s.undo("move")
        self.assertEqual(s.snapshot(), {"A": 5, "B": 0})

    @covers("C2", "C1")
    def test_undo_net_delta_not_reversed_validation_order(self):
        s = self.m.InventoryStream()
        self.stock(s, "seed", "A", 1)
        roundtrip = event("roundtrip", "batch", ops=[{"kind": "move", "src": "A", "dst": "B", "qty": 1},
                                                    {"kind": "move", "src": "B", "dst": "A", "qty": 1}])
        s.feed(line(roundtrip))
        s.feed(line(event("drain", "move", src="A", dst="C", qty=1)))
        before = s.snapshot()
        s.undo("roundtrip")
        self.assertEqual(s.snapshot(), before)
        self.assertEqual(s.feed(line(roundtrip)), [])
        self.bad(s, event("roundtrip", "batch", ops=[]), self.m.ConflictError)
        empty = event("empty", "batch", ops=[])
        s.feed(line(empty))
        s.undo("empty")
        self.assertEqual(s.feed(line(empty)), [])

    @covers("C1", "C2")
    def test_batch_undo_aggregates_signed_deltas_atomically(self):
        s = self.m.InventoryStream()
        payload = event("batch", "batch", ops=[{"kind": "stock", "sku": "A", "qty": 4},
                                               {"kind": "move", "src": "A", "dst": "B", "qty": 3},
                                               {"kind": "stock", "sku": "A", "qty": 2}])
        s.feed(line(payload))
        self.assertEqual(s.snapshot(), {"A": 3, "B": 3})
        s.feed(line(event("drain", "move", src="B", dst="C", qty=3)))
        before = s.snapshot()
        with self.assertRaises(self.m.InventoryError):
            s.undo("batch")
        self.assertEqual(s.snapshot(), before)
        self.assertEqual(s.feed(line(payload)), [])
        self.stock(s, "refill", "B", 3)
        s.undo("batch")
        self.assertEqual(s.snapshot(), {"A": 0, "B": 0, "C": 3})
        self.assertEqual(s.feed(line(payload)), [])

    @covers("C2")
    def test_unrelated_undo_not_a_stack(self):
        s = self.m.InventoryStream()
        self.stock(s, "first", "A", 2)
        self.stock(s, "second", "B", 3)
        self.stock(s, "third", "A", 4)
        s.undo("first")
        self.assertEqual(s.snapshot(), {"A": 4, "B": 3})
        s.undo("second")
        self.assertEqual(s.snapshot(), {"A": 4, "B": 0})
        s.undo("third")
        self.assertEqual(s.snapshot(), {"A": 0, "B": 0})

    @covers("C3", "C0", "C1", "C2")
    def test_checkpoint_buffer_payloads_tombstones_deltas_reuse(self):
        s = self.m.InventoryStream()
        payload = event("batch", "batch", ops=[{"kind": "stock", "sku": "A", "qty": 3},
                                               {"kind": "move", "src": "A", "dst": "B", "qty": 2}])
        dead = event("dead", sku="D", qty=2)
        s.feed(line(payload))
        s.feed(line(dead))
        s.undo("dead")
        encoded = line(event("pending", sku="P", qty=5))
        cut = encoded.index("P") + 1
        self.assertEqual(s.feed(encoded[:cut]), [])
        cp = s.checkpoint()
        self.immutable(cp)
        saved = s.snapshot()
        self.assertEqual(s.feed(encoded[cut:]), ["pending"])
        s.undo("batch")
        self.stock(s, "after", "X", 9)
        s.feed("junk")
        s.restore(cp)
        self.assertEqual(s.snapshot(), saved)
        self.assertEqual(s.feed(encoded[cut:]), ["pending"])
        self.assertEqual(s.feed(line(payload)), [])
        self.assertEqual(s.feed(line(dead)), [])
        with self.assertRaises(self.m.InventoryError):
            s.undo("dead")
        self.bad(s, event("dead", sku="D", qty=3), self.m.ConflictError)
        s.undo("batch")
        self.assertEqual(s.balance("A"), 0)
        self.assertEqual(s.balance("B"), 0)
        self.assertEqual(self.stock(s, "after", "X", 9), ["after"])
        s.restore(cp)
        self.assertEqual(s.snapshot(), saved)
        self.assertEqual(s.feed(encoded[cut:]), ["pending"])
        s.undo("batch")
        self.assertEqual(s.balance("B"), 0)

    @covers("C3")
    def test_checkpoint_restores_pre_undo_status(self):
        s = self.m.InventoryStream()
        payload = event("seed", sku="A", qty=2)
        s.feed(line(payload))
        cp = s.checkpoint()
        s.undo("seed")
        self.assertEqual(s.feed(line(payload)), [])
        s.restore(cp)
        self.assertEqual(s.balance("A"), 2)
        s.undo("seed")
        self.assertEqual(s.balance("A"), 0)

    @covers("C3")
    def test_invalid_foreign_checkpoint_preserves_buffer_state(self):
        s = self.m.InventoryStream()
        self.stock(s, "seed", "A", 2)
        encoded = line(event("pending", sku="B", qty=3))
        s.feed(encoded[:12])
        foreign = self.m.InventoryStream().checkpoint()
        for cp in (None, {}, [], 0, "checkpoint", object(), foreign):
            with self.subTest(type=type(cp).__name__), self.assertRaises(self.m.InventoryError):
                s.restore(cp)
            self.assertEqual(s.snapshot(), {"A": 2})
        self.assertEqual(s.feed(encoded[12:]), ["pending"])
        s.undo("seed")
        self.assertEqual(s.balance("A"), 0)

    def sized_payload(self, codepoints, identifier="long", char="雪"):
        base = json.dumps(event(identifier, sku="", qty=1), ensure_ascii=False)
        return json.dumps(event(identifier, sku=char * (codepoints - len(base)), qty=1), ensure_ascii=False)

    @covers("C3", "C0")
    def test_unicode_codepoint_bound_complete_unfinished_finish(self):
        for char in ("雪", "😀"):
            for mode in ("complete", "fragmented", "finish"):
                with self.subTest(char=char, mode=mode):
                    s = self.m.InventoryStream()
                    encoded = self.sized_payload(4096, char=char)
                    self.assertEqual(len(encoded), 4096)
                    self.assertGreater(len(encoded.encode("utf-8")), 4096)
                    if mode == "complete":
                        ids = s.feed(encoded + "\n")
                    elif mode == "fragmented":
                        self.assertEqual(s.feed(encoded[:2000]), [])
                        self.assertEqual(s.feed(encoded[2000:]), [])
                        ids = s.feed("\n")
                    else:
                        self.assertEqual(s.feed(encoded), [])
                        ids = s.finish()
                    self.assertEqual(ids, ["long"])
                    self.assertEqual(sum(s.snapshot().values()), 1)
        for suffix in ("", "\n"):
            s = self.m.InventoryStream()
            with self.assertRaises(self.m.InventoryError):
                s.feed(self.sized_payload(4097) + suffix)
            self.assertEqual(s.snapshot(), {})
            self.assertEqual(self.stock(s, "long", "retry", 1), ["long"])

    @covers("C3", "C0", "C2")
    def test_overflow_whole_call_rolls_back_events_ids_buffer(self):
        for suffix in ("", "\n"):
            with self.subTest(suffix=repr(suffix)):
                s = self.m.InventoryStream()
                self.stock(s, "seed", "A", 1)
                s.undo("seed")
                completed = line(event("first", sku="B", qty=3))
                s.feed(completed[:15])
                before = s.snapshot()
                with self.assertRaises(self.m.InventoryError):
                    s.feed(completed[15:] + self.sized_payload(4097, identifier="too-long") + suffix)
                self.assertEqual(s.snapshot(), before)
                self.assertEqual(s.feed(completed[15:]), ["first"])
                self.assertEqual(s.feed(line(event("seed", sku="A", qty=1))), [])
                with self.assertRaises(self.m.InventoryError):
                    s.undo("seed")
                self.assertEqual(self.stock(s, "too-long", "C", 1), ["too-long"])

    @covers("C3")
    def test_unfinished_overflow_restores_prior_buffer_for_finish(self):
        s = self.m.InventoryStream()
        s.feed(self.sized_payload(4096))
        with self.assertRaises(self.m.InventoryError):
            s.feed("x")
        self.assertEqual(s.snapshot(), {})
        self.assertEqual(s.finish(), ["long"])
        self.assertEqual(s.finish(), [])

    @covers("C3")
    def test_overflow_whole_feed_even_after_semantically_bad_line(self):
        s = self.m.InventoryStream()
        self.stock(s, "seed", "A", 1)
        bad = line(event("bad", "move", src="A", dst="B", qty=2))
        before = s.snapshot()
        # ANY overlong feed rolls back THAT call, even if semantic processing
        # would otherwise encounter an earlier failing line.
        with self.assertRaises(self.m.InventoryError):
            s.feed(line(event("earlier", sku="C", qty=1)) + bad + "x" * 4097)
        self.assertEqual(s.snapshot(), before)
        self.assertEqual(self.stock(s, "earlier", "C", 1), ["earlier"])
        self.assertEqual(self.stock(s, "bad", "B", 1), ["bad"])


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
        self.started = {}
        self.subtest_counts = {}

    def startTest(self, test):
        self.started[test.id()] = time.monotonic()
        self.subtest_counts[test.id()] = {"passed": 0, "failed": 0, "error": 0}
        super().startTest(test)

    def record(self, test, status, detail=None):
        parent = getattr(test, "test_case", test)
        fn = getattr(parent, parent._testMethodName)
        self.records.append(dict(test=test.id(), status=status, requirements=list(getattr(fn, "requirements", ())),
                                 seconds=round(time.monotonic() - self.started.get(parent.id(), time.monotonic()), 6),
                                 detail=detail))

    def addSuccess(self, test):
        self.record(test, "passed")
        super().addSuccess(test)

    def addFailure(self, test, err):
        self.record(test, "failed", self._exc_info_to_string(err, test))
        super().addFailure(test, err)

    def addError(self, test, err):
        self.record(test, "error", self._exc_info_to_string(err, test))
        super().addError(test, err)

    def addSubTest(self, test, subtest, err):
        status = "passed" if err is None else "failed" if issubclass(err[0], test.failureException) else "error"
        self.subtest_counts[test.id()][status] += 1
        if err is not None:
            self.record(subtest, status, self._exc_info_to_string(err, test))
        super().addSubTest(test, subtest, err)


def main():
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    run_dir = pathlib.Path(sys.argv[1]).resolve()
    workspace = run_dir / "workspace"
    source = pathlib.Path(__file__).resolve()
    verifier_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    spec_hash = hashlib.sha256((source.parent / "requests.json").read_bytes()).hexdigest()
    # Preload normal standard-library dependencies before target path insertion.
    import bisect, contextlib, copy, dataclasses, enum, functools, heapq, math, operator, re, threading, types, typing, uuid, weakref
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(workspace))
    # No generated test discovery; invoke public target APIs only.
    for name in ("routing_graph", "booking_calendar", "inventory_stream"):
        try:
            MODULES[name] = importlib.import_module(name)
        except Exception:
            IMPORT_ERRORS[name] = traceback.format_exc()
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls)
                               for cls in (GraphContract, CalendarContract, StreamContract))
    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=2, resultclass=RecordingResult).run(suite)
    coverage = {}
    for requirement in SPEC_IDS:
        records = [r for r in result.records if requirement in r["requirements"]]
        coverage[requirement] = dict(tests=sorted({r["test"] for r in records}),
                                     failures=sum(r["status"] != "passed" for r in records),
                                     status="failed" if any(r["status"] != "passed" for r in records)
                                     else "passed" if records else "uncovered")
    summary = dict(verifier_sha256=verifier_hash, requests_sha256=spec_hash,
                   authored_from="requests.json only; generated implementation/tests not read before verifier hash freeze",
                   run_directory=str(run_dir), workspace=str(workspace), tests_run=result.testsRun,
                   failures=len(result.failures), errors=len(result.errors), successful=result.wasSuccessful(),
                   import_errors=IMPORT_ERRORS, requirement_coverage=coverage, tests=result.records,
                   subtest_counts=result.subtest_counts)
    rendered = output.getvalue()
    (run_dir / "independent-run.txt").write_text(rendered, encoding="utf-8")
    (run_dir / "independent-result.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(rendered, end="")
    print("Verifier SHA256:", verifier_hash)
    print("Requests SHA256:", spec_hash)
    print("Coverage:", ", ".join(k + "=" + v["status"] for k, v in coverage.items()))
    print("Evidence:", run_dir / "independent-run.txt", run_dir / "independent-result.json")
    return 0 if result.wasSuccessful() and all(v["status"] == "passed" for v in coverage.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

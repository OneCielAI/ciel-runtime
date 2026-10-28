import itertools
import random
import unittest

from routing_graph import ConflictError, Graph, NoPathError


def brute_shortest(adj, src, dst, blocked=(), max_hops=None):
    """Enumerate every simple path and pick min by (cost, hops, path)."""
    if src in blocked or dst in blocked:
        return None
    if src == dst:
        return (0, (src,))
    best = None
    stack = [(src, (src,), 0)]
    while stack:
        node, path, cost = stack.pop()
        if max_hops is not None and len(path) - 1 >= max_hops:
            continue
        for nxt, w in adj.get(node, {}).items():
            if nxt in path or nxt in blocked:
                continue
            p, c = path + (nxt,), cost + w
            if nxt == dst:
                key = (c, len(p), p)
                if best is None or key < best:
                    best = key
            else:
                stack.append((nxt, p, c))
    if best is None:
        return None
    return (best[0], best[2])


class GraphBasics(unittest.TestCase):
    def test_exceptions_exist(self):
        self.assertTrue(issubclass(NoPathError, Exception))
        self.assertTrue(issubclass(ConflictError, Exception))

    def test_src_equals_dst(self):
        g = Graph(tie_policy="fewest_hops")
        g.add_node("a")
        self.assertEqual(g.shortest("a", "a"), (0, ("a",)))

    def test_unreachable(self):
        g = Graph(tie_policy="fewest_hops")
        g.add_node("a")
        g.add_node("b")
        with self.assertRaises(NoPathError):
            g.shortest("a", "b")

    def test_directed(self):
        g = Graph(tie_policy="fewest_hops")
        g.add_node("a")
        g.add_node("b")
        g.set_edge("a", "b", 3)
        self.assertEqual(g.shortest("a", "b"), (3, ("a", "b")))
        with self.assertRaises(NoPathError):
            g.shortest("b", "a")

    def test_unknown_endpoints(self):
        g = Graph(tie_policy="fewest_hops")
        g.add_node("a")
        for call in (lambda: g.set_edge("a", "x", 1), lambda: g.set_edge("x", "a", 1),
                     lambda: g.shortest("a", "x"), lambda: g.shortest("x", "a"),
                     lambda: g.shortest("x", "x"), lambda: g.remove_edge("a", "x")):
            with self.assertRaises(ValueError):
                call()
        self.assertEqual(g.version, 1)

    def test_invalid_names(self):
        g = Graph(tie_policy="fewest_hops")
        for bad in ("", None, 1, b"a", ("a",)):
            with self.assertRaises(ValueError):
                g.add_node(bad)
            with self.assertRaises(ValueError):
                g.remove_node(bad)
        self.assertEqual(g.version, 0)

    def test_invalid_costs(self):
        g = Graph(tie_policy="fewest_hops")
        g.add_node("a")
        g.add_node("b")
        for bad in (-1, True, False, 1.0, "1", None):
            with self.assertRaises(ValueError):
                g.set_edge("a", "b", bad)
        self.assertEqual(g.version, 2)
        with self.assertRaises(NoPathError):
            g.shortest("a", "b")

    def test_overwrite(self):
        g = Graph(tie_policy="fewest_hops")
        for n in "abc":
            g.add_node(n)
        g.set_edge("a", "b", 10)
        g.set_edge("a", "b", 1)
        self.assertEqual(g.shortest("a", "b"), (1, ("a", "b")))
        g.set_edge("a", "c", 0)
        g.set_edge("c", "b", 0)
        self.assertEqual(g.shortest("a", "b"), (0, ("a", "c", "b")))

    def test_remove_edge_and_node(self):
        g = Graph(tie_policy="fewest_hops")
        for n in "abc":
            g.add_node(n)
        g.set_edge("a", "b", 1)
        g.set_edge("b", "c", 1)
        g.set_edge("c", "a", 1)
        self.assertFalse(g.remove_edge("a", "c"))
        self.assertTrue(g.remove_edge("a", "b"))
        self.assertFalse(g.remove_edge("a", "b"))
        g.set_edge("a", "b", 1)
        self.assertTrue(g.remove_node("b"))
        self.assertFalse(g.remove_node("b"))
        g.add_node("b")
        # incident edges must be gone after re-adding
        with self.assertRaises(NoPathError):
            g.shortest("a", "b")
        with self.assertRaises(NoPathError):
            g.shortest("b", "c")
        self.assertEqual(g.shortest("c", "a"), (1, ("c", "a")))


class TieBreaking(unittest.TestCase):
    def test_fewest_edges_beats_lexical(self):
        g = Graph(tie_policy="fewest_hops")
        for n in ("s", "a", "t"):
            g.add_node(n)
        g.set_edge("s", "a", 1)
        g.set_edge("a", "t", 1)
        g.set_edge("s", "t", 2)
        self.assertEqual(g.shortest("s", "t"), (2, ("s", "t")))

    def test_lexical_full_path(self):
        g = Graph(tie_policy="fewest_hops")
        for n in ("s", "a", "b", "x", "y", "t"):
            g.add_node(n)
        # s-b-x-t vs s-a-y-t, equal cost and hops: lexical picks s,a,y,t
        g.set_edge("s", "b", 1)
        g.set_edge("b", "x", 1)
        g.set_edge("x", "t", 1)
        g.set_edge("s", "a", 1)
        g.set_edge("a", "y", 1)
        g.set_edge("y", "t", 1)
        self.assertEqual(g.shortest("s", "t"), (3, ("s", "a", "y", "t")))

    def test_lexical_not_last_hop(self):
        # Full-tuple comparison: earlier element decides, not predecessor name.
        g = Graph(tie_policy="fewest_hops")
        for n in ("s", "a", "b", "p", "q", "t"):
            g.add_node(n)
        g.set_edge("s", "a", 1)
        g.set_edge("a", "q", 1)
        g.set_edge("q", "t", 1)
        g.set_edge("s", "b", 1)
        g.set_edge("b", "p", 1)
        g.set_edge("p", "t", 1)
        self.assertEqual(g.shortest("s", "t"), (3, ("s", "a", "q", "t")))

    def test_zero_cost_cycle_simple(self):
        g = Graph(tie_policy="fewest_hops")
        for n in ("s", "a", "b", "t"):
            g.add_node(n)
        g.set_edge("s", "a", 0)
        g.set_edge("a", "b", 0)
        g.set_edge("b", "a", 0)
        g.set_edge("b", "s", 0)
        g.set_edge("a", "t", 5)
        g.set_edge("s", "s", 0)
        cost, path = g.shortest("s", "t")
        self.assertEqual((cost, path), (5, ("s", "a", "t")))
        self.assertEqual(len(path), len(set(path)))

    def test_zero_cost_more_hops_vs_cheaper(self):
        g = Graph(tie_policy="fewest_hops")
        for n in ("s", "a", "b", "t"):
            g.add_node(n)
        g.set_edge("s", "t", 1)
        g.set_edge("s", "a", 0)
        g.set_edge("a", "b", 0)
        g.set_edge("b", "t", 0)
        self.assertEqual(g.shortest("s", "t"), (0, ("s", "a", "b", "t")))


class VersionSemantics(unittest.TestCase):
    def test_version_counts_only_changes(self):
        g = Graph(tie_policy="fewest_hops")
        self.assertEqual(g.version, 0)
        g.add_node("a"); self.assertEqual(g.version, 1)
        g.add_node("a"); self.assertEqual(g.version, 1)
        g.add_node("b"); self.assertEqual(g.version, 2)
        g.set_edge("a", "b", 1); self.assertEqual(g.version, 3)
        g.set_edge("a", "b", 1); self.assertEqual(g.version, 3)
        g.set_edge("a", "b", 2); self.assertEqual(g.version, 4)
        g.set_edge("b", "a", 0); self.assertEqual(g.version, 5)
        g.shortest("a", "b"); self.assertEqual(g.version, 5)
        g.remove_edge("a", "b"); self.assertEqual(g.version, 6)
        g.remove_edge("a", "b"); self.assertEqual(g.version, 6)
        # removing node with incident edges is ONE mutation
        g.remove_node("a"); self.assertEqual(g.version, 7)
        g.remove_node("a"); self.assertEqual(g.version, 7)
        with self.assertRaises(ValueError):
            g.set_edge("a", "b", 1)
        self.assertEqual(g.version, 7)


class RandomizedAgainstBruteForce(unittest.TestCase):
    def test_random_graphs(self):
        rng = random.Random(12345)
        names = ["a", "b", "c", "d", "e", "f", "aa", "ab"]
        for trial in range(400):
            g = Graph(tie_policy="fewest_hops")
            adj = {}
            k = rng.randint(1, 7)
            nodes = rng.sample(names, k)
            for n in nodes:
                g.add_node(n)
                adj[n] = {}
            for s, d in itertools.product(nodes, nodes):
                if rng.random() < 0.45:
                    w = rng.choice([0, 0, 1, 1, 2, 3])
                    g.set_edge(s, d, w)
                    adj[s][d] = w
            if rng.random() < 0.3 and k > 1:
                victim = rng.choice(nodes)
                g.remove_node(victim)
                del adj[victim]
                for e in adj.values():
                    e.pop(victim, None)
                nodes = [n for n in nodes if n != victim]
            for s, d in itertools.product(nodes, nodes):
                expected = brute_shortest(adj, s, d)
                if expected is None:
                    with self.assertRaises(NoPathError, msg=(trial, s, d)):
                        g.shortest(s, d)
                else:
                    self.assertEqual(g.shortest(s, d), expected, msg=(trial, s, d, adj))


class BlockedAndHopBound(unittest.TestCase):
    """REQ-A1."""

    def make(self):
        g = Graph(tie_policy="fewest_hops")
        for n in ("s", "a", "b", "c", "t"):
            g.add_node(n)
        # cheap 4-hop route s-a-b-c-t (cost 4) vs costly 1-hop s-t (cost 10)
        g.set_edge("s", "a", 1)
        g.set_edge("a", "b", 1)
        g.set_edge("b", "c", 1)
        g.set_edge("c", "t", 1)
        g.set_edge("s", "t", 10)
        g.set_edge("a", "t", 6)
        return g

    def test_cheap_route_violates_bound(self):
        g = self.make()
        self.assertEqual(g.shortest("s", "t"), (4, ("s", "a", "b", "c", "t")))
        self.assertEqual(g.shortest("s", "t", max_hops=4), (4, ("s", "a", "b", "c", "t")))
        self.assertEqual(g.shortest("s", "t", max_hops=3), (7, ("s", "a", "t")))
        self.assertEqual(g.shortest("s", "t", max_hops=2), (7, ("s", "a", "t")))
        self.assertEqual(g.shortest("s", "t", max_hops=1), (10, ("s", "t")))
        with self.assertRaises(NoPathError):
            g.shortest("s", "t", max_hops=0)
        self.assertEqual(g.shortest("s", "s", max_hops=0), (0, ("s",)))

    def test_bound_not_truncation(self):
        # Unrestricted best prefix s-x (cost 0) is useless under the bound;
        # bounded search must still find s-y-t.
        g = Graph(tie_policy="fewest_hops")
        for n in ("s", "x", "x2", "y", "t"):
            g.add_node(n)
        g.set_edge("s", "x", 0)
        g.set_edge("x", "x2", 0)
        g.set_edge("x2", "t", 0)
        g.set_edge("s", "y", 5)
        g.set_edge("y", "t", 5)
        self.assertEqual(g.shortest("s", "t", max_hops=2), (10, ("s", "y", "t")))

    def test_bounded_ties_unchanged(self):
        g = Graph(tie_policy="fewest_hops")
        for n in ("s", "a", "b", "t"):
            g.add_node(n)
        g.set_edge("s", "b", 1)
        g.set_edge("b", "t", 1)
        g.set_edge("s", "a", 1)
        g.set_edge("a", "t", 1)
        g.set_edge("s", "t", 2)
        self.assertEqual(g.shortest("s", "t", max_hops=2), (2, ("s", "t")))
        g.remove_edge("s", "t")
        self.assertEqual(g.shortest("s", "t", max_hops=2), (2, ("s", "a", "t")))

    def test_blocked(self):
        g = self.make()
        self.assertEqual(g.shortest("s", "t", blocked=("b",)), (7, ("s", "a", "t")))
        self.assertEqual(g.shortest("s", "t", blocked=["a"]), (10, ("s", "t")))
        self.assertEqual(g.shortest("s", "t", blocked={"a"}, max_hops=1), (10, ("s", "t")))
        g.remove_edge("s", "t")
        with self.assertRaises(NoPathError):
            g.shortest("s", "t", blocked=("a",))
        with self.assertRaises(NoPathError):
            g.shortest("s", "t", blocked=("a",), max_hops=2)

    def test_blocked_endpoints(self):
        g = self.make()
        for kw in ({}, {"max_hops": 3}):
            with self.assertRaises(NoPathError):
                g.shortest("s", "t", blocked=("s",), **kw)
            with self.assertRaises(NoPathError):
                g.shortest("s", "t", blocked=("t",), **kw)
            with self.assertRaises(NoPathError):
                g.shortest("s", "s", blocked=("s",), **kw)

    def test_validation(self):
        g = self.make()
        v = g.version
        for bad in (("zzz",), ("",), (1,), "s", 5, None):
            with self.assertRaises(ValueError, msg=bad):
                g.shortest("s", "t", blocked=bad)
        for bad in (-1, True, False, 1.5, "2"):
            with self.assertRaises(ValueError, msg=bad):
                g.shortest("s", "t", max_hops=bad)
        with self.assertRaises(ValueError):
            g.shortest("s", "nope", blocked=("s",))
        with self.assertRaises(TypeError):
            g.shortest("s", "t", ("a",))  # keyword-only
        self.assertEqual(g.version, v)

    def test_randomized(self):
        rng = random.Random(99)
        names = ["a", "b", "c", "d", "e", "f", "g"]
        for trial in range(300):
            g = Graph(tie_policy="fewest_hops")
            adj = {}
            nodes = rng.sample(names, rng.randint(1, 7))
            for n in nodes:
                g.add_node(n)
                adj[n] = {}
            for s, d in itertools.product(nodes, nodes):
                if rng.random() < 0.5:
                    w = rng.choice([0, 0, 1, 2, 5])
                    g.set_edge(s, d, w)
                    adj[s][d] = w
            blocked = tuple(n for n in nodes if rng.random() < 0.15)
            max_hops = rng.choice([None, 0, 1, 2, 3, 4, 10])
            for s, d in itertools.product(nodes, nodes):
                expected = brute_shortest(adj, s, d, blocked, max_hops)
                ctx = (trial, s, d, blocked, max_hops, adj)
                if expected is None:
                    with self.assertRaises(NoPathError, msg=ctx):
                        g.shortest(s, d, blocked=blocked, max_hops=max_hops)
                else:
                    self.assertEqual(
                        g.shortest(s, d, blocked=blocked, max_hops=max_hops), expected, msg=ctx)


class SnapshotAndApply(unittest.TestCase):
    """REQ-A2."""

    def base(self):
        g = Graph(tie_policy="fewest_hops")
        g.apply([{"op": "add_node", "name": n} for n in ("s", "a", "b", "t")])
        g.apply([{"op": "set_edge", "src": "s", "dst": "a", "cost": 1},
                 {"op": "set_edge", "src": "a", "dst": "t", "cost": 1},
                 {"op": "set_edge", "src": "s", "dst": "t", "cost": 5}])
        return g

    def test_snapshot_frozen(self):
        g = self.base()
        snap = g.snapshot()
        self.assertEqual(snap.version, 2)
        self.assertEqual(snap.shortest("s", "t"), (2, ("s", "a", "t")))
        g.remove_node("a")
        g.set_edge("s", "t", 0)
        g.add_node("z")
        self.assertEqual(g.shortest("s", "t"), (0, ("s", "t")))
        self.assertEqual(snap.version, 2)
        self.assertEqual(snap.shortest("s", "t"), (2, ("s", "a", "t")))
        with self.assertRaises(ValueError):
            snap.shortest("s", "z")
        # A1 options on snapshots
        self.assertEqual(snap.shortest("s", "t", max_hops=1), (5, ("s", "t")))
        self.assertEqual(snap.shortest("s", "t", blocked=("a",)), (5, ("s", "t")))
        with self.assertRaises(NoPathError):
            snap.shortest("s", "s", blocked=("s",))

    def test_snapshot_read_only(self):
        g = self.base()
        snap = g.snapshot()
        for attr in ("add_node", "set_edge", "remove_edge", "remove_node", "apply"):
            self.assertFalse(hasattr(snap, attr), attr)
        with self.assertRaises(AttributeError):
            snap.version = 99
        with self.assertRaises(AttributeError):
            snap._adj = {}
        with self.assertRaises(AttributeError):
            snap.other = 1
        self.assertEqual(snap.version, 2)

    def test_apply_ops_see_earlier_ops(self):
        g = Graph(tie_policy="fewest_hops")
        v = g.apply([{"op": "add_node", "name": "x"},
                     {"op": "add_node", "name": "y"},
                     {"op": "set_edge", "src": "x", "dst": "y", "cost": 3},
                     {"op": "set_edge", "src": "x", "dst": "y", "cost": 4}])
        self.assertEqual((v, g.version), (1, 1))
        self.assertEqual(g.shortest("x", "y"), (4, ("x", "y")))
        g.apply([{"op": "remove_edge", "src": "x", "dst": "y"},
                 {"op": "remove_node", "name": "y"}], expected_version=1)
        self.assertEqual(g.version, 2)
        with self.assertRaises(ValueError):
            g.shortest("x", "y")

    def test_apply_rollback(self):
        g = self.base()
        before = g.snapshot()
        bad = [
            [{"op": "add_node", "name": "n"}, {"op": "set_edge", "src": "n", "dst": "q", "cost": 1}],
            [{"op": "remove_node", "name": "a"}, {"op": "set_edge", "src": "s", "dst": "a", "cost": 1}],
            [{"op": "add_node", "name": "n"}, {"op": "set_edge", "src": "n", "dst": "s", "cost": -1}],
            [{"op": "add_node", "name": "n"}, {"op": "set_edge", "src": "n", "dst": "s", "cost": True}],
            [{"op": "add_node", "name": "n"}, {"op": "explode"}],
            [{"op": "add_node", "name": "n"}, {"op": "add_node"}],
            [{"op": "add_node", "name": "n"}, {"op": "add_node", "name": "m", "extra": 1}],
            [{"op": "add_node", "name": "n"}, "add_node"],
            [{"op": "add_node", "name": ""}],
        ]
        for changes in bad:
            with self.assertRaises(ValueError, msg=changes):
                g.apply(changes)
            self.assertEqual(g.version, 2)
            self.assertEqual(g.shortest("s", "t"), before.shortest("s", "t"))
            with self.assertRaises(ValueError):
                g.shortest("s", "n")
        with self.assertRaises(ValueError):
            g.apply(5)

    def test_expected_version_conflict_before_apply(self):
        g = self.base()
        with self.assertRaises(ConflictError):
            g.apply([{"op": "add_node", "name": "n"}], expected_version=1)
        # conflict detected even when the batch itself would be invalid
        with self.assertRaises(ConflictError):
            g.apply([{"op": "bogus"}], expected_version=0)
        self.assertEqual(g.version, 2)
        with self.assertRaises(ValueError):
            g.shortest("s", "n")
        g.apply([{"op": "add_node", "name": "n"}], expected_version=2)
        self.assertEqual(g.version, 3)
        with self.assertRaises(ValueError):
            g.apply([], expected_version=True)

    def test_no_change_batches_do_not_bump(self):
        g = self.base()
        self.assertEqual(g.apply([]), 2)
        self.assertEqual(g.apply([], expected_version=2), 2)
        g.apply([{"op": "add_node", "name": "s"},
                 {"op": "set_edge", "src": "s", "dst": "a", "cost": 1},
                 {"op": "remove_edge", "src": "t", "dst": "s"},
                 {"op": "remove_node", "name": "ghost"}])
        self.assertEqual(g.version, 2)
        g.apply([{"op": "set_edge", "src": "s", "dst": "a", "cost": 9},
                 {"op": "set_edge", "src": "s", "dst": "a", "cost": 1}])
        self.assertEqual(g.version, 2)
        g.apply(iter([{"op": "set_edge", "src": "s", "dst": "b", "cost": 0},
                      {"op": "remove_node", "name": "b"},
                      {"op": "add_node", "name": "c"}]))
        self.assertEqual(g.version, 3)


class FinalTiePolicy(unittest.TestCase):
    """REQ-A3 supersedes A0 default, not B or C ordering."""

    def test_longer_lex_smaller_route_and_snapshots(self):
        for policy in ("lexical", "fewest_hops"):
            g = Graph(policy)
            for n in ("s", "a", "b", "t"):
                g.add_node(n)
            for a, b, c in (("s", "t", 2), ("s", "a", 0), ("a", "b", 0),
                            ("b", "a", 0), ("b", "t", 2)):
                g.set_edge(a, b, c)
            expected = (2, ("s", "a", "b", "t")) if policy == "lexical" else (2, ("s", "t"))
            self.assertEqual(g.shortest("s", "t"), expected)
            snap = g.snapshot()
            self.assertEqual(snap.tie_policy, policy)
            with self.assertRaises(AttributeError):
                snap.tie_policy = "other"
            with self.assertRaises(TypeError):
                snap._adj["s"]["t"] = 9
            g.remove_node("a")
            self.assertEqual(snap.shortest("s", "t"), expected)
            self.assertEqual(snap.shortest("s", "t", max_hops=1), (2, ("s", "t")))
            self.assertEqual(snap.shortest("s", "t", blocked=("a",)), (2, ("s", "t")))
        self.assertEqual(Graph().tie_policy, "lexical")

    def test_invalid_policy(self):
        for bad in ("", "LEXICAL", "fewest", None, [], 1, True):
            with self.assertRaises(ValueError):
                Graph(bad)

    def test_bounded_prefix_hop_alternatives(self):
        g = Graph()
        for n in ("s", "a", "x", "t", "unused"):
            g.add_node(n)
        for a, b, c in (("s", "a", 0), ("a", "x", 0), ("s", "x", 1), ("x", "t", 0)):
            g.set_edge(a, b, c)
        self.assertEqual(g.shortest("s", "t", max_hops=2), (1, ("s", "x", "t")))
        self.assertEqual(g.shortest("s", "t"), (0, ("s", "a", "x", "t")))

    def test_random_both_policies_against_simple_path_enumeration(self):
        rng = random.Random(833)
        for trial in range(250):
            nodes = list("abcdefg")[:rng.randint(2, 7)]
            edges = [(s, d, rng.choice([0, 0, 1, 2])) for s in nodes for d in nodes
                     if rng.random() < 0.45]
            blocked = set(rng.sample(nodes, rng.randint(0, 1)))
            bound = rng.choice([None, 0, 1, 2, 3, 9])
            for policy in ("lexical", "fewest_hops"):
                g = Graph(policy)
                for n in nodes:
                    g.add_node(n)
                for s, d, c in edges:
                    g.set_edge(s, d, c)
                adj = {n: {} for n in nodes}
                for s, d, c in edges:
                    adj[s][d] = c
                for src, dst in itertools.product(nodes, nodes):
                    candidates = []
                    stack = [(0, (src,))] if src not in blocked and dst not in blocked else []
                    while stack:
                        cost, path = stack.pop()
                        if path[-1] == dst:
                            candidates.append((cost, path))
                            continue
                        if bound is not None and len(path) - 1 >= bound:
                            continue
                        for nxt, w in adj[path[-1]].items():
                            if nxt not in path and nxt not in blocked:
                                stack.append((cost + w, path + (nxt,)))
                    if not candidates:
                        with self.assertRaises(NoPathError):
                            g.shortest(src, dst, blocked=blocked, max_hops=bound)
                    else:
                        key = (lambda p: (p[0], p[1], len(p[1]))) if policy == "lexical" else (
                            lambda p: (p[0], len(p[1]), p[1]))
                        self.assertEqual(g.shortest(src, dst, blocked=blocked, max_hops=bound),
                                         min(candidates, key=key), (trial, policy, src, dst, bound))


if __name__ == "__main__":
    unittest.main()

import random
import unittest
from routing_graph import ConflictError, Graph, NoPathError


class GraphTests(unittest.TestCase):
    def test_mutations_and_version(self):
        graph = Graph()
        self.assertEqual(graph.version, 0)
        graph.add_node("a")
        graph.add_node("a")
        self.assertEqual(graph.version, 1)
        graph.add_node("b")
        graph.set_edge("a", "b", 2)
        graph.set_edge("a", "b", 2)
        self.assertEqual(graph.version, 3)
        self.assertFalse(graph.remove_edge("b", "a"))
        self.assertEqual(graph.version, 3)
        self.assertTrue(graph.remove_node("b"))
        self.assertFalse(graph.remove_node("b"))
        self.assertEqual(graph.version, 4)

    def test_validation_is_atomic(self):
        graph = Graph()
        with self.assertRaises(ValueError): graph.add_node(1)
        with self.assertRaises(ValueError): graph.set_edge("a", "b", 1)
        graph.add_node("a")
        with self.assertRaises(ValueError): graph.set_edge("a", "a", True)
        self.assertEqual(graph.version, 1)

    def test_shortest_cost_hops_and_lexical_path(self):
        graph = Graph()
        for name in "abcde": graph.add_node(name)
        graph.set_edge("a", "b", 1)
        graph.set_edge("b", "d", 1)
        graph.set_edge("a", "c", 1)
        graph.set_edge("c", "d", 1)
        graph.set_edge("a", "e", 0)
        graph.set_edge("e", "d", 2)
        self.assertEqual(graph.shortest("a", "d"), (2, ("a", "b", "d")))
        self.assertEqual(graph.shortest("a", "a"), (0, ("a",)))
        with self.assertRaises(NoPathError): graph.shortest("d", "a")

    def test_simple_paths_with_zero_cycle(self):
        graph = Graph()
        for name in ("a", "b", "c"): graph.add_node(name)
        graph.set_edge("a", "b", 0)
        graph.set_edge("b", "a", 0)
        graph.set_edge("b", "c", 1)
        self.assertEqual(graph.shortest("a", "c"), (1, ("a", "b", "c")))

    def test_a1_constraints_before_optimization(self):
        graph = Graph()
        for node in "abcd": graph.add_node(node)
        for src, dst, cost in [("a", "b", 0), ("b", "c", 0), ("c", "d", 0), ("a", "d", 9)]:
            graph.set_edge(src, dst, cost)
        self.assertEqual(graph.shortest("a", "d", max_hops=1), (9, ("a", "d")))
        self.assertEqual(graph.shortest("a", "d", blocked=["c"]), (9, ("a", "d")))
        with self.assertRaises(NoPathError): graph.shortest("a", "a", blocked=["a"])
        with self.assertRaises(ValueError): graph.shortest("a", "d", blocked=["unknown"])
        for bound in (True, -1, 1.5):
            with self.assertRaises(ValueError): graph.shortest("a", "d", max_hops=bound)
        with self.assertRaises(NoPathError): graph.shortest("a", "d", max_hops=0)

    def test_a2_frozen_snapshot(self):
        graph = Graph()
        graph.apply([{"op": "add_node", "name": "a"}, {"op": "add_node", "name": "b"},
                     {"op": "set_edge", "src": "a", "dst": "b", "cost": 1}])
        snapshot = graph.snapshot()
        graph.remove_node("b")
        self.assertEqual(snapshot.version, 1)
        self.assertEqual(snapshot.shortest("a", "b", max_hops=1), (1, ("a", "b")))
        with self.assertRaises(AttributeError): snapshot.version = 9
        with self.assertRaises(AttributeError): snapshot.set_edge

    def test_a2_apply_atomic_versioned(self):
        graph = Graph()
        graph.add_node("a")
        with self.assertRaises(ConflictError):
            graph.apply([{"op": "add_node", "name": "bad"}], expected_version=0)
        with self.assertRaises(ValueError):
            graph.apply([{"op": "add_node", "name": "b"}, {"op": "set_edge", "src": "a", "dst": "b", "cost": -1}])
        self.assertEqual(graph.version, 1)
        with self.assertRaises(ValueError): graph.shortest("a", "b")
        graph.apply([], expected_version=1)
        graph.apply([{"op": "add_node", "name": "a"}])
        graph.apply([{"op": "add_node", "name": "x"}, {"op": "remove_node", "name": "x"}])
        self.assertEqual(graph.version, 1)
        graph.apply([{"op": "add_node", "name": "b"}, {"op": "set_edge", "src": "a", "dst": "b", "cost": 2},
                     {"op": "remove_edge", "src": "a", "dst": "b"}, {"op": "set_edge", "src": "b", "dst": "a", "cost": 0}],
                    expected_version=1)
        self.assertEqual(graph.version, 2)
        self.assertEqual(graph.shortest("b", "a"), (0, ("b", "a")))

    def _tie_graph(self, **kwargs):
        graph = Graph(**kwargs)
        for node in ("s", "a", "b", "t", "z"): graph.add_node(node)
        for src, dst, cost in [("s", "a", 0), ("a", "b", 1), ("b", "t", 1), ("s", "z", 1), ("z", "t", 1),
                               ("b", "a", 0)]:
            graph.set_edge(src, dst, cost)
        return graph

    def test_a3_lexical_default_and_explicit_fewest_hops(self):
        self.assertEqual(Graph().tie_policy, "lexical")
        self.assertEqual(self._tie_graph().shortest("s", "t"), (2, ("s", "a", "b", "t")))
        fewest = self._tie_graph(tie_policy="fewest_hops")
        self.assertEqual(fewest.shortest("s", "t"), (2, ("s", "z", "t")))
        self.assertEqual(self._tie_graph().shortest("s", "t", max_hops=2), (2, ("s", "z", "t")))
        self.assertEqual(self._tie_graph().shortest("s", "t", blocked=["a"]), (2, ("s", "z", "t")))
        for policy in ("other", None, 1):
            with self.assertRaises(ValueError): Graph(tie_policy=policy)

    def test_a3_snapshot_freezes_policy(self):
        lexical = self._tie_graph().snapshot()
        fewest = self._tie_graph(tie_policy="fewest_hops").snapshot()
        self.assertEqual((lexical.tie_policy, fewest.tie_policy), ("lexical", "fewest_hops"))
        self.assertEqual(lexical.shortest("s", "t")[1], ("s", "a", "b", "t"))
        self.assertEqual(fewest.shortest("s", "t")[1], ("s", "z", "t"))
        with self.assertRaises(AttributeError): fewest.tie_policy = "lexical"

    def test_randomized_against_brute_force(self):
        rng = random.Random(7)
        names = ["a", "b", "c", "d", "e"]

        def routes(edges, path, dst, blocked, limit):
            if path[-1] == dst:
                yield path
                return
            if len(path) - 1 >= limit:
                return
            for nxt in edges.get(path[-1], {}):
                if nxt not in path and nxt not in blocked:
                    yield from routes(edges, path + (nxt,), dst, blocked, limit)

        for _ in range(300):
            policy = rng.choice(["lexical", "fewest_hops"])
            graph = Graph(tie_policy=policy)
            for name in names: graph.add_node(name)
            edges = {}
            for src in names:
                for dst in names:
                    if src != dst and rng.random() < 0.45:
                        cost = rng.randint(0, 3)
                        graph.set_edge(src, dst, cost)
                        edges.setdefault(src, {})[dst] = cost
            src, dst = rng.choice(names), rng.choice(names)
            blocked = {name for name in names if name not in (src, dst) and rng.random() < 0.2}
            hops = rng.choice([None, 0, 1, 2, 3])
            limit = len(names) if hops is None else hops
            cost = lambda p: sum(edges[a][b] for a, b in zip(p, p[1:]))
            key = (lambda p: (cost(p), p)) if policy == "lexical" else (lambda p: (cost(p), len(p), p))
            candidates = sorted(routes(edges, (src,), dst, blocked, limit), key=key)
            if candidates:
                self.assertEqual(graph.shortest(src, dst, blocked=blocked, max_hops=hops),
                                 (cost(candidates[0]), candidates[0]))
            else:
                with self.assertRaises(NoPathError):
                    graph.shortest(src, dst, blocked=blocked, max_hops=hops)


if __name__ == "__main__":
    unittest.main()

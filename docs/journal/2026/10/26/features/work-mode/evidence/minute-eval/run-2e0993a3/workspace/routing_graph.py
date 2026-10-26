"""Directed, versioned graph with deterministic simple shortest paths (REQ-A0..A3)."""

import heapq


class NoPathError(Exception):
    pass


class ConflictError(Exception):
    pass


TIE_POLICIES = ("lexical", "fewest_hops")


class Graph:
    def __init__(self, tie_policy="lexical"):
        if not isinstance(tie_policy, str) or tie_policy not in TIE_POLICIES:
            raise ValueError("tie_policy must be 'lexical' or 'fewest_hops'")
        self._edges = {}
        self.version = 0
        self.tie_policy = tie_policy

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not name:
            raise ValueError("node names must be nonempty strings")

    def _endpoint(self, name):
        self._name(name)
        if name not in self._edges:
            raise ValueError("unknown node: " + name)

    def add_node(self, name):
        self._name(name)
        if name not in self._edges:
            self._edges[name] = {}
            self.version += 1

    def set_edge(self, src, dst, cost):
        self._endpoint(src)
        self._endpoint(dst)
        if type(cost) is not int or cost < 0:
            raise ValueError("cost must be a nonnegative integer")
        if self._edges[src].get(dst) != cost:
            self._edges[src][dst] = cost
            self.version += 1

    def remove_edge(self, src, dst):
        self._endpoint(src)
        self._endpoint(dst)
        if dst not in self._edges[src]:
            return False
        del self._edges[src][dst]
        self.version += 1
        return True

    def remove_node(self, name):
        self._name(name)
        if name not in self._edges:
            return False
        del self._edges[name]
        for edges in self._edges.values():
            edges.pop(name, None)
        self.version += 1
        return True

    def shortest(self, src, dst, *, blocked=(), max_hops=None):
        self._endpoint(src)
        self._endpoint(dst)
        if max_hops is not None and (type(max_hops) is not int or max_hops < 0):
            raise ValueError("max_hops must be a nonnegative integer or None")
        try:
            blocked = tuple(blocked)
        except TypeError as error:
            raise ValueError("blocked must be iterable") from error
        for name in blocked:
            self._endpoint(name)
        blocked = set(blocked)
        if src in blocked or dst in blocked:
            raise NoPathError((src, dst))
        limit = len(self._edges) - 1 if max_hops is None else max_hops
        # Both orderings are monotone under path extension with nonnegative costs,
        # so the first popped route to dst is optimal among bounded simple routes.
        lexical = self.tie_policy == "lexical"
        queue = [(0, (src,), 0) if lexical else (0, 0, (src,))]
        while queue:
            if lexical:
                cost, path, hops = heapq.heappop(queue)
            else:
                cost, hops, path = heapq.heappop(queue)
            node = path[-1]
            if node == dst:
                return cost, path
            if hops >= limit:
                continue
            for neighbor, weight in self._edges[node].items():
                if neighbor in path or neighbor in blocked:
                    continue
                route = path + (neighbor,)
                heapq.heappush(queue, (cost + weight, route, hops + 1) if lexical
                               else (cost + weight, hops + 1, route))
        raise NoPathError((src, dst))

    def _copy(self):
        graph = Graph(self.tie_policy)
        graph._edges = {node: edges.copy() for node, edges in self._edges.items()}
        graph.version = self.version
        return graph

    def snapshot(self):
        return GraphSnapshot(self._copy())

    def apply(self, changes, *, expected_version=None):
        if expected_version is not None and (
                type(expected_version) is not int or expected_version != self.version):
            raise ConflictError("expected version %r, found %r" % (expected_version, self.version))
        try:
            changes = list(changes)
        except TypeError as error:
            raise ValueError("changes must be iterable") from error
        candidate = self._copy()
        operations = {
            "add_node": ("name",),
            "set_edge": ("src", "dst", "cost"),
            "remove_edge": ("src", "dst"),
            "remove_node": ("name",),
        }
        for change in changes:
            if not isinstance(change, dict) or change.get("op") not in operations:
                raise ValueError("unknown change operation")
            fields = operations[change["op"]]
            if set(change) != {"op", *fields}:
                raise ValueError("invalid fields for " + change["op"])
            getattr(candidate, change["op"])(*(change[field] for field in fields))
        if candidate._edges != self._edges:
            self._edges = candidate._edges
            self.version += 1


class GraphSnapshot:
    __slots__ = ("_graph",)

    def __init__(self, graph):
        object.__setattr__(self, "_graph", graph)

    def __setattr__(self, name, value):
        raise AttributeError("graph snapshots are read-only")

    @property
    def version(self):
        return self._graph.version

    @property
    def tie_policy(self):
        return self._graph.tie_policy

    def shortest(self, src, dst, *, blocked=(), max_hops=None):
        return self._graph.shortest(src, dst, blocked=blocked, max_hops=max_hops)

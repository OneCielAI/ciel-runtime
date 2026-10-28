"""Directed weighted routing graph (REQ-A0 .. REQ-A3).

Shortest-path ordering always minimizes total cost first.  Equal-cost ties
follow the graph's ``tie_policy`` (REQ-A3, supersedes the A0 tie order):

* ``"lexical"`` (default): lexically smallest full path tuple, then hops.
* ``"fewest_hops"``: fewest edges, then lexically smallest path (A0 order).

The lexical policy searches simple path prefixes without collapsing different
visited-node sets: with zero-cost cycles, the best prefix to a node need not
be extendible to the best simple path. The fewest_hops policy can use node
labels since removing a cycle always improves its key.
"""

import heapq
from types import MappingProxyType

__all__ = ["Graph", "GraphSnapshot", "NoPathError", "ConflictError",
           "LEXICAL", "FEWEST_HOPS"]

LEXICAL = "lexical"
FEWEST_HOPS = "fewest_hops"
_TIE_POLICIES = (LEXICAL, FEWEST_HOPS)


class NoPathError(Exception):
    """Raised when no path exists between two known nodes."""


class ConflictError(Exception):
    """Raised when a requested mutation conflicts with graph state."""


def _check_name(name):
    if not isinstance(name, str) or not name:
        raise ValueError("node name must be a nonempty string: %r" % (name,))


def _check_tie_policy(policy):
    if not isinstance(policy, str) or policy not in _TIE_POLICIES:
        raise ValueError("tie_policy must be one of %r: %r" % (_TIE_POLICIES, policy))


def _check_cost(cost):
    if isinstance(cost, bool) or not isinstance(cost, int) or cost < 0:
        raise ValueError("cost must be a nonnegative int (bool invalid): %r" % (cost,))


def _require_in(adj, name):
    _check_name(name)
    if name not in adj:
        raise ValueError("unknown node: %r" % (name,))


# Mutation primitives on a raw adjacency dict; each returns True on change.

def _add_node(adj, name):
    _check_name(name)
    if name in adj:
        return False
    adj[name] = {}
    return True


def _set_edge(adj, src, dst, cost):
    _require_in(adj, src)
    _require_in(adj, dst)
    _check_cost(cost)
    edges = adj[src]
    if dst in edges and edges[dst] == cost:
        return False
    edges[dst] = cost
    return True


def _remove_edge(adj, src, dst):
    _require_in(adj, src)
    _require_in(adj, dst)
    return adj[src].pop(dst, None) is not None


def _remove_node(adj, name):
    _check_name(name)
    if name not in adj:
        return False
    del adj[name]
    for edges in adj.values():
        edges.pop(name, None)
    return True


# REQ-A2 change dicts: {"op": <name>, <fields>...}; "kind"/"type" are
# accepted as aliases for the "op" key.
_CHANGE_OPS = {
    "add_node": (("name",), _add_node),
    "set_edge": (("src", "dst", "cost"), _set_edge),
    "remove_edge": (("src", "dst"), _remove_edge),
    "remove_node": (("name",), _remove_node),
}
_OP_KEYS = ("op", "kind", "type")


def _apply_change(adj, change):
    if not isinstance(change, dict):
        raise ValueError("change must be a dict: %r" % (change,))
    op_keys = [k for k in _OP_KEYS if k in change]
    if len(op_keys) != 1:
        raise ValueError("change needs exactly one 'op' key: %r" % (change,))
    op = change[op_keys[0]]
    if not isinstance(op, str) or op not in _CHANGE_OPS:
        raise ValueError("unknown change op: %r" % (op,))
    fields, func = _CHANGE_OPS[op]
    if set(change) != set(fields) | {op_keys[0]}:
        raise ValueError("change %r needs exactly fields %r: %r" % (op, fields, change))
    return func(adj, *(change[f] for f in fields))


class _RouteView:
    """Shared read-only query logic over ``self._adj``."""

    __slots__ = ()

    def _require(self, name):
        _require_in(self._adj, name)

    def shortest(self, src, dst, *, blocked=(), max_hops=None):
        """Return (cost, path_tuple) of the best simple path src->dst.

        REQ-A1: nodes in `blocked` are excluded entirely; `max_hops` bounds
        the number of edges and is applied while searching, so the result is
        the best route among those within the bound.
        """
        self._require(src)
        self._require(dst)
        if isinstance(blocked, str):
            raise ValueError("blocked must be an iterable of node names, not a str")
        try:
            blocked = frozenset(blocked)
        except TypeError:
            raise ValueError("blocked must be an iterable of node names") from None
        for name in blocked:
            self._require(name)
        if max_hops is not None and (
                isinstance(max_hops, bool) or not isinstance(max_hops, int) or max_hops < 0):
            raise ValueError("max_hops must be None or a nonnegative int: %r" % (max_hops,))
        if src in blocked or dst in blocked:
            raise NoPathError("endpoint blocked: %r -> %r" % (src, dst))
        if src == dst:
            return (0, (src,))
        if self._tie_policy == LEXICAL:
            bound = len(self._adj) - 1 if max_hops is None else min(max_hops, len(self._adj) - 1)
            return self._lexical(src, dst, blocked, bound)
        if max_hops is None or max_hops >= len(self._adj) - 1:
            return self._dijkstra(src, dst, blocked, False)
        return self._bounded_fewest_hops(src, dst, blocked, max_hops)

    @property
    def tie_policy(self):
        return self._tie_policy

    def _dijkstra(self, src, dst, blocked, lexical):
        # Heap key: (cost, path) for lexical, (cost, hops, path) otherwise.
        heap = [(0, (src,))] if lexical else [(0, 0, (src,))]
        settled = set(blocked)
        while heap:
            entry = heapq.heappop(heap)
            cost, path = entry[0], entry[-1]
            node = path[-1]
            if node in settled:
                continue
            settled.add(node)
            if node == dst:
                return (cost, path)
            for nxt, w in self._adj[node].items():
                if nxt not in settled:
                    if lexical:
                        heapq.heappush(heap, (cost + w, path + (nxt,)))
                    else:
                        heapq.heappush(heap, (cost + w, len(path), path + (nxt,)))
        raise NoPathError("no path from %r to %r" % (src, dst))

    def _lexical(self, src, dst, blocked, max_hops):
        # Each extension strictly increases (cost, path). Keep distinct simple
        # prefixes: collapsing by endpoint loses visited-set/hop alternatives.
        heap = [(0, (src,))]
        while heap:
            cost, path = heapq.heappop(heap)
            if path[-1] == dst:
                return cost, path
            if len(path) - 1 >= max_hops:
                continue
            for nxt, weight in self._adj[path[-1]].items():
                if nxt not in blocked and nxt not in path:
                    heapq.heappush(heap, (cost + weight, path + (nxt,)))
        raise NoPathError("no path from %r to %r within %d hops" % (src, dst, max_hops))

    def _bounded_fewest_hops(self, src, dst, blocked, max_hops):
        # Layer k holds, per node, the best (cost, path) walk of exactly k
        # edges.  Any walk with a repeated node is beaten by its cycle-free
        # shortening (cost <=, strictly fewer hops, still within the bound),
        # so the overall minimum over layers is always a simple path.
        best = None
        layer = {src: (0, (src,))}
        for hops in range(1, max_hops + 1):
            nxt_layer = {}
            for node, (cost, path) in layer.items():
                for nxt, w in self._adj[node].items():
                    if nxt in blocked:
                        continue
                    cand = (cost + w, path + (nxt,))
                    cur = nxt_layer.get(nxt)
                    if cur is None or cand < cur:
                        nxt_layer[nxt] = cand
            if dst in nxt_layer:
                cost, path = nxt_layer[dst]
                key = (cost, hops, path)
                if best is None or key < best:
                    best = key
            if not nxt_layer:
                break
            layer = nxt_layer
        if best is None:
            raise NoPathError("no path from %r to %r within %d hops" % (src, dst, max_hops))
        return (best[0], best[2])


class GraphSnapshot(_RouteView):
    """REQ-A2: immutable point-in-time copy of a Graph."""

    __slots__ = ("_adj", "_version", "_tie_policy")

    def __init__(self, adj, version, tie_policy):
        object.__setattr__(self, "_adj", MappingProxyType(
            {n: MappingProxyType(dict(e)) for n, e in adj.items()}))
        object.__setattr__(self, "_version", version)
        object.__setattr__(self, "_tie_policy", tie_policy)

    @property
    def version(self):
        return self._version

    def __setattr__(self, name, value):
        raise AttributeError("GraphSnapshot is read-only")

    def __delattr__(self, name):
        raise AttributeError("GraphSnapshot is read-only")


class Graph(_RouteView):
    __slots__ = ("_adj", "version", "_tie_policy")

    def __init__(self, tie_policy=LEXICAL):
        _check_tie_policy(tie_policy)
        self._tie_policy = tie_policy
        self._adj = {}  # src -> {dst: cost}
        self.version = 0

    def _bump(self, changed):
        if changed:
            self.version += 1
        return changed

    def add_node(self, name):
        """Add a node; returns True if added, False if it already existed."""
        return self._bump(_add_node(self._adj, name))

    def set_edge(self, src, dst, cost):
        """Create or overwrite the directed edge src->dst."""
        self._bump(_set_edge(self._adj, src, dst, cost))

    def remove_edge(self, src, dst):
        return self._bump(_remove_edge(self._adj, src, dst))

    def remove_node(self, name):
        """Remove a node and its incident edges (one version step)."""
        return self._bump(_remove_node(self._adj, name))

    def snapshot(self):
        """REQ-A2: independent read-only copy supporting shortest()."""
        return GraphSnapshot(self._adj, self.version, self._tie_policy)

    def apply(self, changes, *, expected_version=None):
        """REQ-A2: apply change dicts atomically; returns the new version.

        A version mismatch raises ConflictError before anything is applied.
        Any invalid change raises ValueError and leaves the graph untouched.
        The version advances once if the final state differs, else not at all.
        """
        if expected_version is not None:
            if isinstance(expected_version, bool) or not isinstance(expected_version, int):
                raise ValueError("expected_version must be None or an int: %r"
                                 % (expected_version,))
            if expected_version != self.version:
                raise ConflictError("expected version %d, graph is at %d"
                                    % (expected_version, self.version))
        if isinstance(changes, (str, bytes, dict)):
            raise ValueError("changes must be an iterable of change dicts")
        try:
            changes = list(changes)
        except TypeError:
            raise ValueError("changes must be an iterable of change dicts") from None
        staged = {n: dict(e) for n, e in self._adj.items()}
        for change in changes:
            _apply_change(staged, change)
        if staged != self._adj:
            self._adj = staged
            self.version += 1
        return self.version

"""Incremental JSONL inventory event stream (REQ-C0, REQ-C1).

Text arrives as arbitrary fragments (a split may fall anywhere, including
inside JSON strings or between CR and LF).  Each complete nonblank line is one
event object:

    {"id": ..., "kind": "stock", "sku": ..., "qty": ...}
    {"id": ..., "kind": "move", "src": ..., "dst": ..., "qty": ...}
    {"id": ..., "kind": "batch", "ops": [<stock/move op without id>, ...]}

Failure handling: a failing line is consumed and leaves balances and the
id registry unchanged.  The exception is raised immediately; IDs applied
earlier in the same call are available as ``exc.applied`` and any complete
lines after the failing one stay buffered and are processed by the next
``feed``/``finish`` call.

REQ-C3: every line (complete or still unfinished) is limited to MAX_LINE
Unicode code points, not counting its line terminator (LF or CRLF).  A feed
that would exceed the bound raises InventoryError and has no effect at all:
the buffer, balances and id registry are exactly as before that call.
"""

import copy
import json

__all__ = ["InventoryStream", "InventoryCheckpoint", "InventoryError",
           "ConflictError", "MAX_LINE"]

MAX_LINE = 4096


class InventoryError(Exception):
    """Invalid JSON, invalid event fields/kind, or insufficient stock."""

    applied = ()


class ConflictError(InventoryError):
    """An already-accepted event id was reused with different content."""


_OP_FIELDS = {
    "stock": frozenset(("kind", "sku", "qty")),
    "move": frozenset(("kind", "src", "dst", "qty")),
}


def _no_dup_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise InventoryError("duplicate JSON key: %r" % (key,))
        obj[key] = value
    return obj


def _reject_constant(name):
    raise InventoryError("non-standard JSON constant: %s" % name)


def _parse(line):
    try:
        obj = json.loads(line, object_pairs_hook=_no_dup_keys,
                         parse_constant=_reject_constant)
    except InventoryError:
        raise
    except (ValueError, RecursionError) as exc:
        raise InventoryError("invalid JSON: %s" % exc) from None
    if not isinstance(obj, dict):
        raise InventoryError("event must be a JSON object")
    return obj


def _name(obj, key):
    value = obj[key]
    if not isinstance(value, str) or not value:
        raise InventoryError("%s must be a nonempty string: %r" % (key, value))
    return value


def _qty(obj):
    value = obj["qty"]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InventoryError("qty must be a positive int: %r" % (value,))
    return value


def _op(obj):
    """Validate a stock/move mapping (without id) -> normalized op tuple."""
    kind = obj.get("kind")
    if not isinstance(kind, str) or kind not in _OP_FIELDS:
        raise InventoryError("invalid op kind: %r" % (kind,))
    if set(obj) != _OP_FIELDS[kind]:
        raise InventoryError("invalid fields for %s: %r" % (kind, sorted(obj)))
    if kind == "stock":
        return ("stock", _name(obj, "sku"), _qty(obj))
    src, dst = _name(obj, "src"), _name(obj, "dst")
    if src == dst:
        raise InventoryError("move src and dst must differ: %r" % (src,))
    return ("move", src, dst, _qty(obj))


def _event(obj):
    """Validate a top-level event -> (id, [op tuples])."""
    if "id" not in obj:
        raise InventoryError("event missing id")
    event_id = _name(obj, "id")
    body = {k: v for k, v in obj.items() if k != "id"}
    if body.get("kind") == "batch":
        if set(body) != {"kind", "ops"}:
            raise InventoryError("invalid fields for batch: %r" % (sorted(obj),))
        ops = body["ops"]
        if not isinstance(ops, list):
            raise InventoryError("batch ops must be a list")
        result = []
        for op in ops:
            if not isinstance(op, dict):
                raise InventoryError("batch op must be an object: %r" % (op,))
            if "id" in op:
                raise InventoryError("batch op must not carry an id")
            if op.get("kind") == "batch":
                raise InventoryError("nested batches are forbidden")
            result.append(_op(op))
        return event_id, result
    return event_id, [_op(body)]


def _line_len(segment):
    return len(segment) - 1 if segment.endswith("\r") else len(segment)


class InventoryCheckpoint:
    """REQ-C3: opaque, immutable saved state of one InventoryStream."""

    __slots__ = ("_owner", "_state")

    def __init__(self, owner, state):
        object.__setattr__(self, "_owner", owner)
        object.__setattr__(self, "_state", state)

    def __setattr__(self, name, value):
        raise AttributeError("InventoryCheckpoint is immutable")

    def __delattr__(self, name):
        raise AttributeError("InventoryCheckpoint is immutable")

    def __repr__(self):
        return "<InventoryCheckpoint>"


class InventoryStream:
    def __init__(self):
        self._buf = ""
        self._balances = {}
        self._seen = {}  # id -> parsed JSON object of the accepted event
        self._deltas = {}  # REQ-C2: live (not undone) id -> net {sku: signed qty}
        self._token = object()  # identifies checkpoints taken from this stream

    def feed(self, fragment):
        if not isinstance(fragment, str):
            raise TypeError("fragment must be a str")
        text = self._buf + fragment
        # REQ-C3: reject before applying anything, so an overlong line rolls
        # back every effect of this call, including earlier complete events.
        for segment in text.split("\n"):
            if _line_len(segment) > MAX_LINE:
                raise InventoryError("line exceeds %d code points" % MAX_LINE)
        self._buf = text
        return self._drain(final=False)

    def checkpoint(self):
        """REQ-C3: opaque snapshot of balances, ids, undo state and buffer."""
        state = (self._buf, dict(self._balances), copy.deepcopy(self._seen),
                 copy.deepcopy(self._deltas))
        return InventoryCheckpoint(self._token, state)

    def restore(self, cp):
        """REQ-C3: return to `cp` (taken from this stream); reusable."""
        if not isinstance(cp, InventoryCheckpoint) or cp._owner is not self._token:
            raise InventoryError("checkpoint does not belong to this stream")
        buf, balances, seen, deltas = cp._state
        self._buf = buf
        self._balances = dict(balances)
        self._seen = copy.deepcopy(seen)
        self._deltas = copy.deepcopy(deltas)

    def finish(self):
        return self._drain(final=True)

    def balance(self, sku):
        return self._balances.get(sku, 0)

    def snapshot(self):
        return dict(self._balances)

    def _drain(self, final):
        applied = []
        while True:
            idx = self._buf.find("\n")
            if idx >= 0:
                line, self._buf = self._buf[:idx], self._buf[idx + 1:]
            elif final and self._buf:
                line, self._buf = self._buf, ""
            else:
                return applied
            if not line.strip(" \t\r"):
                continue
            try:
                event_id = self._apply_line(line)
            except InventoryError as exc:
                exc.applied = applied
                raise
            if event_id is not None:
                applied.append(event_id)

    def _apply_line(self, line):
        obj = _parse(line)
        event_id, ops = _event(obj)
        if event_id in self._seen:
            if self._seen[event_id] == obj:
                return None
            raise ConflictError("event id %r reused with different content" % (event_id,))
        staged = dict(self._balances)
        delta = {}
        for op in ops:
            if op[0] == "stock":
                _, sku, qty = op
                staged[sku] = staged.get(sku, 0) + qty
                delta[sku] = delta.get(sku, 0) + qty
            else:
                _, src, dst, qty = op
                have = staged.get(src, 0)
                if have < qty:
                    raise InventoryError(
                        "insufficient stock for %r: have %d, need %d" % (src, have, qty))
                staged[src] = have - qty
                staged[dst] = staged.get(dst, 0) + qty
                delta[src] = delta.get(src, 0) - qty
                delta[dst] = delta.get(dst, 0) + qty
        self._balances = staged
        self._seen[event_id] = obj
        self._deltas[event_id] = delta
        return event_id

    def undo(self, event_id):
        """REQ-C2: reverse a successful event (stock, move or batch).

        Reversal subtracts the event's NET signed per-SKU delta in one step;
        if any balance would go negative nothing changes and the undo may be
        retried later.  The id stays registered as a tombstone, so replaying
        the identical line is ignored and different content is a conflict.
        """
        if not isinstance(event_id, str) or event_id not in self._deltas:
            if isinstance(event_id, str) and event_id in self._seen:
                raise InventoryError("event %r already undone" % (event_id,))
            raise InventoryError("unknown event id: %r" % (event_id,))
        delta = self._deltas[event_id]
        staged = dict(self._balances)
        for sku, d in delta.items():
            staged[sku] = staged.get(sku, 0) - d
            if staged[sku] < 0:
                raise InventoryError(
                    "undo of %r would make %r negative (%d)" % (event_id, sku, staged[sku]))
        self._balances = staged
        del self._deltas[event_id]

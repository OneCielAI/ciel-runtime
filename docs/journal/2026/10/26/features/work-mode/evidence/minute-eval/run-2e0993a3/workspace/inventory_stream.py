"""Incremental JSONL inventory application (REQ-C0..C3)."""

import json

MAX_LINE_LENGTH = 4096


class InventoryError(Exception):
    pass


class ConflictError(Exception):
    pass


class Checkpoint:
    """Opaque, immutable stream state; only its originating stream restores it."""

    __slots__ = ("_owner", "_state")

    def __init__(self, owner, state):
        object.__setattr__(self, "_owner", owner)
        object.__setattr__(self, "_state", state)

    def __setattr__(self, name, value):
        raise AttributeError("checkpoints are immutable")

    def __delattr__(self, name):
        raise AttributeError("checkpoints are immutable")

    def __repr__(self):
        return "<Checkpoint>"


class InventoryStream:
    def __init__(self):
        self._token = object()
        self._pending = ""
        self._balances = {}
        self._accepted = {}
        self._deltas = {}
        self._undone = set()

    @staticmethod
    def _name(value):
        if not isinstance(value, str) or not value:
            raise InventoryError("expected nonempty string")

    @staticmethod
    def _op(op, balances):
        if not isinstance(op, dict):
            raise InventoryError("operation must be an object")
        kind = op.get("kind")
        qty = op.get("qty")
        if type(qty) is not int or qty <= 0:
            raise InventoryError("qty must be a positive integer")
        if kind == "stock":
            sku = op.get("sku")
            InventoryStream._name(sku)
            balances[sku] = balances.get(sku, 0) + qty
        elif kind == "move":
            src, dst = op.get("src"), op.get("dst")
            InventoryStream._name(src)
            InventoryStream._name(dst)
            if src == dst or balances.get(src, 0) < qty:
                raise InventoryError("invalid or insufficient-stock move")
            balances[src] -= qty
            balances[dst] = balances.get(dst, 0) + qty
        else:
            raise InventoryError("unknown kind")

    def _line(self, line):
        if not line.strip():
            return []
        try:
            event = json.loads(line, parse_constant=self._invalid_constant)
        except (ValueError, TypeError, RecursionError) as error:
            raise InventoryError("invalid JSON") from error
        if not isinstance(event, dict):
            raise InventoryError("event must be an object")
        id = event.get("id")
        self._name(id)
        canonical = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if id in self._accepted:
            if self._accepted[id] != canonical:
                raise ConflictError(id)
            return []
        candidate = self._balances.copy()
        if event.get("kind") == "batch":
            ops = event.get("ops")
            if not isinstance(ops, list):
                raise InventoryError("ops must be a list")
            for op in ops:
                if not isinstance(op, dict) or "id" in op:
                    raise InventoryError("batch operations cannot have ids")
                self._op(op, candidate)
        else:
            self._op(event, candidate)
        self._deltas[id] = {sku: value - self._balances.get(sku, 0)
                            for sku, value in candidate.items()}
        self._balances = candidate
        self._accepted[id] = canonical
        return [id]

    @staticmethod
    def _invalid_constant(value):
        raise ValueError("invalid JSON constant: " + value)

    def feed(self, fragment):
        if not isinstance(fragment, str):
            raise InventoryError("fragment must be a string")
        self._check_lengths(self._pending + fragment)
        self._pending += fragment
        applied = []
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            applied.extend(self._line(line))
        return applied

    def finish(self):
        applied = self.feed("")
        line, self._pending = self._pending, ""
        applied.extend(self._line(line))
        return applied

    @staticmethod
    def _check_lengths(text):
        # Length counts code points of line content; the LF terminator and a CR
        # immediately before it (or pending at the buffer end) are not content.
        for line in text.split("\n"):
            if len(line[:-1] if line.endswith("\r") else line) > MAX_LINE_LENGTH:
                raise InventoryError("line exceeds %d code points" % MAX_LINE_LENGTH)

    def checkpoint(self):
        return Checkpoint(self._token, (
            self._pending,
            tuple(self._balances.items()),
            tuple(self._accepted.items()),
            tuple((id, tuple(delta.items())) for id, delta in self._deltas.items()),
            frozenset(self._undone),
        ))

    def restore(self, checkpoint):
        if not isinstance(checkpoint, Checkpoint) or checkpoint._owner is not self._token:
            raise InventoryError("checkpoint does not belong to this stream")
        pending, balances, accepted, deltas, undone = checkpoint._state
        self._pending = pending
        self._balances = dict(balances)
        self._accepted = dict(accepted)
        self._deltas = {id: dict(delta) for id, delta in deltas}
        self._undone = set(undone)

    def undo(self, event_id):
        self._name(event_id)
        if event_id not in self._accepted or event_id in self._undone:
            raise InventoryError("unknown or already undone event")
        candidate = self._balances.copy()
        for sku, delta in self._deltas[event_id].items():
            value = candidate.get(sku, 0) - delta
            if value < 0:
                raise InventoryError("undo would make stock negative")
            candidate[sku] = value
        self._balances = candidate
        self._undone.add(event_id)

    def balance(self, sku):
        self._name(sku)
        return self._balances.get(sku, 0)

    def snapshot(self):
        return self._balances.copy()

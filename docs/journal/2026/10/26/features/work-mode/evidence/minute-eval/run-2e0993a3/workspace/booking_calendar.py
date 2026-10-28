"""Cumulative half-open interval bookings (REQ-B0..B3)."""


class CapacityError(Exception):
    pass


class ConflictError(Exception):
    pass


class Checkpoint:
    """Opaque, immutable calendar state; only its originating Calendar restores it."""

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


class Calendar:
    def __init__(self, capacity=1):
        self._positive(capacity)
        self.capacity = capacity
        self._reservations = {}
        self._waiting = {}
        self._token = object()
        self.version = 0

    @staticmethod
    def _positive(value):
        if type(value) is not int or value <= 0:
            raise ValueError("expected a positive integer")

    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not value:
            raise ValueError("id must be a nonempty string")

    def _interval(self, start, end, units):
        if type(start) is not int or type(end) is not int or start >= end:
            raise ValueError("expected integer start < end")
        self._positive(units)

    def available(self, start, end, units=1):
        self._interval(start, end, units)
        changes = {start: units, end: -units}
        for a, b, count in self._reservations.values():
            changes[a] = changes.get(a, 0) + count
            changes[b] = changes.get(b, 0) - count
        active = 0
        for time in sorted(changes):
            active += changes[time]
            if active > self.capacity:
                return False
        return True

    def _reserve(self, id, start, end, units):
        self._id(id)
        self._interval(start, end, units)
        if id in self._reservations or id in self._waiting:
            raise ValueError("duplicate active or waiting id")
        if not self.available(start, end, units):
            raise CapacityError("capacity exceeded")
        self._reservations[id] = (start, end, units)

    def reserve(self, id, start, end, units=1):
        self._reserve(id, start, end, units)
        self.version += 1

    def _candidate(self):
        candidate = Calendar(self.capacity)
        candidate._reservations = self._reservations.copy()
        candidate._waiting = self._waiting
        return candidate

    def _commit(self, reservations):
        if reservations != self._reservations:
            self._reservations = reservations
            self.version += 1

    def reserve_many(self, requests):
        candidate = self._candidate()
        try:
            iterator = iter(requests)
        except TypeError as error:
            raise ValueError("requests must be iterable") from error
        for request in iterator:
            try:
                id, start, end, units = request
            except (TypeError, ValueError) as error:
                raise ValueError("expected (id, start, end, units)") from error
            candidate._reserve(id, start, end, units)
        self._commit(candidate._reservations)

    def replace(self, id, start, end, units=1):
        self._id(id)
        self._interval(start, end, units)
        if id not in self._reservations:
            raise ValueError("unknown id")
        candidate = self._candidate()
        del candidate._reservations[id]
        candidate._reserve(id, start, end, units)
        self._commit(candidate._reservations)

    def wait(self, id, start, end, units=1, priority=0):
        self._id(id)
        self._interval(start, end, units)
        if type(priority) is not int:
            raise ValueError("priority must be an integer")
        if id in self._reservations or id in self._waiting:
            raise ValueError("duplicate active or waiting id")
        self._waiting[id] = (start, end, units, priority)
        self.version += 1

    def waiting(self):
        order = sorted(enumerate(self._waiting.items()), key=lambda item: (-item[1][1][3], item[0]))
        return [id for _, (id, _) in order]

    def promote(self):
        promoted = []
        for id in self.waiting():
            start, end, units, _ = self._waiting[id]
            if self.available(start, end, units):
                del self._waiting[id]
                self._reservations[id] = (start, end, units)
                promoted.append(id)
        if promoted:
            self.version += 1
        return promoted

    def cancel(self, id):
        self._id(id)
        if id in self._waiting:
            del self._waiting[id]
        elif id in self._reservations:
            del self._reservations[id]
        else:
            return False
        self.version += 1
        return True

    def _state(self):
        # Waiting dict insertion order is the FIFO sequence, so keep it as an ordered tuple.
        return (self.capacity, tuple(sorted(self._reservations.items())),
                tuple(self._waiting.items()))

    def checkpoint(self):
        return Checkpoint(self._token, self._state())

    def restore(self, checkpoint):
        if not isinstance(checkpoint, Checkpoint) or checkpoint._owner is not self._token:
            raise ValueError("checkpoint does not belong to this calendar")
        if checkpoint._state == self._state():
            return
        capacity, reservations, waiting = checkpoint._state
        self.capacity = capacity
        self._reservations = dict(reservations)
        self._waiting = dict(waiting)
        self.version += 1

    def allocations(self):
        return sorted(((id, *value) for id, value in self._reservations.items()),
                      key=lambda row: (row[1], row[0]))

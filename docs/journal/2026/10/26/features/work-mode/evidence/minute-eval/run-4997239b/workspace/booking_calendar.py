"""Capacity-limited booking calendar (REQ-B0 .. REQ-B3).

Reservations occupy half-open intervals [start, end) and consume `units` of a
shared cumulative capacity.  Every failing call leaves the calendar unchanged.
"""

__all__ = ["Calendar", "CalendarCheckpoint", "CapacityError", "ConflictError"]


class CapacityError(Exception):
    """Raised when a reservation would exceed capacity at some instant."""


class ConflictError(Exception):
    """Raised when a request conflicts with calendar state."""


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _check_positive(value, what):
    if not _is_int(value) or value <= 0:
        raise ValueError("%s must be a positive int (bool invalid): %r" % (what, value))


def _check_interval(start, end):
    if not _is_int(start) or not _is_int(end):
        raise ValueError("start/end must be ints (bool invalid): %r, %r" % (start, end))
    if not start < end:
        raise ValueError("start must be < end: %r, %r" % (start, end))


class CalendarCheckpoint:
    """REQ-B3: opaque, immutable saved state of one Calendar."""

    __slots__ = ("_owner", "_state")

    def __init__(self, owner, state):
        object.__setattr__(self, "_owner", owner)
        object.__setattr__(self, "_state", state)

    def __setattr__(self, name, value):
        raise AttributeError("CalendarCheckpoint is immutable")

    def __delattr__(self, name):
        raise AttributeError("CalendarCheckpoint is immutable")

    def __repr__(self):
        return "<CalendarCheckpoint>"


def _check_id(res_id):
    if not isinstance(res_id, str) or not res_id:
        raise ValueError("id must be a nonempty string: %r" % (res_id,))


class Calendar:
    def __init__(self, capacity=1):
        _check_positive(capacity, "capacity")
        self._capacity = capacity
        self._active = {}  # id -> (start, end, units)
        self._waiting = {}  # REQ-B2: id -> (start, end, units, priority, seq)
        self._seq = 0
        self._version = 0  # REQ-B3
        self._token = object()  # identifies checkpoints taken from this calendar

    @property
    def version(self):
        """REQ-B3: +1 per state-changing call; no-ops and errors leave it."""
        return self._version

    @property
    def capacity(self):
        return self._capacity

    def _peak(self, start, end, bookings=None):
        """Maximum units in use at any instant within [start, end)."""
        if bookings is None:
            bookings = self._active
        deltas = {}
        for s, e, u in bookings.values():
            if s < end and start < e:
                lo = max(s, start)
                deltas[lo] = deltas.get(lo, 0) + u
                hi = min(e, end)
                deltas[hi] = deltas.get(hi, 0) - u
        peak = used = 0
        # Half-open: at equal times the release (negative) and the claim are
        # summed together, so back-to-back bookings never overlap.
        for t in sorted(deltas):
            used += deltas[t]
            if used > peak:
                peak = used
        return peak

    def available(self, start, end, units=1):
        _check_interval(start, end)
        _check_positive(units, "units")
        return self._peak(start, end) + units <= self._capacity

    def reserve(self, id, start, end, units=1):
        _check_id(id)
        _check_interval(start, end)
        _check_positive(units, "units")
        if id in self._active or id in self._waiting:
            raise ValueError("duplicate reservation id: %r" % (id,))
        if self._peak(start, end) + units > self._capacity:
            raise CapacityError(
                "reservation %r [%r, %r) x%r exceeds capacity %r"
                % (id, start, end, units, self._capacity))
        self._active[id] = (start, end, units)
        self._version += 1

    def reserve_many(self, requests):
        """REQ-B1: reserve every (id, start, end, units) request or none."""
        try:
            batch = list(requests)
        except TypeError:
            raise ValueError("requests must be an iterable of 4-tuples") from None
        staged = dict(self._active)
        new_ids = []
        for req in batch:
            try:
                rid, start, end, units = req
            except (TypeError, ValueError):
                raise ValueError("request must be (id, start, end, units): %r" % (req,)) from None
            _check_id(rid)
            _check_interval(start, end)
            _check_positive(units, "units")
            if rid in staged or rid in self._waiting:
                raise ValueError("duplicate reservation id: %r" % (rid,))
            staged[rid] = (start, end, units)
            new_ids.append(rid)
        # Adding bookings one at a time and checking each against everything
        # staged before it is equivalent to checking the final state: any
        # overloaded instant is caught when its last covering booking is added.
        check = dict(self._active)
        for rid in new_ids:
            start, end, units = staged[rid]
            if self._peak(start, end, check) + units > self._capacity:
                raise CapacityError(
                    "batch reservation %r [%r, %r) x%r exceeds capacity %r"
                    % (rid, start, end, units, self._capacity))
            check[rid] = staged[rid]
        if new_ids:
            self._active = staged
            self._version += 1

    def replace(self, id, start, end, units=1):
        """REQ-B1: atomically move active `id` to a new interval/units."""
        _check_id(id)
        _check_interval(start, end)
        _check_positive(units, "units")
        if id not in self._active:  # waiting ids are not replaceable
            raise ValueError("unknown active reservation id: %r" % (id,))
        others = {k: v for k, v in self._active.items() if k != id}
        if self._peak(start, end, others) + units > self._capacity:
            raise CapacityError(
                "replacement %r [%r, %r) x%r exceeds capacity %r"
                % (id, start, end, units, self._capacity))
        if self._active[id] != (start, end, units):
            self._active[id] = (start, end, units)
            self._version += 1

    def wait(self, id, start, end, units=1, priority=0):
        """REQ-B2: queue a request; it consumes no capacity until promoted."""
        _check_id(id)
        _check_interval(start, end)
        _check_positive(units, "units")
        if not _is_int(priority):
            raise ValueError("priority must be an int (bool invalid): %r" % (priority,))
        if id in self._active or id in self._waiting:
            raise ValueError("duplicate reservation id: %r" % (id,))
        self._waiting[id] = (start, end, units, priority, self._seq)
        self._seq += 1
        self._version += 1

    def waiting(self):
        """Waiting ids: higher priority first, then FIFO insertion order."""
        return sorted(self._waiting, key=lambda i: (-self._waiting[i][3], self._waiting[i][4]))

    def promote(self):
        """Reserve every waiting request that fits, scanning waiting() order.

        Non-fitting requests are skipped (they stay waiting) and the scan
        continues; each promotion is visible to the fit checks after it.
        """
        promoted = []
        for wid in self.waiting():
            start, end, units, _, _ = self._waiting[wid]
            if self._peak(start, end) + units <= self._capacity:
                del self._waiting[wid]
                self._active[wid] = (start, end, units)
                promoted.append(wid)
        if promoted:
            self._version += 1  # once per promote() call, not per member
        return promoted

    def cancel(self, id):
        """Cancel an active or waiting request; never auto-promotes."""
        _check_id(id)
        if self._active.pop(id, None) is None and self._waiting.pop(id, None) is None:
            return False
        self._version += 1
        return True

    def _state(self):
        return (self._capacity,
                tuple(sorted(self._active.items())),
                tuple(sorted(self._waiting.items(), key=lambda kv: kv[1][4])))

    def checkpoint(self):
        """REQ-B3: opaque immutable snapshot of bookings, queue and capacity."""
        return CalendarCheckpoint(self._token, (self._state(), self._seq))

    def restore(self, cp):
        """REQ-B3: atomically return to `cp` (from this calendar only).

        The version moves forward by one (never rewinds); restoring a state
        identical to the current one is a no-op.  The checkpoint is reusable.
        """
        if not isinstance(cp, CalendarCheckpoint) or cp._owner is not self._token:
            raise ValueError("checkpoint does not belong to this calendar")
        state, seq = cp._state
        if state == self._state():
            return
        capacity, active, waiting = state
        self._capacity = capacity
        self._active = dict(active)
        self._waiting = dict(waiting)
        # Restored entries keep their original sequence numbers; later waits
        # must still queue behind every entry ever issued.
        self._seq = max(self._seq, seq)
        self._version += 1

    def allocations(self):
        return sorted(((i, s, e, u) for i, (s, e, u) in self._active.items()),
                      key=lambda a: (a[1], a[0]))

"""Dependency-aware task scheduler (problem B).

Priority in this module means: a HIGHER number is scheduled first.
Ties between equal priorities are broken by original insertion order
(the order in which ``add`` was first called for each task).
There is no TTL, cache, or eviction behavior here.
"""

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"


class CycleError(Exception):
    """A dependency change would create a self-dependency or a cycle."""


class StateError(Exception):
    """An operation is not valid for the task's current state."""


class _Task:
    __slots__ = ("task_id", "deps", "priority", "seq", "state")

    def __init__(self, task_id, deps, priority, seq):
        self.task_id = task_id
        self.deps = deps
        self.priority = priority
        self.seq = seq
        self.state = PENDING


class Scheduler:
    def __init__(self):
        self._tasks = {}
        self._next_seq = 0

    # -- helpers -----------------------------------------------------------

    def _get(self, task_id):
        try:
            return self._tasks[task_id]
        except KeyError:
            raise KeyError(f"unknown task: {task_id!r}") from None

    def _normalize_deps(self, task_id, deps):
        if isinstance(deps, str):
            raise TypeError("deps must be an iterable of task ids, not a string")
        normalized = []
        seen = set()
        for dep in deps:
            if dep == task_id:
                raise CycleError(f"task {task_id!r} cannot depend on itself")
            if dep not in self._tasks:
                raise ValueError(f"unknown dependency: {dep!r}")
            if dep not in seen:
                seen.add(dep)
                normalized.append(dep)
        return tuple(normalized)

    def _reaches(self, start, target):
        """Return True if ``target`` is reachable from ``start`` via deps."""
        stack = [start]
        visited = set()
        while stack:
            node = stack.pop()
            if node == target:
                return True
            if node in visited:
                continue
            visited.add(node)
            stack.extend(self._tasks[node].deps)
        return False

    def _is_ready(self, task):
        return task.state == PENDING and all(
            self._tasks[dep].state == DONE for dep in task.deps
        )

    # -- public API --------------------------------------------------------

    def add(self, task_id, deps=(), priority=0):
        if task_id in self._tasks:
            raise ValueError(f"duplicate task: {task_id!r}")
        normalized = self._normalize_deps(task_id, deps)
        self._tasks[task_id] = _Task(task_id, normalized, priority, self._next_seq)
        self._next_seq += 1

    def ready(self):
        ready = [t for t in self._tasks.values() if self._is_ready(t)]
        ready.sort(key=lambda t: (-t.priority, t.seq))
        return [t.task_id for t in ready]

    def start(self, task_id):
        task = self._get(task_id)
        if not self._is_ready(task):
            raise StateError(f"task {task_id!r} is not ready (state={task.state})")
        task.state = RUNNING

    def finish(self, task_id, success=True):
        task = self._get(task_id)
        if task.state != RUNNING:
            raise StateError(f"task {task_id!r} is not running (state={task.state})")
        task.state = DONE if success else FAILED

    def status(self, task_id):
        return self._get(task_id).state

    def replace_deps(self, task_id, deps):
        task = self._get(task_id)
        if task.state != PENDING:
            raise StateError(f"task {task_id!r} is not pending (state={task.state})")
        # Validate everything before mutating so failures leave state untouched.
        normalized = self._normalize_deps(task_id, deps)
        for dep in normalized:
            if self._reaches(dep, task_id):
                raise CycleError(f"dependency {dep!r} would create a cycle via {task_id!r}")
        task.deps = normalized

    def retry(self, task_id):
        task = self._get(task_id)
        if task.state != FAILED:
            raise StateError(f"task {task_id!r} is not failed (state={task.state})")
        task.state = PENDING

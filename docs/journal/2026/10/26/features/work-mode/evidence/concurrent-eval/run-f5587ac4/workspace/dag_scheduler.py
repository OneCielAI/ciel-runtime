"""Dependency scheduler: higher priority first, insertion-order ties."""

from dataclasses import dataclass


class CycleError(ValueError):
    """A dependency change would introduce a cycle."""


class StateError(RuntimeError):
    """The operation is not valid in the task's current state."""


@dataclass
class _Task:
    deps: frozenset
    priority: object
    state: str = 'pending'


class Scheduler:
    def __init__(self):
        self._tasks = {}

    def _task(self, task_id):
        try:
            return self._tasks[task_id]
        except KeyError:
            raise ValueError(f"unknown task: {task_id!r}") from None

    def _validate_deps(self, task_id, deps):
        deps = frozenset(deps)
        if task_id in deps:
            raise CycleError("self dependency")
        for dep in deps:
            self._task(dep)
        # Any proposed dependency reaching the target closes a cycle.
        seen = set()
        stack = list(deps)
        while stack:
            current = stack.pop()
            if current == task_id:
                raise CycleError("dependency cycle")
            if current not in seen:
                seen.add(current)
                stack.extend(self._tasks[current].deps)
        return deps

    def add(self, task_id, deps=(), priority=0):
        if task_id in self._tasks:
            raise ValueError(f"duplicate task: {task_id!r}")
        deps = self._validate_deps(task_id, deps)
        self._tasks[task_id] = _Task(deps, priority)

    def _ready(self, task):
        return task.state == 'pending' and all(
            self._tasks[dep].state == 'done' for dep in task.deps
        )

    def ready(self) -> list[str]:
        # Python's stable sort preserves the dict's original insertion order.
        return sorted((key for key, task in self._tasks.items() if self._ready(task)),
                      key=lambda key: self._tasks[key].priority, reverse=True)

    def start(self, task_id):
        task = self._tasks.get(task_id)
        if task is None or not self._ready(task):
            raise StateError("only ready tasks can start")
        task.state = 'running'

    def finish(self, task_id, success=True):
        task = self._task(task_id)
        if task.state != 'running':
            raise StateError("only running tasks can finish")
        task.state = 'done' if success else 'failed'

    def status(self, task_id) -> str:
        return self._task(task_id).state

    def replace_deps(self, task_id, deps):
        task = self._task(task_id)
        if task.state != 'pending':
            raise StateError("only pending tasks can change dependencies")
        replacement = self._validate_deps(task_id, deps)
        task.deps = replacement

    def retry(self, task_id):
        task = self._task(task_id)
        if task.state != 'failed':
            raise StateError("only failed tasks can retry")
        task.state = 'pending'
